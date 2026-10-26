"""Plan item 1, test 15: env validation (section D). Pure functions, no OpenD."""
import logging
import pytest
from app import futu_config
from app.futu_config import load, FutuSettings


def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_defaults_and_bound():
    s = load({})
    assert (s.hot_budget, s.batch_timeout, s.warm_per_cycle, s.hang_seconds, s.busy_alert_cycles) == (7.0, 9.0, 1, 60.0, 6)
    assert s.warm_bound() == 2 and s.watchdog == 0


def test_hot_budget_not_below_timeout_resets_both(caplog):
    s = load({'FUTU_HOT_BUDGET': '9', 'FUTU_BATCH_TIMEOUT': '9'})
    assert (s.hot_budget, s.batch_timeout) == (7.0, 9.0) and warnings(caplog)


@pytest.mark.parametrize('raw', ['0', '-3', 'abc', 'nan', 'inf'])
def test_invalid_numbers_fall_back_with_warning(caplog, raw):
    s = load({'FUTU_HOT_BUDGET': raw, 'FUTU_CLOSE_WAIT': raw})
    assert s.hot_budget == 7.0 and s.close_wait == 10.0
    assert any('FUTU_CLOSE_WAIT' in w for w in warnings(caplog))


@pytest.mark.parametrize('raw,expected,warned', [('0', 1, True), ('3', 2, True), ('2', 2, False), ('', 1, False), ('x', 1, True)])
def test_warm_per_cycle(caplog, raw, expected, warned):
    assert load({'FUTU_CHAIN_WARM_PER_CYCLE': raw}).warm_per_cycle == expected
    assert bool(warnings(caplog)) == warned


def test_headroom_override_lowering_bound_clamps(caplog):
    s = load({'FUTU_HEADROOM_CHAIN': '2.0', 'FUTU_CHAIN_WARM_PER_CYCLE': '2'})
    assert s.room('get_option_chain') == 2.0 and s.warm_bound() == 1 and s.warm_per_cycle == 1
    assert any('clamped' in w for w in warnings(caplog))


def test_headroom_not_below_budget_rejected(caplog):
    assert load({'FUTU_HEADROOM_SNAPSHOT': '7'}).room('get_market_snapshot') == 0.75 and warnings(caplog)


@pytest.mark.parametrize('env,field,value', [
    ({'FUTU_HANG_SECONDS': '10'}, 'hang_seconds', 60.0), ({'FUTU_HANG_SECONDS': '601'}, 'hang_seconds', 60.0),
    ({'FUTU_HANG_SECONDS': '300'}, 'hang_seconds', 300.0), ({'FUTU_BUSY_ALERT_CYCLES': '1'}, 'busy_alert_cycles', 6),
    ({'FUTU_BUSY_ALERT_CYCLES': '61'}, 'busy_alert_cycles', 6), ({'FUTU_CHAIN_WARM_MAX_SECONDS': '60'}, 'chain_warm_max_seconds', 600.0),
    ({'FUTU_CHAIN_WARM_MAX_SECONDS': '1200'}, 'chain_warm_max_seconds', 1200.0)])
def test_ranges(env, field, value):
    assert getattr(load(env), field) == value


def test_hang_must_exceed_batch_timeout(caplog):
    assert load({'FUTU_HANG_SECONDS': '20', 'FUTU_BATCH_TIMEOUT': '30', 'FUTU_HOT_BUDGET': '25'}).hang_seconds == 60.0


def test_watchdog_phase2_not_enabled(caplog):
    assert load({'FUTU_WATCHDOG': '1'}).watchdog == 0
    assert any('FUTU_WATCHDOG' in w for w in warnings(caplog))


def test_effective_config_logged_once_without_secrets(caplog, monkeypatch):
    caplog.set_level(logging.INFO, 'app.futu')
    monkeypatch.setenv('FUTU_HOT_BUDGET', '6')
    futu_config.settings.cache_clear()
    try:
        assert futu_config.settings().hot_budget == 6.0 and futu_config.settings() is futu_config.settings()
        logged = [r.getMessage() for r in caplog.records if 'effective' in r.getMessage()]
        assert len(logged) == 1 and 'hot_budget=6.0' in logged[0]
    finally:
        monkeypatch.delenv('FUTU_HOT_BUDGET'); futu_config.settings.cache_clear()
