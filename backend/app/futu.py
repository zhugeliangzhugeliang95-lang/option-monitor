"""Read-only Futu OpenD snapshots. No trade context, passwords or order endpoints."""
from __future__ import annotations
import asyncio
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import math
import os
import socket
import logging
import time
import zlib
from zoneinfo import ZoneInfo

from .models import Capabilities, Config, Contract, MarketState, Quote, Snapshot, Leg, utcnow
from .instruments import FUTU_SYMBOLS
from .engine import selected_strikes
from .providers import ProviderError, regular_session, exchange_day, live_expirations, target_expiry
from . import futu_config

log = logging.getLogger('app.futu')

RIGHTS = {'LV1', 'LV2', 'LV3', 'LEVEL1', 'LEVEL2', 'LEVEL3'}
SOURCE = 'Futu OpenD / US snapshots'
WINDOW = 30.1            # seconds, internal limiter window
CHAIN_TTL = 1800         # base fresh TTL for chains and expirations
STAGGER = 600            # per-symbol TTL offset range [0, 600)
REFRESH_LEAD = 300       # refresh a fresh chain this long before its TTL ends (no stale serving in PR 1)
PREFETCH_PER_BATCH = 3   # expiration prefetches per batch


class DeadlineReached(Exception):
    """Internal: the next SDK call would not finish inside the batch budget; it was not started."""
    def __init__(self, method):
        self.method = method
        super().__init__(method)


def timeout_error(method):
    e = ProviderError('TIMEOUT')
    e.operation = method
    return e


def stagger(code):
    """Deterministic per-symbol TTL offset in [0, STAGGER)."""
    return zlib.crc32(code.encode()) % STAGGER


class WarmState:
    __slots__ = ('since', 'waiting', 'attempts', 'last_error', 'next_try', 'warned')

    def __init__(self, since, waiting):
        self.since = since            # wall clock, first time a monitor found nothing servable
        self.waiting = waiting        # monotonic, round-robin position
        self.attempts = 0
        self.last_error = None        # {'code', 'at'} (+ 'ret' if Futu returned one); never message text
        self.next_try = 0.0
        self.warned = False


def code_for(symbol):
    code = symbol if symbol.startswith('US.') else 'US.' + symbol
    if code.removeprefix('US.') not in FUTU_SYMBOLS:
        raise ProviderError('UNSUPPORTED_SYMBOL')
    return code


def number(value):
    if value is None or value == 'N/A' or value == '':
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        raise ProviderError('INVALID_PROVIDER_DATA') from None


def quote_from_row(row):
    stamp = row.get('update_time')
    try:
        at = datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc) if stamp else None
    except (ValueError, TypeError):
        at = None
    # update_time is current-price update time, NOT bid/ask event time.
    # Leave bidAt/askAt absent: snapshot data alone cannot pass strict MID quality.
    return Quote(last=number(row.get('last_price')), bid=number(row.get('bid_price')),
                 ask=number(row.get('ask_price')), lastAt=at,
                 bidSize=number(row.get('bid_vol')), askSize=number(row.get('ask_vol')),
                 source=SOURCE, feedType='REALTIME')


def vendor_error(data):
    # Inspect in memory, but never expose raw provider messages/account identifiers.
    msg = str(data).lower()
    if any(s in msg for s in ('权限', 'permission', 'right')):
        return ProviderError('PERMISSION_DENIED')
    if any(s in msg for s in ('频繁', '频率', '限频', 'too frequent', 'frequency')):
        return ProviderError('RATE_LIMITED', 30)
    if any(s in msg for s in ('登录', 'login', 'log in')):
        return ProviderError('OPEND_LOGIN_REQUIRED')
    if 'timeout' in msg or '超时' in msg:
        return ProviderError('TIMEOUT')
    return ProviderError('FUTU_REQUEST_FAILED')


class FutuProvider:
    def __init__(self, context=None, clock=None, mono=None, settings=None):
        self.context = context
        self.clock = clock or utcnow  # injectable for tests; decides today's exchange date
        self.mono = mono or time.monotonic  # injectable for tests; limiter, deadline, TTL, hang detection
        self.settings = settings or futu_config.settings()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='futu-quotes')
        self.lock = asyncio.Lock()
        self.pending = None
        self.cache = {}
        self.calls = defaultdict(deque)
        self.deadline = None
        # E.1 hang / busy escalation state (per process, status only)
        self.pending_since = 0.0
        self.pending_fn = None
        self.current_method = None
        self.busy_streak = 0
        self.unresponsive_since = None
        self.next_error_at = 0.0
        # Chain warm cache (section A): key (code, expiry)
        self.chains = {}       # key -> {'contracts', 'fetched', 'expires'}
        self.dates = {}        # code -> {'dates', 'fetched', 'expires'}
        self.warm = {}         # key -> WarmState
        self.refresh_next = {} # key -> monotonic time of next refresh-ahead attempt

    async def run(self, fn, *args):
        async with self.lock:
            if self.pending is not None and not self.pending.done():
                raise self.busy_error()
            self.busy_streak = 0
            self.current_method = None
            self.pending_fn = getattr(fn, '__name__', 'call')
            self.pending_since = self.mono()
            self.pending = asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)
            self.pending.add_done_callback(self.pending_done)
            # Worker cancellation never starts a second SDK call on top of the first.
            return await asyncio.shield(self.pending)

    def busy_error(self):
        """FUTU_BUSY, or OPEND_UNRESPONSIVE once the pending call is hung (E.1). Never rebuilds anything."""
        s = self.settings
        now = self.mono()
        pending = now - self.pending_since
        self.busy_streak += 1
        op = self.current_method or self.pending_fn or 'unknown'
        if pending <= s.hang_seconds and self.busy_streak < s.busy_alert_cycles:
            e = ProviderError('FUTU_BUSY', 10)
            e.operation = 'busy'
            e.pending_seconds = round(pending, 1)
            return e
        if self.unresponsive_since is None:
            self.unresponsive_since = self.pending_since
            self.next_error_at = self.pending_since + 5 * s.hang_seconds
            log.warning('futu call %s pending %ds (limit %ds, %d busy cycles), status OPEND_UNRESPONSIVE, '
                        'connection not rebuilt (FUTU_WATCHDOG=0)', op, pending, s.hang_seconds, self.busy_streak)
        if now >= self.next_error_at:
            log.error('futu call %s still pending after %ds, OpenD unresponsive, connection not rebuilt', op, pending)
            self.next_error_at = now + 5 * s.hang_seconds
        e = ProviderError('OPEND_UNRESPONSIVE', 10)
        e.operation = op
        e.pending_seconds = round(pending, 1)
        e.busy_cycles = self.busy_streak
        return e

    def pending_done(self, fut):
        if not fut.cancelled():
            fut.exception()  # mark retrieved; a late result is discarded
        if self.unresponsive_since is not None:
            log.info('futu call %s returned after %ds, OPEND_UNRESPONSIVE cleared',
                     self.current_method or self.pending_fn, self.mono() - self.unresponsive_since)
            self.unresponsive_since = None

    def connect(self):
        if self.context is not None:
            return
        host = os.getenv('FUTU_OPEND_HOST', '127.0.0.1')
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise ProviderError('OPEND_LOCAL_ONLY')
        try:
            port = int(os.getenv('FUTU_OPEND_PORT', '11111'))
            with socket.create_connection((host, port), timeout=1):
                pass
        except (OSError, ValueError):
            raise ProviderError('OPEND_OFFLINE') from None
        try:
            from futu import OpenQuoteContext
        except ImportError:
            raise ProviderError('FUTU_SDK_MISSING') from None
        self.context = OpenQuoteContext(host=host, port=port, is_async_connect=True, ai_type=1)
        self.context.set_sync_query_connect_timeout(3)

    def call(self, method, *args, ttl=0, **kwargs):
        self.connect()
        key = (method, repr(args), repr(kwargs))
        now = self.mono()
        cached = self.cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        # Cooperative deadline (section A): never start a call that cannot finish inside the budget.
        if self.deadline is not None and now + self.settings.room(method) > self.deadline:
            raise DeadlineReached(method)
        # Reserve margin beneath documented per-30s limits; all monitors share this provider.
        limit = futu_config.CHAIN_LIMIT if method in ('get_option_chain', 'get_market_state') else 50
        window = self.calls[method]
        while window and window[0] < now - WINDOW:
            window.popleft()
        if len(window) >= limit:
            raise ProviderError('RATE_LIMITED', WINDOW - (now - window[0]))
        window.append(now)
        self.current_method = method
        ret, data = getattr(self.context, method)(*args, **kwargs)
        if ret != 0:
            error = vendor_error(data)
            error.operation = method
            error.vendor_ret = ret if isinstance(ret, int) else None
            raise error
        now = self.mono()
        self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
        if ttl:
            self.cache[key] = (now + ttl, data)
        return data

    def chain_window(self):
        window = self.calls['get_option_chain']
        now = self.mono()
        while window and window[0] < now - WINDOW:
            window.popleft()
        return len(window)

    def permissions(self, require_options=True):
        state = self.call('get_global_state', ttl=2)
        if not state.get('qot_logined'):
            raise ProviderError('OPEND_LOGIN_REQUIRED')
        rights = self.call('get_user_info', ttl=60)
        if rights.get('us_qot_right') not in RIGHTS:
            raise ProviderError('FUTU_STOCK_PERMISSION_REQUIRED')
        if require_options and rights.get('us_option_qot_right') not in RIGHTS:
            raise ProviderError('FUTU_OPTION_PERMISSION_REQUIRED')
        return state, rights

    def inspect_connection(self):
        state = self.call('get_global_state')
        rights = self.call('get_user_info')
        stock = rights.get('us_qot_right', 'UNKNOWN')
        option = rights.get('us_option_qot_right', 'UNKNOWN')
        status = ('OPEND_LOGIN_REQUIRED' if not state.get('qot_logined') else
                  'FUTU_STOCK_PERMISSION_REQUIRED' if stock not in RIGHTS else
                  'FUTU_OPTION_PERMISSION_REQUIRED' if option not in RIGHTS else 'FUTU_RIGHTS_READY')
        return {'status': status, 'quoteLoggedIn': bool(state.get('qot_logined')),
                'stockRight': stock, 'optionRight': option, 'marketState': state.get('market_us', 'UNKNOWN'),
                'liveQuotesVerified': False, 'orderSubmissionEnabled': False}

    async def diagnose(self):
        try:
            return await self.run(self.inspect_connection)
        except ProviderError as e:
            return {'status': e.code, 'liveQuotesVerified': False, 'orderSubmissionEnabled': False}

    async def get_capabilities(self):
        await self.run(self.permissions)
        return Capabilities(provider='futu', configured=True, feedType='UNKNOWN',
                            batchLimit=400, protocol='futu-opend-snapshot-v1')

    def market_state(self, cfg):
        self.permissions()
        rows = self.call('get_market_state', [code_for(cfg.underlyingId)], ttl=2).to_dict('records')
        state = rows[0].get('market_state', 'UNKNOWN') if rows else 'UNKNOWN'
        # Option cutoffs are not supplied by chain/snapshot. No inferred 16:00 endpoint.
        return MarketState(marketOpen=state in ('MORNING', 'AFTERNOON', 'TRADING') and regular_session(cfg, datetime.now(timezone.utc)),
                           optionSessionOpen=False, reason='FUTU_' + state + '_OPTION_CUTOFF_UNVERIFIED')

    async def get_market_state(self, cfg):
        return await self.run(self.market_state, cfg)

    def snapshots(self, codes, check_permissions=True):
        if check_permissions: self.permissions()
        out = {}
        for i in range(0, len(codes), 400):
            data = self.call('get_market_snapshot', codes[i:i+400], ttl=1)
            for row in data.to_dict('records'):
                if row.get('code') in codes:
                    out[row['code']] = row
        return out

    async def get_underlying_snapshots(self, ids):
        mapping = {key: code_for(key) for key in ids}
        rows = await self.run(self.snapshots, list(set(mapping.values())))
        return {key: quote_from_row(rows[code]) for key, code in mapping.items() if code in rows}

    def expirations(self, cfg, check_permissions=True, ttl=CHAIN_TTL):
        if check_permissions: self.permissions()
        rows = self.call('get_option_expiration_date', code_for(cfg.underlyingId), ttl=ttl)
        # Futu may still list dates that already expired (cached for up to 30 min too); drop them.
        return live_expirations(sorted(set(rows['strike_time'].tolist())), exchange_day(cfg, self.clock()))

    async def list_expirations(self, cfg):
        return await self.run(self.expirations, cfg)

    def contracts(self, cfg, expiration, check_permissions=True, ttl=CHAIN_TTL):
        if check_permissions: self.permissions()
        rows = self.call('get_option_chain', code_for(cfg.underlyingId), start=expiration, end=expiration, ttl=ttl)
        result = []
        for row in rows.to_dict('records'):
            if row.get('stock_owner') != code_for(cfg.underlyingId) or row.get('strike_time') != expiration:
                raise ProviderError('CONTRACT_METADATA_MISMATCH')
            standard = row.get('option_standard_type') == 'STANDARD' and number(row.get('lot_size')) == 100
            result.append(Contract(id=row['code'], underlyingId=cfg.underlyingId, expiration=expiration,
                                   strike=Decimal(str(row['strike_price'])), right={'CALL':'C','PUT':'P'}[row['option_type']],
                                   currency='USD', exercise='AMERICAN', settlement='PHYSICAL',
                                   multiplier=row['lot_size'], deliverable='100_SHARES' if standard else 'UNKNOWN_ADJUSTED',
                                   adjusted=not standard))
        return result

    async def list_option_contracts(self, cfg, expiration):
        return await self.run(self.contracts, cfg, expiration)

    async def get_option_quotes(self, contracts):
        if not contracts:
            return {}
        rows = await self.run(self.snapshots, [c.id for c in contracts])
        return self.map_option_quotes(contracts, rows)

    def map_option_quotes(self, contracts, rows):
        out = {}
        for c in contracts:
            row = rows.get(c.id)
            if row is None:
                continue
            if (not row.get('option_valid') or row.get('stock_owner') != code_for(c.underlyingId)
                    or row.get('strike_time') != c.expiration or number(row.get('option_strike_price')) != float(c.strike)
                    or row.get('option_type') != {'C':'CALL','P':'PUT'}[c.right]
                    or number(row.get('option_contract_size')) != float(c.multiplier)):
                raise ProviderError('CONTRACT_METADATA_MISMATCH')
            out[c.id] = quote_from_row(row)
        return out

    # ---- batch path: hot first, then warm with what is left (sections A, E.2) ----
    def batch_snapshots(self, configs):
        """Quotes for monitors whose chain is cached, then warm at most WARM_PER_CYCLE chains.

        Each monitor keeps its own errors; no expiry or strike substitution. A monitor whose
        chain is not cached gets CHAIN_WARMING (or CHAIN_UNAVAILABLE after the E.2 cap) and
        its first quote in the next batch. Nothing stale is served in this path.
        """
        self.deadline = self.mono() + self.settings.hot_budget
        try:
            return self._batch(configs)
        finally:
            self.deadline = None

    def _catalog(self, code, expiry, day, now):
        if expiry < day:
            return []  # expired: never replaced -> engine status EXPIRED, no OpenD call
        chain = self.chains.get((code, expiry))
        if chain and chain['expires'] > now:
            return chain['contracts']
        dates = self.dates.get(code)
        if dates and dates['expires'] > now and expiry not in live_expirations(dates['dates'], day):
            return []  # not listed -> NO_SAME_DAY_EXPIRY / EXPIRED from the engine
        return None

    def _batch(self, configs):
        try:
            self.permissions()
        except DeadlineReached as d:
            raise timeout_error(d.method) from None
        wall, now = self.clock(), self.mono()
        catalogs, errors, cold, info = {}, {}, {}, {}
        for mid, cfg in configs.items():
            try:
                code = code_for(cfg.underlyingId)
                day = exchange_day(cfg, wall)
                expiry = target_expiry(cfg, day)
                catalog = self._catalog(code, expiry, day, now)
                if catalog is not None:
                    catalogs[mid] = catalog
                    info.setdefault((code, expiry), (cfg, day, 1 if expiry == day else 3))
                else:
                    cold.setdefault((code, expiry), []).append(mid)
                    info[(code, expiry)] = (cfg, day, 1 if expiry == day else 3)
            except ProviderError as e:
                errors[mid] = e
        for key in [k for k, st in self.warm.items() if k not in cold and k[1] < exchange_day(Config(), wall)]:
            del self.warm[key]
        for key in cold:
            if key not in self.warm:
                self.warm[key] = WarmState(wall, now)
        snapshots = {}
        ready = {mid: configs[mid] for mid in catalogs}
        if ready:
            try:
                snapshots, hot_errors = self._hot(ready, catalogs)
                errors.update(hot_errors)
            except DeadlineReached as d:
                for mid in ready:
                    errors[mid] = timeout_error(d.method)
        warmed = self._warm(cold, info, catalogs)
        for key, mids in cold.items():
            st = self.warm[key]
            waited = max(0, round((wall - st.since).total_seconds()))
            chain = {'state': 'warming', 'reason': 'retrying' if st.last_error else 'queued',
                     'waitingSeconds': waited, 'attempts': st.attempts, 'lastError': st.last_error}
            if key not in warmed and st.last_error and waited > self.settings.chain_warm_max_seconds:
                chain['state'] = 'unavailable'
                error = 'CHAIN_UNAVAILABLE'
                if not st.warned:
                    st.warned = True
                    log.warning('futu chain %s %s unavailable after %ds, %d attempts, last error %s',
                                key[0], key[1], waited, st.attempts, st.last_error['code'])
            else:
                error = 'CHAIN_WARMING'
            for mid in mids:
                e = ProviderError(error, 10)
                e.operation = 'get_option_chain'
                e.chain = dict(chain)
                errors[mid] = e
        for key in warmed:
            st = self.warm.pop(key, None)
            if st is not None and st.attempts:
                log.info('futu chain %s %s ready after %ds, %d attempts', key[0], key[1],
                         (wall - st.since).total_seconds(), st.attempts + 1)
        return snapshots, errors

    def _hot(self, ready, catalogs):
        errors = {}
        codes = list(dict.fromkeys(code_for(cfg.underlyingId) for cfg in ready.values()))
        states = self.call('get_market_state', codes, ttl=2).to_dict('records')
        states = {row['code']: row.get('market_state', 'UNKNOWN') for row in states}
        stocks = self.snapshots(codes, check_permissions=False)
        selected, anchors = {}, {}
        for mid, cfg in ready.items():
            row = stocks.get(code_for(cfg.underlyingId))
            if row is None:
                errors[mid] = ProviderError('MISSING_UNDERLYING')
                continue
            q = quote_from_row(row)
            anchors[mid] = q
            keys = {k for k, _ in selected_strikes(catalogs[mid], q.last, cfg.nBelow, cfg.nAbove)} if q.last is not None else set()
            selected[mid] = [c for c in catalogs[mid] if c.strike in keys]
        option_codes = list(dict.fromkeys(c.id for cs in selected.values() for c in cs))
        option_rows = self.snapshots(option_codes, check_permissions=False) if option_codes else {}
        snapshots = {}
        for mid, cs in selected.items():
            try:
                cfg = ready[mid]
                quotes = self.map_option_quotes(cs, option_rows)
                at = self.clock()
                state = states.get(code_for(cfg.underlyingId), 'UNKNOWN')
                snapshots[mid] = Snapshot(monitorId=mid, valuationAt=at, provider='futu',
                    underlying=anchors[mid], contracts=catalogs[mid],
                    legs=[Leg(contract=c, quote=quotes[c.id]) for c in cs if c.id in quotes],
                    marketOpen=state in ('MORNING', 'AFTERNOON', 'TRADING') and regular_session(cfg, at),
                    optionSessionOpen=False, marketReason='FUTU_' + state + '_OPTION_CUTOFF_UNVERIFIED')
            except ProviderError as e:
                errors[mid] = e
        return snapshots, errors

    def _dates(self, code, cfg, force=False):
        """Fresh expirations for a symbol, fetching them if needed (one E call)."""
        now = self.mono()
        dates = self.dates.get(code)
        if dates and dates['expires'] > now and not force:
            return dates['dates']
        values = self.expirations(cfg, check_permissions=False, ttl=0)
        done = self.mono()
        self.dates[code] = {'dates': values, 'fetched': done, 'expires': done + CHAIN_TTL}
        return values

    def _fail(self, key, error):
        st = self.warm[key]
        st.attempts += 1
        st.last_error = {'code': error.code, 'at': self.clock().isoformat()}
        if getattr(error, 'vendor_ret', None) is not None:
            st.last_error['ret'] = error.vendor_ret
        st.next_try = self.mono() + min(300, 10 * 2 ** (st.attempts - 1))
        st.waiting = self.mono()  # back of the round-robin queue

    def _warm(self, cold, info, catalogs):
        """Warm/refresh queue: class 1 cold today, class 2 refresh-ahead, class 3 other cold."""
        now = self.mono()
        warm_cold = [k for k in cold if self.warm[k].next_try <= now]
        refresh = [k for k in info if k not in cold and k in self.chains
                   and now >= self.chains[k]['expires'] - REFRESH_LEAD and self.refresh_next.get(k, 0) <= now]
        queue = (sorted([k for k in warm_cold if info[k][2] == 1], key=lambda k: (self.warm[k].waiting, k[0]))
                 + sorted(refresh, key=lambda k: (self.chains[k]['fetched'], k[0]))
                 + sorted([k for k in warm_cold if info[k][2] == 3], key=lambda k: (self.warm[k].waiting, k[0])))
        warmed, chain_calls, served = set(), 0, set()
        for key in queue:
            if chain_calls >= self.settings.warm_per_cycle or self.chain_window() >= futu_config.CHAIN_LIMIT:
                break
            code, expiry = key
            cfg, day, _ = info[key]
            served.add(key)
            try:
                dates = self._dates(code, cfg)
                if expiry not in live_expirations(dates, day):
                    if key in cold:
                        warmed.add(key)
                    continue
                chain_calls += 1
                contracts = self.contracts(cfg, expiry, check_permissions=False, ttl=0)
                done = self.mono()
                self.chains[key] = {'contracts': contracts, 'fetched': done,
                                    'expires': done + CHAIN_TTL + stagger(code)}
                self.refresh_next.pop(key, None)
                if key in cold:
                    warmed.add(key)
            except DeadlineReached:
                served.discard(key)
                break
            except ProviderError as e:
                if key in cold:
                    self._fail(key, e)
                else:
                    self.refresh_next[key] = self.mono() + 10
                    log.debug('futu chain refresh %s %s failed: %s', code, expiry, e.code)
        # Prefetch expirations for the next queued cold symbols, then refresh-ahead expirations
        # that are about to expire (own 50/window limit, at most PREFETCH_PER_BATCH per batch).
        prefetched = 0
        soon = [k for k in info if k not in cold and k not in self.chains and k[0] in self.dates
                and self.mono() >= self.dates[k[0]]['expires'] - REFRESH_LEAD]
        for key in [k for k in queue if k in cold and k not in served] + soon:
            if prefetched >= PREFETCH_PER_BATCH:
                break
            code = key[0]
            if key in cold and self.dates.get(code) and self.dates[code]['expires'] > self.mono():
                continue
            prefetched += 1
            try:
                self._dates(code, info[key][0], force=key not in cold)
            except DeadlineReached:
                break
            except ProviderError as e:
                if key in cold:
                    self._fail(key, e)
        return warmed

    async def collect_batch(self, configs):
        return await self.run(self.batch_snapshots, configs)

    async def close(self):
        """Section B step 4: await pending (bounded), close the context in the executor thread, then shut down."""
        if self.pending is not None and not self.pending.done():
            done, _ = await asyncio.wait({self.pending}, timeout=self.settings.close_wait)
            if not done:
                log.error('futu close: call %s still pending after %ss, context not closed',
                          self.current_method or self.pending_fn, self.settings.close_wait)
                self.executor.shutdown(wait=False)
                return
        if self.context is not None:
            await asyncio.get_running_loop().run_in_executor(self.executor, self.context.close)
            self.context = None
        self.executor.shutdown(wait=True)
