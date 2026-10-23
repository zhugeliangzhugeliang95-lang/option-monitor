"""Local historical paper orders only. No broker imports or order transport."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from math import floor, isfinite
from functools import lru_cache
import exchange_calendars as xcals
import pandas as pd
from typing import Literal
from pydantic import Field, model_validator
from sqlalchemy import select, func
from .models import Model
from .db import RawSnapshot, AggregateResult, RunConfig, Monitor, digest

NY = ZoneInfo('America/New_York')
VERSION = 'independent-paper-v1'

class PaperConfig(Model):
    tradingDay: date
    optionDirection: Literal['convergence', 'follow_stock']
    initialCapital: float = Field(200000, ge=1000, le=10000000)
    stockAllocation: float = Field(.5, gt=0, lt=1)
    winProbability: float = Field(.5, gt=0, lt=1)
    kellyFraction: float = Field(.25, gt=0, le=1)
    stockStopBps: float = Field(25, ge=5, le=1000)
    optionPremiumStop: float = Field(.20, gt=0, le=1)
    convergenceFraction: float = Field(.80, gt=0, lt=1)
    maxLeverage: float = Field(2, ge=1, le=2)
    tradeRiskFraction: float = Field(.005, gt=0, le=.01)
    dailyLossFraction: float = Field(.02, gt=0, le=.05)
    maxHoldingSeconds: int = Field(900, ge=60, le=3600)
    delaySeconds: int = Field(10, ge=1, le=120)
    pendingExpirySeconds: int = Field(60, ge=10, le=300)
    costMultiplier: float = Field(1, ge=1, le=3)
    # Assumptions, not claimed broker tariffs. Spread is paid using actual bid/ask.
    stockCommissionPerShare: float = Field(.005, ge=0, le=10)
    stockMinimumCommission: float = Field(1, ge=0, le=10)
    optionCommissionPerContract: float = Field(.65, ge=0, le=10)
    stockSlippage: float = Field(.01, ge=0, le=10)
    optionSlippage: float = Field(.01, ge=0, le=10)
    financingAnnual: float = Field(.08, ge=0, le=10)
    borrowAnnual: float = Field(.05, ge=0, le=10)

    @model_validator(mode="after")
    def valid_delay(self):
        if self.pendingExpirySeconds < self.delaySeconds:
            raise ValueError("挂单有效期必须大于执行延迟")
        return self


def timestamp(v):
    t = datetime.fromisoformat(v.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Naive quote timestamp')
    return t.astimezone(timezone.utc)


def kelly(gap, stop, probability=.5):
    b = abs(gap) / stop
    return b, max(0., probability - (1 - probability) / b) if b else 0.


@lru_cache(maxsize=10000)
def regular_minute(minute):
    return bool(xcals.get_calendar('XNYS').is_open_on_minute(pd.Timestamp(minute), ignore_breaks=False))

def regular(t):
    local = t.astimezone(NY)
    return time(9, 30) <= local.time() < time(16) and regular_minute(t.replace(second=0, microsecond=0))


def load_events(db, cfg):
    start = datetime.combine(cfg.tradingDay, time(9, 30), NY).astimezone(timezone.utc)
    end = datetime.combine(cfg.tradingDay, time(16), NY).astimezone(timezone.utc)
    # Select only needed JSON fields, not all five repeated modes or the static chain.
    q = select(RawSnapshot.id, RawSnapshot.created_at, RawSnapshot.config_id,
        func.json_extract(RawSnapshot.payload, '$.valuationAt'),
        func.json_extract(RawSnapshot.payload, '$.underlying'),
        func.json_extract(RawSnapshot.payload, '$.legs'),
        func.json_extract(RawSnapshot.payload, '$.optionSessionOpen'),
        func.json_extract(AggregateResult.payload, '$.modes.MID'),
        func.json_extract(AggregateResult.payload, '$.duplicate'),
        func.json_extract(AggregateResult.payload, '$.feedType'),
        RunConfig.payload, RunConfig.hash
    ).join(AggregateResult, AggregateResult.snapshot_id == RawSnapshot.id).join(
        RunConfig, RunConfig.id == RawSnapshot.config_id).join(Monitor, Monitor.id == RawSnapshot.monitor_id).where(
        Monitor.active == True,
        RawSnapshot.created_at >= start.isoformat(), RawSnapshot.created_at < end.isoformat()
    ).order_by(RawSnapshot.created_at, RawSnapshot.id)
    import json
    events = []
    for sid, created, cid, valuation, stock, legs, session, mode, duplicate, feed, rc, ch in db.execute(q):
        if rc['provider'] != 'futu' or feed != 'REALTIME':
            continue
        at = timestamp(created)
        if not regular(at) or timestamp(valuation).astimezone(NY).date() != cfg.tradingDay:
            continue
        events.append(dict(id=sid, at=at, valuation=timestamp(valuation), symbol=rc['symbol'],
            configId=cid, configHash=ch, stock=json.loads(stock), legs=json.loads(legs),
            mode=json.loads(mode), duplicate=bool(duplicate), session=bool(session),
            expiration=rc.get('expiration'), config=rc))
    return events


def quote_reason(q, at, strict, option=False):
    if not q or q.get('feedType') != 'REALTIME' or q.get('unit') != 'PER_SHARE':
        return 'NON_REAL_QUOTE'
    b, a = q.get('bid'), q.get('ask')
    if not all(isinstance(v, (float, int)) and isfinite(v) for v in (b, a)) or b < 0 or a <= 0 or b > a:
        return 'INVALID_BOOK'
    if strict:
        for field in ('bidAt', 'askAt'):
            if not q.get(field): return 'MISSING_EVENT_TIME'
            age = (at - timestamp(q[field])).total_seconds()
            if age < -.1 or age > 5: return 'STALE_OR_FUTURE_BOOK'
    else:
        # Reception time is an explicit exploratory assumption, NEVER an event-time replacement.
        for field in ('receivedAt', 'ingestedAt'):
            if not q.get(field): return 'MISSING_RECEIPT'
            age = (at - timestamp(q[field])).total_seconds()
            if age < -.1 or age > 30: return 'STALE_OR_FUTURE_RECEIPT'
        if not q.get('lastAt'): return 'MISSING_LAST_TIME'
        age = (at - timestamp(q['lastAt'])).total_seconds()
        if age < -.1 or age > (300 if option else 30): return 'STALE_OR_FUTURE_LAST'
    return None


def signal_reason(e, strict):
    if e['duplicate']: return 'DUPLICATE'
    m = e['mode']
    if not isinstance(m.get('rawGapBps'), (int, float)) or not isfinite(m['rawGapBps']): return 'MISSING_GAP'
    if not m.get('requestedCount') or len(m['rows']) != m['requestedCount']: return 'INCOMPLETE_PAIRS'
    r = quote_reason(e['stock'], e['at'], strict)
    if r: return r
    times = []
    for row in m['rows']:
        if row.get('value') is None or not row.get('call') or not row.get('put'): return 'INCOMPLETE_PAIRS'
        for side in ('call', 'put'):
            leg = row[side]; c = leg['contract']; q = leg['quote']
            if c.get('adjusted') or float(c['multiplier']) != 100 or c['deliverable'] != '100_SHARES': return 'NONSTANDARD_CONTRACT'
            if c['expiration'] < e['at'].astimezone(NY).date().isoformat(): return 'EXPIRED'
            if q.get('source') != e['stock'].get('source'): return 'SOURCE_MISMATCH'
            r = quote_reason(q, e['at'], strict, True)
            if r: return r
            if strict: times.extend([timestamp(q['bidAt']), timestamp(q['askAt'])])
    if strict:
        times.extend([timestamp(e['stock']['bidAt']), timestamp(e['stock']['askAt'])])
        if (max(times) - min(times)).total_seconds() > 1: return 'UNSYNCHRONIZED'
        if not e['session']: return 'UNCONFIRMED_OPTION_SESSION'
    return None


def commission(kind, qty, cfg):
    return cfg.costMultiplier * (max(cfg.stockMinimumCommission, qty * cfg.stockCommissionPerShare)
        if kind == 'stock' else qty * cfg.optionCommissionPerContract)


def fill_price(q, buy, kind, cfg):
    slip = cfg.costMultiplier * (cfg.stockSlippage if kind == 'stock' else cfg.optionSlippage)
    return q['ask'] + slip if buy else max(0., q['bid'] - slip)


def financing(p, at, cfg):
    if p['kind'] != 'stock': return 0.
    days = max(0, (at - p['entryAt']).total_seconds()) / 86400
    # Conservatively charge long financing on full notional, rather than infer free cash allocation.
    rate = cfg.financingAnnual if p['direction'] > 0 else cfg.borrowAnnual
    return p['entryPrice'] * p['qty'] * days / 365 * rate * cfg.costMultiplier


def make_account(kind, capital):
    return dict(kind=kind, initial=capital, cash=capital, positions={}, pending={}, cooldown={},
        orders=[], trades=[], reasons=Counter(), peak=capital, maxDrawdown=0., maxExposure=0.,
        fees=0., finance=0., halted=False, curve=[], opportunities=0)


def account_equity(a, at, cfg):
    return a['cash'] + sum(p['direction'] * p['qty'] * p['multiplier'] * p['markPrice'] - financing(p, at, cfg)
        for p in a['positions'].values())


def exposure(a):
    return sum(p['qty'] * p['multiplier'] * p['markPrice'] for p in a['positions'].values())


def held_quote(e, p):
    if p['kind'] == 'stock': return e['stock']
    return next((l['quote'] for l in e['legs'] if l['contract']['id'] == p['contractId']), None)


def log_order(a, e, side, status, reason, **extra):
    a['orders'].append(dict(snapshotId=e['id'], at=e['at'].isoformat(), symbol=e['symbol'],
        side=side, status=status, reason=reason, hypothetical=True, **extra))


def open_position(a, e, pending, cfg, strict):
    symbol = e['symbol']; kind = a['kind']; gap = e['mode']['rawGapBps']
    if e['at'].astimezone(NY).time() >= time(15, 30): return 'ENTRY_CUTOFF'
    if gap * pending['gap'] <= 0 or abs(gap) < cfg.stockStopBps:
        return 'SIGNAL_GONE'
    if e['configId'] != pending['configId']: return 'CONFIG_CHANGED'
    equity = account_equity(a, e['at'], cfg)
    b, f = kelly(gap, cfg.stockStopBps, cfg.winProbability)
    if f <= 0: return 'NONPOSITIVE_KELLY'
    direction = 1 if gap > 0 else -1
    contract_id = None; multiplier = 1; q = e['stock']; right = None
    stock_mid = (q['bid'] + q['ask']) / 2
    if kind == 'option':
        direction = 1
        right = ('P' if gap > 0 else 'C') if cfg.optionDirection == 'convergence' else ('C' if gap > 0 else 'P')
        candidates = [l for l in e['legs'] if l['contract']['right'] == right and l['contract']['id'] == pending['contractId']]
        if not candidates: return 'HELD_CONTRACT_NOT_QUOTED'
        leg = candidates[0]; contract_id = leg['contract']['id']; q = leg['quote']; multiplier = 100
        r = quote_reason(q, e['at'], strict, True)
        if r: return r
    if strict and kind == 'stock' and direction < 0: return 'BORROW_NOT_VERIFIED'
    price = fill_price(q, direction > 0, kind, cfg)
    if price <= 0: return 'INVALID_FILL_PRICE'
    close_price = fill_price(q, direction < 0, kind, cfg)
    # Profit target is a scenario based on the gap, not a predicted option premium return.
    reward_unit = abs(gap) * stock_mid / 10000 * multiplier * cfg.convergenceFraction
    spread_unit = abs(price - close_price) * multiplier
    if reward_unit <= spread_unit: return 'SPREAD_EXCEEDS_REWARD_PROXY'
    risk_unit = stock_mid * cfg.stockStopBps / 10000 * multiplier
    if kind == 'option': risk_unit = min(risk_unit, price * multiplier * cfg.optionPremiumStop)
    risk_budget = min(equity * f * cfg.kellyFraction, equity * cfg.tradeRiskFraction,
        max(0., account_equity(a, e['at'], cfg) - a['initial'] * (1 - cfg.dailyLossFraction)))
    risk_left = max(0., equity * .02 - sum(p['risk'] for p in a['positions'].values()))
    qty = floor(min(risk_budget, risk_left) / max(.01, risk_unit + spread_unit))
    if kind == 'stock':
        remaining = max(0., equity * cfg.maxLeverage - exposure(a))
        qty = min(qty, floor(min(equity * .5, remaining) / price))
    else:
        # Cap the entire premium at the per-trade loss budget: stops may gap or fail.
        qty = min(qty, floor(min(risk_budget, risk_left, max(0., a['cash']), equity * .02 - exposure(a)) / (price * 100 + cfg.optionCommissionPerContract * cfg.costMultiplier)))
    sizes = [q.get('bidSize'), q.get('askSize')]
    if any(v is None or v <= 0 for v in sizes): return 'MISSING_LIQUIDITY'
    qty = min(qty, floor(.1 * min(sizes)))
    if qty < 1: return 'LOT_OR_RISK_LIMIT'
    fee = commission(kind, qty, cfg)
    if reward_unit * qty <= spread_unit * qty + 2 * fee: return 'COST_EXCEEDS_REWARD_PROXY'
    risk = (risk_unit + spread_unit) * qty + 2 * fee
    if kind == 'option': risk = price * 100 * qty + fee
    if risk > min(risk_budget, risk_left): return 'RISK_AFTER_FEES'
    a['cash'] -= direction * qty * multiplier * price + fee; a['fees'] += fee
    p = dict(kind=kind, symbol=symbol, direction=direction, qty=qty, multiplier=multiplier,
        contractId=contract_id, right=right, entryAt=e['at'], entryPrice=price, entryStock=stock_mid,
        markPrice=close_price, markAt=e['at'], risk=risk, entryFee=fee, entryGap=gap,
        configId=e['configId'], entrySnapshotId=e['id'], signalSnapshotId=pending['snapshotId'],
        kelly=f, appliedKelly=f * cfg.kellyFraction, odds=b, exitPending=None)
    a['positions'][symbol] = p
    marked = account_equity(a, e['at'], cfg)
    a['maxDrawdown'] = max(a['maxDrawdown'], (a['peak'] - marked) / a['peak'])
    a['maxExposure'] = max(a['maxExposure'], exposure(a))
    log_order(a, e, 'OPEN', 'FILLED', 'NEXT_SNAPSHOT', quantity=qty, price=price, fee=fee,
        direction=direction, contractId=contract_id, signalSnapshotId=pending['snapshotId'],
        signalAt=pending['at'].isoformat(), rawKelly=f, appliedKelly=f * cfg.kellyFraction, odds=b)
    return None


def manage_position(a, e, cfg, strict, valid_signal):
    p = a['positions'].get(e['symbol'])
    if not p: return
    q = held_quote(e, p)
    r = quote_reason(q, e['at'], strict, p['kind'] == 'option')
    if r:
        a['reasons']['EXIT_' + r] += 1
        return
    price = fill_price(q, p['direction'] < 0, p['kind'], cfg)
    p['markPrice'] = price; p['markAt'] = e['at']
    gross = (price - p['entryPrice']) * p['direction'] * p['qty'] * p['multiplier']
    equity = account_equity(a, e['at'], cfg)
    if equity <= a['initial'] * (1 - cfg.dailyLossFraction): a['halted'] = True
    reason = None
    stock_ok = not quote_reason(e['stock'], e['at'], strict)
    stock_mid = (e['stock']['bid'] + e['stock']['ask']) / 2 if stock_ok else None
    directional = p['direction'] if p['kind'] == 'stock' else (1 if p['right'] == 'C' else -1)
    if a['halted']: reason = 'DAILY_LOSS'
    elif stock_mid is not None and directional * (stock_mid / p['entryStock'] - 1) * 10000 <= -cfg.stockStopBps: reason = 'UNDERLYING_STOP'
    elif p['kind'] == 'option' and price <= p['entryPrice'] * (1 - cfg.optionPremiumStop): reason = 'PREMIUM_STOP'
    elif exposure(a) > equity * cfg.maxLeverage and p['kind'] == 'stock': reason = 'LEVERAGE_REDUCTION'
    elif e['configId'] != p['configId']: reason = 'CONFIG_CHANGED'
    elif e['at'].astimezone(NY).time() >= time(15, 55): reason = 'SESSION_EXIT'
    elif (e['at'] - p['entryAt']).total_seconds() >= cfg.maxHoldingSeconds: reason = 'TIME_EXIT'
    elif valid_signal and e['mode']['rawGapBps'] * (1 if p['entryGap'] > 0 else -1) <= abs(p['entryGap']) * (1 - cfg.convergenceFraction): reason = 'GAP_CONVERGENCE'
    if reason and not p['exitPending']:
        p['exitPending'] = dict(at=e['at'], reason=reason, snapshotId=e['id'])
        log_order(a, e, 'CLOSE', 'PENDING', reason)
    pending = p['exitPending']
    if not pending or (e['at'] - pending['at']).total_seconds() < cfg.delaySeconds: return
    size = q.get('askSize' if p['direction'] < 0 else 'bidSize')
    if size is None or size < p['qty']:
        a['reasons']['EXIT_INSUFFICIENT_LIQUIDITY'] += 1; return
    fee = commission(p['kind'], p['qty'], cfg); finance = financing(p, e['at'], cfg)
    a['cash'] += p['direction'] * p['qty'] * p['multiplier'] * price - fee - finance
    a['fees'] += fee; a['finance'] += finance
    a['trades'].append(dict(symbol=p['symbol'], contractId=p['contractId'], direction=p['direction'],
        right=p['right'], quantity=p['qty'], entryAt=p['entryAt'].isoformat(), exitAt=e['at'].isoformat(),
        entryPrice=p['entryPrice'], exitPrice=price, grossPnl=gross,
        fees=p['entryFee'] + fee, financing=finance, netPnl=gross - p['entryFee'] - fee - finance,
        entryGapBps=p['entryGap'], odds=p['odds'], rawKelly=p['kelly'], appliedKelly=p['appliedKelly'],
        signalSnapshotId=p['signalSnapshotId'], entrySnapshotId=p['entrySnapshotId'], exitSnapshotId=e['id'],
        exitReason=pending['reason'], hypothetical=True))
    log_order(a, e, 'CLOSE', 'FILLED', pending['reason'], quantity=p['qty'], price=price, fee=fee)
    del a['positions'][e['symbol']]; a['cooldown'][e['symbol']] = e['at'] + timedelta(seconds=60)


def replay(events, cfg, strict):
    accounts = [make_account('stock', cfg.initialCapital * cfg.stockAllocation),
                make_account('option', cfg.initialCapital * (1 - cfg.stockAllocation))]
    quality = Counter(); seen = set()
    for e in events:
        # A repeated payload may be retained for data coverage but must not generate another signal/fill.
        key = (e['symbol'], e['configId'], e['valuation'])
        if key in seen: quality['DUPLICATE_TIMESTAMP'] += 1; continue
        seen.add(key)
        r = signal_reason(e, strict); quality[r or 'ELIGIBLE'] += 1
        for a in accounts:
            manage_position(a, e, cfg, strict, r is None)
            eq = account_equity(a, e['at'], cfg)
            if eq <= a['initial'] * (1 - cfg.dailyLossFraction): a['halted'] = True
            a['peak'] = max(a['peak'], eq)
            a['maxDrawdown'] = max(a['maxDrawdown'], (a['peak'] - eq) / a['peak'])
            a['maxExposure'] = max(a['maxExposure'], exposure(a))
            if not a['curve'] or e['at'].isoformat()[:16] != a['curve'][-1]['at'][:16]:
                a['curve'].append(dict(at=e['at'].isoformat(), equity=eq))
            symbol = e['symbol']; pending = a['pending'].get(symbol)
            if pending:
                age = (e['at'] - pending['at']).total_seconds()
                if age >= cfg.delaySeconds:
                    reason = 'PENDING_EXPIRED' if age > cfg.pendingExpirySeconds else r
                    if not reason and a['halted']: reason = 'DAILY_LOSS'
                    if not reason and len(a['positions']) >= 3: reason = 'MAX_POSITIONS'
                    if not reason: reason = open_position(a, e, pending, cfg, strict)
                    if reason:
                        a['reasons'][reason] += 1; log_order(a, e, 'OPEN', 'CANCELLED', reason)
                    del a['pending'][symbol]
                continue
            if r:
                a['reasons'][r] += 1; continue
            if symbol in a['positions'] or a['halted'] or len(a['positions']) >= 3: continue
            if e['at'] < a['cooldown'].get(symbol, e['at']): continue
            if e['at'].astimezone(NY).time() >= time(15, 30): continue
            gap = e['mode']['rawGapBps']; b, f = kelly(gap, cfg.stockStopBps, cfg.winProbability)
            if f <= 0:
                a['reasons']['NONPOSITIVE_KELLY'] += 1; continue
            a['opportunities'] += 1
            contract_id = None
            if a['kind'] == 'option':
                right = ('P' if gap > 0 else 'C') if cfg.optionDirection == 'convergence' else ('C' if gap > 0 else 'P')
                legs = [row['put' if right == 'P' else 'call'] for row in e['mode']['rows']]
                mid = (e['stock']['bid'] + e['stock']['ask']) / 2
                leg = min(legs, key=lambda l: (abs(float(l['contract']['strike']) - mid), float(l['contract']['strike'])))
                contract_id = leg['contract']['id']
            a['pending'][symbol] = dict(at=e['at'], gap=gap, snapshotId=e['id'], configId=e['configId'], contractId=contract_id)
            log_order(a, e, 'OPEN', 'PENDING', 'GAP_AND_KELLY', gapBps=gap, odds=b, rawKelly=f)
    end = events[-1]['at'] if events else datetime.combine(cfg.tradingDay, time(16), NY)
    reports = []
    for a in accounts:
        positions = []
        for p in a['positions'].values():
            positions.append({**p, 'entryAt':p['entryAt'].isoformat(), 'markAt':p['markAt'].isoformat(),
                'markAgeSeconds':(end - p['markAt']).total_seconds(),
                'exitPending':({**p['exitPending'], 'at':p['exitPending']['at'].isoformat()} if p['exitPending'] else None)})
        eq = account_equity(a, end, cfg); wins = sum(t['netPnl'] > 0 for t in a['trades'])
        realized = sum(t['netPnl'] for t in a['trades'])
        reports.append(dict(kind=a['kind'], initialCapital=a['initial'], cash=a['cash'], equity=eq,
            netPnl=eq - a['initial'], realizedNetPnl=realized, unrealizedNetPnl=eq-a['initial']-realized,
            returnPct=(eq/a['initial']-1)*100, fees=a['fees'], financing=a['finance'],
            closedTrades=len(a['trades']), filledOrders=sum(o['status']=='FILLED' for o in a['orders']),
            hitRate=wins/len(a['trades']) if a['trades'] else None, maxDrawdownPct=a['maxDrawdown']*100,
            maxExposure=a['maxExposure'], dailyHalted=a['halted'], positions=positions,
            pendingCount=len(a['pending']), trades=a['trades'], orders=a['orders'], reasons=dict(a['reasons']),
            opportunities=a['opportunities'], equityCurve=a['curve'], finalValueVerified=not positions))
    return dict(mode='STRICT' if strict else 'EXPLORATORY', quality=dict(quality), accounts=reports,
        totalEquity=sum(a['equity'] for a in reports), totalNetPnl=sum(a['netPnl'] for a in reports),
        status='UNRESOLVED_POSITIONS' if any(a['positions'] for a in reports) else
            ('DATA_BLOCKED' if not quality['ELIGIBLE'] else 'COMPLETED_PARTIAL_REPLAY'))


def run_research(events, cfg):
    events = sorted(events, key=lambda e:(e['at'],e['id']))
    coverage = {}
    for e in events:
        c = coverage.setdefault(e['symbol'], dict(rows=0, firstAt=e['at'].isoformat(), lastAt=None,
            largestAbsGapBps=0., aboveStop=0, missingBookTime=0))
        c['rows'] += 1; c['lastAt'] = e['at'].isoformat()
        gap = e['mode'].get('rawGapBps')
        c['largestAbsGapBps'] = max(c['largestAbsGapBps'], abs(gap or 0))
        c['aboveStop'] += gap is not None and abs(gap)>cfg.stockStopBps
        c['missingBookTime'] += not e['stock'].get('bidAt') or not e['stock'].get('askAt')
    strict = replay(events,cfg,True); exploratory = replay(events,cfg,False)
    sensitivity=[]
    for multiplier,delay in ((2,cfg.delaySeconds),(3,cfg.delaySeconds),(1,cfg.delaySeconds*2)):
        altered=cfg.model_copy(update={'costMultiplier':multiplier,'delaySeconds':delay})
        result=replay(events,altered,False)
        sensitivity.append(dict(costMultiplier=multiplier,delaySeconds=delay,totalNetPnl=result['totalNetPnl'],
            closedTrades=sum(a['closedTrades'] for a in result['accounts']),status=result['status']))
    return dict(modelVersion=VERSION, modelHash=sha256(Path(__file__).read_bytes()).hexdigest(), hypothetical=True, liveOrdersEnabled=False,
        config=cfg.model_dump(mode='json'),configHash=digest(cfg.model_dump(mode='json')),
        datasetHash=digest([(e['id'],e['configHash'],e['at'].isoformat(),e['stock'],e['legs'],e['mode']) for e in events]),
        snapshotIds=[e['id'] for e in events], coverage=coverage, sampleCount=len(events),
        firstAt=events[0]['at'].isoformat() if events else None,lastAt=events[-1]['at'].isoformat() if events else None,
        fullDay=False,classification='EXPLORATORY_UNVALIDATED',benchmark={'name':'现金不交易（不计利息）','netPnl':0},
        strict=strict,exploratory=exploratory,sensitivity=sensitivity,
        limitations=['历史回放，不是已发生的券商成交或全天收益；没有样本外验证。',
        '研究回放假设收到的买卖价当时有效；缺少盘口事件时间，不能证明可成交。严格模式不放宽该门禁。',
        '胜率50%与价差/25bp赔率均为用户假设；四分之一凯利不是经验证的最优仓位。',
        '期权凯利使用正股价差代理，未估计Delta/IV/时间价值，不等于期权实际盈利赔率。',
        '独立正股与期权账户不互相对冲；价差回落不保证任一账户获利。',
        '费用、滑点、融资与借券费为研究假设；借券可用性未核验；税费未建模。',
        '止损触发后在下一有效快照成交，跳空或缺报价可能超过止损；期权单笔全额权利金受限。',
        '无到期行权/交割和股息处理；收盘前退出，缺少退出报价则保留未平仓，绝不虚构平仓收益。',
        '标的是事后研究名单；未据本日盈利优化参数，敏感性结果不用于挑选最佳方案。'])
