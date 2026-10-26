"""Deterministic expiry selection; exchange close is a labelled research assumption."""
from datetime import date
from functools import lru_cache
import exchange_calendars as xcals

@lru_cache(maxsize=512)
def reference_close(calendar, expiration):
    """Regular-session close (including half days), NOT verified option trading/exercise cutoff."""
    day = date.fromisoformat(expiration).isoformat()
    cal = xcals.get_calendar(calendar)
    if not cal.is_session(day):
        return None
    return cal.session_close(day).to_pydatetime()

def nearest_expiration(cfg, dates, at):
    from zoneinfo import ZoneInfo
    today = at.astimezone(ZoneInfo(cfg.exchangeTimezone)).date().isoformat()
    for expiration in sorted(set(dates)):
        date.fromisoformat(expiration)  # reject malformed supplier data instead of guessing
        if expiration < today:
            continue
        end = reference_close(cfg.calendar, expiration)
        if end is not None and end > at:
            return expiration
    return None
