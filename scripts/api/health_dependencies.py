"""R-028 / REL-316: bounded, coarse storage/feed observations for health.

Read one relay heartbeat through API Hrana, never native libsql. Expose only
verdicts; raw errors, account identities and topology stay out of health/lite.
"""
import asyncio
import json
from datetime import datetime, timezone

from api import db_http
from utils.market_calendar import is_market_open_et

DEPENDENCY_HTTP_TIMEOUT_S = 0.25
DEPENDENCY_DEADLINE_S = 0.35
_RELAY_SQL = (
    "SELECT state, updated_at, last_error FROM service_health "
    "WHERE service = ? LIMIT 1"
)


def _probe_dependencies():
    rows = db_http.hrana_execute(_RELAY_SQL, ('ib-realtime-relay',),
                                 timeout=DEPENDENCY_HTTP_TIMEOUT_S)
    result = {'database': 'up', 'market_data': 'unknown'}
    if not rows:
        return result
    if not isinstance(rows[0], (tuple, list)) or len(rows[0]) != 3:
        return result
    state, stamp, raw_detail = rows[0]
    if state in ('error', 'paused'):
        result['market_data'] = 'degraded'
        return result
    if state != 'ok':
        return result
    try:
        updated = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if updated.tzinfo is None:
            return result
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        market_open = is_market_open_et()
        window = 300 if market_open else 86400
        if age < 0:
            return result
        if age > window:
            result['market_data'] = 'degraded'
            return result
        detail = json.loads(raw_detail) if raw_detail else {}
        if not isinstance(detail, dict):
            return result
        # Event-driven errors also carry an explicit reason. Never claim feed
        # recovery merely because a writer left an old error behind an ok row.
        if detail.get('reason') in ('farm_down', 'subscriptions_nulled', 'stale_ticks'):
            result['market_data'] = 'degraded'
            return result
        if not market_open or detail.get('subscribed_symbols') == 0:
            result['market_data'] = 'idle'
            return result
        tick = detail.get('last_tick_at')
        if not isinstance(tick, str):
            return result
        last_tick = datetime.fromisoformat(tick.replace('Z', '+00:00'))
        if last_tick.tzinfo is None:
            return result
        tick_age = (datetime.now(timezone.utc) - last_tick).total_seconds()
        if tick_age < 0:
            return result
        result['market_data'] = 'degraded' if tick_age > 300 else 'up'
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        pass  # DB is reachable, but malformed feed evidence remains unknown.
    return result


async def health_dependencies():
    try:
        return await asyncio.wait_for(asyncio.to_thread(_probe_dependencies),
                                      timeout=DEPENDENCY_DEADLINE_S)
    except (db_http.DbHttpError, TimeoutError):
        return {'database': 'down', 'market_data': 'unknown'}
