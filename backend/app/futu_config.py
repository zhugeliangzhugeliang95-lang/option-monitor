"""Futu worker settings from the environment, validated once (plan item 1, section D).

Invalid values fall back to their defaults with a WARNING naming the variable, the
rejected value and the default. Values are timing knobs only, never credentials.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import lru_cache
import logging, math, os

log = logging.getLogger('app.futu')

# Per-SDK-call headroom in seconds (≈1.5–2× measured). Section A table.
HEADROOM_DEFAULTS = {
    'get_user_info': ('FUTU_HEADROOM_USER_INFO', 2.0),
    'get_global_state': ('FUTU_HEADROOM_GLOBAL_STATE', 0.5),
    'get_option_expiration_date': ('FUTU_HEADROOM_EXPIRATIONS', 1.0),
    'get_option_chain': ('FUTU_HEADROOM_CHAIN', 1.0),
    'get_market_state': ('FUTU_HEADROOM_MARKET_STATE', 0.5),
    'get_market_snapshot': ('FUTU_HEADROOM_SNAPSHOT', 0.75),
    '*': ('FUTU_HEADROOM_DEFAULT', 1.0),
}
CHAIN_LIMIT = 8          # internal per-method window limit (futu.py call())
WINDOW_BATCHES = 4       # batch starts per 30.1 s window at P = 10.0 s
MAX_FRESH_TTL = 1800 + 600   # base chain TTL + max stagger
PREWARM_MIN, PREWARM_MAX = (8, 15), (9, 25)


@dataclass
class FutuSettings:
    hot_budget: float = 7.0
    batch_timeout: float = 9.0
    warm_per_cycle: int = 1
    close_wait: float = 10.0
    hang_seconds: float = 60.0
    busy_alert_cycles: int = 6
    chain_warm_max_seconds: float = 600.0
    maint_alert_seconds: float = 300.0
    watchdog: int = 0
    swr: int = 1                       # (c) serve chains past their fresh TTL (flagged) while refreshing
    max_age: float = 5400.0            # (c) SWR cap for today's 0DTE chain, seconds since fetchedAt
    max_age_future: float = 345600.0   # (b5) SWR cap for X > D chains (4 days)
    prewarm_start: tuple = (9, 0)      # (F) NY HH:MM, start of the chain warm window on trading days
    headroom: dict = field(default_factory=lambda: {k: v for k, (_, v) in HEADROOM_DEFAULTS.items()})

    def room(self, method):
        return self.headroom.get(method, self.headroom['*'])

    def fixed_worst(self):
        return (self.room('get_global_state') + self.room('get_user_info')
                + self.room('get_market_state') + 2 * self.room('get_market_snapshot'))

    def warm_bound(self):
        by_limit = CHAIN_LIMIT // WINDOW_BATCHES
        by_time = math.floor((self.hot_budget - self.fixed_worst()) / self.room('get_option_chain'))
        return min(by_limit, by_time)


def _num(env, name, default, kind=float, lo=None, hi=None):
    raw = env.get(name)
    if raw is None or raw == '':
        return default
    try:
        value = kind(raw)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError
    except (TypeError, ValueError):
        log.warning('futu config %s=%r invalid, using default %s', name, raw, default)
        return default
    if value <= 0 or (lo is not None and value < lo) or (hi is not None and value > hi):
        log.warning('futu config %s=%r out of range, using default %s', name, raw, default)
        return default
    return value


def load(env=None) -> FutuSettings:
    env = os.environ if env is None else env
    d = FutuSettings()
    s = FutuSettings()
    s.hot_budget = _num(env, 'FUTU_HOT_BUDGET', d.hot_budget)
    s.batch_timeout = _num(env, 'FUTU_BATCH_TIMEOUT', d.batch_timeout)
    if not (s.hot_budget < s.batch_timeout <= 45):
        log.warning('futu config FUTU_HOT_BUDGET=%s / FUTU_BATCH_TIMEOUT=%s inconsistent, using defaults %s / %s',
                    s.hot_budget, s.batch_timeout, d.hot_budget, d.batch_timeout)
        s.hot_budget, s.batch_timeout = d.hot_budget, d.batch_timeout
    for method, (name, default) in HEADROOM_DEFAULTS.items():
        value = _num(env, name, default)
        if value >= s.hot_budget:
            log.warning('futu config %s=%s not below FUTU_HOT_BUDGET, using default %s', name, value, default)
            value = default
        s.headroom[method] = value
    s.close_wait = _num(env, 'FUTU_CLOSE_WAIT', d.close_wait)
    s.hang_seconds = _num(env, 'FUTU_HANG_SECONDS', d.hang_seconds, lo=20, hi=600)
    if s.hang_seconds <= s.batch_timeout:
        log.warning('futu config FUTU_HANG_SECONDS=%s not above FUTU_BATCH_TIMEOUT, using default %s', s.hang_seconds, d.hang_seconds)
        s.hang_seconds = d.hang_seconds
    s.busy_alert_cycles = _num(env, 'FUTU_BUSY_ALERT_CYCLES', d.busy_alert_cycles, int, lo=2, hi=60)
    s.chain_warm_max_seconds = _num(env, 'FUTU_CHAIN_WARM_MAX_SECONDS', d.chain_warm_max_seconds, lo=120, hi=3600)
    s.maint_alert_seconds = _num(env, 'FUTU_MAINT_ALERT_SECONDS', d.maint_alert_seconds)
    raw = env.get('FUTU_WATCHDOG')
    if raw not in (None, '', '0'):
        # Phase 2 (section G) is not implemented: only status escalation (E.1) exists.
        log.warning('futu config FUTU_WATCHDOG=%r not supported (phase 2), using 0', raw)
    s.watchdog = 0
    raw = env.get('FUTU_CHAIN_SWR')
    if raw in (None, ''):
        s.swr = d.swr
    elif raw in ('0', '1'):
        s.swr = int(raw)
    else:
        log.warning('futu config FUTU_CHAIN_SWR=%r invalid, using default %s', raw, d.swr)
        s.swr = d.swr
    s.max_age = _num(env, 'FUTU_CHAIN_MAX_AGE', d.max_age)
    if s.max_age <= MAX_FRESH_TTL:
        log.warning('futu config FUTU_CHAIN_MAX_AGE=%s not above the max fresh TTL %s, using default %s', s.max_age, MAX_FRESH_TTL, d.max_age)
        s.max_age = d.max_age
    s.max_age_future = _num(env, 'FUTU_CHAIN_MAX_AGE_FUTURE', d.max_age_future, lo=MAX_FRESH_TTL + 1)
    raw = env.get('FUTU_PREWARM_START')
    s.prewarm_start = d.prewarm_start
    if raw not in (None, ''):
        try:
            hh, mm = raw.split(':')
            value = (int(hh), int(mm))
            if len(hh) != 2 or len(mm) != 2 or not (0 <= value[1] < 60) or not (PREWARM_MIN <= value <= PREWARM_MAX):
                raise ValueError
            s.prewarm_start = value
        except ValueError:
            log.warning('futu config FUTU_PREWARM_START=%r invalid (HH:MM within 08:15-09:25 New York), using default 09:00', raw)
    raw = env.get('FUTU_CHAIN_WARM_PER_CYCLE')
    try:
        warm = int(raw) if raw not in (None, '') else d.warm_per_cycle
    except ValueError:
        warm = 0
    if warm < 1:
        log.warning('futu config FUTU_CHAIN_WARM_PER_CYCLE=%r invalid, using default %s', raw, d.warm_per_cycle)
        warm = d.warm_per_cycle
    bound = max(1, s.warm_bound())
    if warm > bound:
        log.warning('futu config FUTU_CHAIN_WARM_PER_CYCLE=%s above bound %s, clamped', warm, bound)
        warm = bound
    s.warm_per_cycle = warm
    return s


@lru_cache(maxsize=1)
def settings() -> FutuSettings:
    s = load()
    log.info('futu config effective: hot_budget=%s batch_timeout=%s warm_per_cycle=%s (bound %s) close_wait=%s '
             'hang_seconds=%s busy_alert_cycles=%s chain_warm_max_seconds=%s maint_alert_seconds=%s watchdog=%s headroom=%s '
             'swr=%s max_age=%s max_age_future=%s prewarm_start=%02d:%02d',
             s.hot_budget, s.batch_timeout, s.warm_per_cycle, s.warm_bound(), s.close_wait, s.hang_seconds,
             s.busy_alert_cycles, s.chain_warm_max_seconds, s.maint_alert_seconds, s.watchdog, s.headroom,
             s.swr, s.max_age, s.max_age_future, *s.prewarm_start)
    return s
