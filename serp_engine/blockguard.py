"""Block / captcha handling: pause state + alerts."""
import datetime as dt

import store
from _common import log, now_iso


def paused(conn):
    until = store.get_state(conn, "paused_until")
    if until and until > now_iso():
        return until
    return None


def pause(conn, hours, reason):
    until = (dt.datetime.utcnow() + dt.timedelta(hours=hours)).replace(microsecond=0).isoformat() + "Z"
    store.set_state(conn, "paused_until", until)
    store.set_state(conn, "last_block", {"ts": now_iso(), "reason": reason})
    log(f"PAUSED until {until}: {reason}")
    return until


def unpause(conn):
    store.set_state(conn, "paused_until", None)
