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
import time
from zoneinfo import ZoneInfo

from .models import Capabilities, Config, Contract, MarketState, Quote, Snapshot, Leg, utcnow
from .instruments import FUTU_SYMBOLS
from .engine import selected_strikes
from .providers import ProviderError, regular_session

RIGHTS = {'LV1', 'LV2', 'LV3', 'LEVEL1', 'LEVEL2', 'LEVEL3'}
SOURCE = 'Futu OpenD / US snapshots'


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
    def __init__(self, context=None):
        self.context = context
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='futu-quotes')
        self.lock = asyncio.Lock()
        self.pending = None
        self.cache = {}
        self.calls = defaultdict(deque)

    async def run(self, fn, *args):
        async with self.lock:
            if self.pending is not None and not self.pending.done():
                raise ProviderError('FUTU_BUSY', 10)
            self.pending = asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)
            # Worker cancellation never starts a second SDK call on top of the first.
            return await asyncio.shield(self.pending)

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
        now = time.monotonic()
        cached = self.cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        # Reserve margin beneath documented per-30s limits; all monitors share this provider.
        limit = 8 if method in ('get_option_chain', 'get_market_state') else 50
        window = self.calls[method]
        while window and window[0] < now - 30.1:
            window.popleft()
        if len(window) >= limit:
            raise ProviderError('RATE_LIMITED', 30.1 - (now - window[0]))
        window.append(now)
        ret, data = getattr(self.context, method)(*args, **kwargs)
        if ret != 0:
            error = vendor_error(data)
            error.operation = method
            raise error
        self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
        if ttl:
            self.cache[key] = (time.monotonic() + ttl, data)
        return data

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

    def expirations(self, cfg, check_permissions=True):
        if check_permissions: self.permissions()
        rows = self.call('get_option_expiration_date', code_for(cfg.underlyingId), ttl=1800)
        return sorted(set(rows['strike_time'].tolist()))

    async def list_expirations(self, cfg):
        return await self.run(self.expirations, cfg)

    def contracts(self, cfg, expiration, check_permissions=True):
        if check_permissions: self.permissions()
        rows = self.call('get_option_chain', code_for(cfg.underlyingId), start=expiration, end=expiration, ttl=1800)
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

    def batch_snapshots(self, configs):
        """Warm static chains under rate limits, then batch all dynamic quotes.

        Each monitor keeps its own errors; no expiry or strike substitution.
        Two snapshot requests per warm cycle for the 20-symbol / 240-leg universe.
        """
        self.permissions()
        catalogs, errors = {}, {}
        for mid, cfg in configs.items():
            try:
                day = utcnow().astimezone(ZoneInfo(cfg.exchangeTimezone)).date().isoformat()
                expiry = day if cfg.expiryMode == 'sameDay' else cfg.expiration
                dates = self.expirations(cfg, check_permissions=False)
                catalogs[mid] = self.contracts(cfg, expiry, check_permissions=False) if expiry in dates else []
            except ProviderError as e:
                errors[mid] = e
        ready = {mid: cfg for mid, cfg in configs.items() if mid in catalogs}
        if not ready:
            return {}, errors
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
                at = utcnow()
                state = states.get(code_for(cfg.underlyingId), 'UNKNOWN')
                snapshots[mid] = Snapshot(monitorId=mid, valuationAt=at, provider='futu',
                    underlying=anchors[mid], contracts=catalogs[mid],
                    legs=[Leg(contract=c, quote=quotes[c.id]) for c in cs if c.id in quotes],
                    marketOpen=state in ('MORNING', 'AFTERNOON', 'TRADING') and regular_session(cfg, at),
                    optionSessionOpen=False, marketReason='FUTU_' + state + '_OPTION_CUTOFF_UNVERIFIED')
            except ProviderError as e:
                errors[mid] = e
        return snapshots, errors

    async def collect_batch(self, configs):
        return await self.run(self.batch_snapshots, configs)

    async def close(self):
        if self.pending is not None:
            await asyncio.gather(self.pending, return_exceptions=True)
        if self.context is not None:
            await asyncio.get_running_loop().run_in_executor(self.executor, self.context.close)
            self.context = None
        self.executor.shutdown(wait=False)
