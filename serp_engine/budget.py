"""Weekly spend cap for paid AI-visibility probes.

The default ceiling is 0.50 USD per ISO week. At perplexity/sonar rates (about 1 USD per million
tokens in and out) a handful of prompts costs well under a cent, so the cap is not a throttle. It is
the stop that keeps a model swap, a retry loop or a pricing change from quietly draining the
OpenRouter balance: a runaway weekly job reports success either way, so nobody would notice.

Spend is metered from the cost OpenRouter reports per call (usage.include), kept per ISO week in
the `state` table, and checked BEFORE each call. A call whose price cannot be determined counts as
the configured estimate, never as zero: an unknown cost must not read as free.
"""
import datetime as dt
import os

import store
from _common import log

DEFAULT_WEEKLY_USD = 0.50
UNKNOWN_CALL_USD = 0.01  # charged against the budget when the API reports no cost


def week_key(day=None):
    d = day or dt.date.today()
    y, w, _ = d.isocalendar()
    return f"ai_spend_{y}W{w:02d}"


def limit():
    try:
        return float(os.environ.get("SERP_AI_WEEKLY_USD", DEFAULT_WEEKLY_USD))
    except ValueError:
        return DEFAULT_WEEKLY_USD


def spent(conn, day=None):
    return float(store.get_state(conn, week_key(day), 0.0) or 0.0)


def remaining(conn, day=None):
    return max(0.0, limit() - spent(conn, day))


def affordable(conn, day=None):
    """True while this ISO week still has budget left."""
    return spent(conn, day) < limit()


def charge(conn, usage, day=None):
    """Book one call against the week and return what it cost."""
    cost = None
    if isinstance(usage, dict):
        cost = usage.get("cost")
        if cost is None:
            cost = (usage.get("cost_details") or {}).get("upstream_inference_cost")
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        cost = UNKNOWN_CALL_USD
        log(f"budget: call reported no cost, booked {UNKNOWN_CALL_USD:.4f} USD")
    key = week_key(day)
    store.set_state(conn, key, round(spent(conn, day) + cost, 6))
    return cost
