#!/usr/bin/env python3.13
"""Personal-use VIX model spread from official same-session Cboe closes.

Cboe is the canonical historical anchor: COR1M IB history has documented lag,
VIXEQ IB coverage is unknown, and the configured UW account lacks Cboe index
approval. A bounded pooled IB availability probe runs each cycle before the
specialized official feed. Never replace VIXEQ with a proxy. See the indicator
spec for source permissions and the distinction between 20/252-session bands.
"""
from __future__ import annotations

import argparse
import csv
import io
import hashlib
import json
import math
import os
import statistics
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_DIR / '.env')
    load_dotenv(_PROJECT_DIR / 'web' / '.env')
except ImportError:
    pass

from clients.cboe_client import CboeClient
from db import writer
from db.hrana_http import HRANA_TIMEOUT_S, hrana_execute, hrana_query
from utils.atomic_io import atomic_save, verified_load
from utils.market_calendar import last_completed_session_date
from utils.ib_preflight import ib_auth_state

SERVICE = "vix-model-spread"
VIX_MODEL_SPREAD_JSON = _PROJECT_DIR / 'data' / 'vix-model-spread.json'
SYMBOLS = ('VIX', 'VIXEQ', 'COR1M')
_SNAPSHOT_CHUNK_CHARS = 64 * 1024
_SNAPSHOT_STAGING_SERVICE = f'{SERVICE}:staging'
_SNAPSHOT_PERSIST_BUDGET_S = 180.0


def _validated(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    by_date = {}
    for row in rows:
        try:
            day = row['date']
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                continue
            raw = row['value']
            if isinstance(raw, bool):
                continue
            value = float(raw)
            if not math.isfinite(value):
                continue
            if (symbol == 'COR1M' and not 0 <= value <= 100) or (symbol != 'COR1M' and value <= 0):
                continue
            by_date[day] = {'date': day, 'value': value}
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return [by_date[day] for day in sorted(by_date)]


def parse_index_csv(text: str, symbol: str) -> list[dict[str, Any]]:
    """Parse the actual Cboe two-column VIXEQ or OHLC VIX/COR1M schema."""
    if symbol not in SYMBOLS:
        raise ValueError(f'Unsupported index: {symbol}')
    rows = []
    column = 'VIXEQ' if symbol == 'VIXEQ' else 'CLOSE'
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not {'DATE', column}.issubset(reader.fieldnames):
        raise ValueError(f'{symbol} CSV is missing DATE/{column}')
    for raw in reader:
        try:
            day = datetime.strptime(raw['DATE'].strip(), '%m/%d/%Y').date().isoformat()
            rows.append({'date': day, 'value': raw[column]})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return _validated(rows, symbol)


def compute_series(vix_rows, vixeq_rows, cor_rows) -> list[dict[str, Any]]:
    """Inner join dates; trailing inclusive population statistics, no filling."""
    maps = [{r['date']: r['value'] for r in _validated(rows, symbol)}
            for rows, symbol in zip((vix_rows, vixeq_rows, cor_rows), SYMBOLS)]
    series = []
    spreads = []
    for day in sorted(set(maps[0]) & set(maps[1]) & set(maps[2])):
        vix, vixeq, cor1m = (values[day] for values in maps)
        model = math.sqrt(cor1m / 100) * vixeq
        spread = vix - model
        if not all(math.isfinite(value) for value in (model, spread)):
            raise ValueError('Nonfinite derived VIX model observation')
        row = dict(date=day, vix=vix, vixeq=vixeq, cor1m=cor1m, model_vix=model, spread=spread)
        spreads.append(spread)
        for window in (20, 252):
            stats = dict.fromkeys(('mean', 'std', 'upper', 'lower', 'z'))
            if len(spreads) >= window:
                values = spreads[-window:]
                mean = statistics.mean(values)
                sd = statistics.pstdev(values)
                stats = dict(mean=mean, std=sd, upper=mean + 2 * sd, lower=mean - 2 * sd,
                             z=(spread - mean) / sd if sd > 0 else None)
                if any(value is not None and not math.isfinite(value) for value in stats.values()):
                    raise ValueError('Nonfinite VIX model rolling statistic')
            row.update({f'{name}_{window}': value for name, value in stats.items()})
        series.append(row)
    return series


def build_output(series, scan_time, source_last_modified) -> dict[str, Any]:
    expected = last_completed_session_date(datetime.fromisoformat(scan_time.replace('Z', '+00:00')))
    latest = series[-1]['date'] if series else None
    return dict(scan_time=scan_time, source_last_modified=source_last_modified,
                source='cboe', status='ok' if latest and latest >= expected else 'stale_source',
                data_date=latest, as_of=latest, expected_session=expected,
                source_dates=dict.fromkeys(SYMBOLS, latest), count=len(series), current=series[-1] if series else None, series=series)


def _record_error(scan_time, exc):
    try:
        writer.ensure_no_replica_for_writers()
        writer.record_service_health(SERVICE, 'error', finished_at=scan_time,
                                     error={'class': 'vix_model_spread_failed', 'message': str(exc)})
    except Exception as health_exc:
        print(f'[{SERVICE}] error heartbeat failed: {health_exc}', file=sys.stderr)


def _persist_snapshot(payload):
    """Publish exact full history atomically without a multi-megabyte request.

    Every intermediate row is private to a UUID under a separate service.
    Canonical readers see only the final, complete JSON. JSON strings are ASCII
    escaped, so 64 KiB pieces remain below 256 KiB even after wire escaping.
    One-day orphan expiry is longer than the 300-second supervised collector.
    """
    serialized = json.dumps(payload, allow_nan=False, separators=(',', ':'))
    if len(serialized) <= _SNAPSHOT_CHUNK_CHARS:
        writer.upsert_scan_snapshot(SERVICE, payload['scan_time'], payload)
        return
    started = datetime.now(timezone.utc)
    stage = f"{started.isoformat()}:{uuid.uuid4().hex}"
    cutoff = (started - timedelta(days=1)).isoformat()
    deadline = time.monotonic() + _SNAPSHOT_PERSIST_BUDGET_S

    def bounded(call, sql, args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('VIX model snapshot persistence budget exhausted')
        return call(sql, args, timeout=min(HRANA_TIMEOUT_S, remaining))

    failed = True
    try:
        bounded(hrana_execute, 'DELETE FROM scan_snapshots WHERE service = ? AND scan_time < ?',
                (_SNAPSHOT_STAGING_SERVICE, cutoff))
        bounded(hrana_execute, 'INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)',
                (_SNAPSHOT_STAGING_SERVICE, stage, '{"chunks":[]}'))
        chunks = [serialized[i:i + _SNAPSHOT_CHUNK_CHARS]
                  for i in range(0, len(serialized), _SNAPSHOT_CHUNK_CHARS)]
        for index, chunk in enumerate(chunks):
            bounded(hrana_execute,
                    "UPDATE scan_snapshots SET payload = json_insert(payload, '$.chunks[#]', ?) "
                    "WHERE service = ? AND scan_time = ? AND json_array_length(payload, '$.chunks') = ?",
                    (chunk, _SNAPSHOT_STAGING_SERVICE, stage, index))
        receipt = bounded(hrana_query,
                "WITH assembled(payload) AS (SELECT group_concat(value, '') FROM "
                "(SELECT value FROM scan_snapshots, json_each(scan_snapshots.payload, '$.chunks') "
                "WHERE service = ? AND scan_time = ? ORDER BY CAST(json_each.key AS INTEGER))) "
                "INSERT OR REPLACE INTO scan_snapshots (service, scan_time, payload) "
                "SELECT ?, ?, payload FROM assembled WHERE json_valid(payload) AND length(payload) = ? "
                "RETURNING length(payload)",
                (_SNAPSHOT_STAGING_SERVICE, stage, SERVICE, payload['scan_time'], len(serialized)))
        if (len(receipt) != 1 or len(receipt[0]) != 1
                or str(receipt[0][0]) != str(len(serialized))):
            raise ValueError('VIX model snapshot assembly did not publish a complete document')
        bounded(hrana_execute,
                'DELETE FROM scan_snapshots WHERE service = ? AND scan_time NOT IN '
                '(SELECT scan_time FROM scan_snapshots WHERE service = ? ORDER BY scan_time DESC LIMIT ?)',
                (SERVICE, SERVICE, writer.SCAN_SNAPSHOT_KEEP))
        failed = False
    finally:
        try:
            # Give cleanup its own bounded attempt even after the work budget.
            hrana_execute('DELETE FROM scan_snapshots WHERE service = ? AND scan_time = ?',
                          (_SNAPSHOT_STAGING_SERVICE, stage), timeout=HRANA_TIMEOUT_S)
        except Exception as exc:
            if not failed:
                raise
            print(f'[{SERVICE}] staging cleanup failed: {type(exc).__name__}', file=sys.stderr)


def persist_result(payload, rows_changed=True):
    """Turso first, atomic disk fallback; DB failure remains an error exit."""
    series = payload.get('series')
    if (not isinstance(series, list) or not series or not isinstance(series[-1], dict)
            or payload.get('current') != series[-1] or payload.get('count') != len(series)
            or payload.get('data_date') != series[-1].get('date')):
        raise ValueError('Refusing empty or inconsistent VIX model snapshot')
    json.dumps(payload, allow_nan=False)
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_vix_model_spread_rows(payload['series'], recorded_at=payload['scan_time'])
        _persist_snapshot(payload)
        writer.record_service_health(SERVICE, 'ok', finished_at=payload['scan_time'])
    except Exception:
        # Keep an independently useful atomic fallback when Turso is unreachable.
        atomic_save(str(VIX_MODEL_SPREAD_JSON), payload)
        raise
    atomic_save(str(VIX_MODEL_SPREAD_JSON), payload)


def _read_disk_previous():
    try:
        payload = verified_load(str(VIX_MODEL_SPREAD_JSON))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _series_hash(series):
    return hashlib.sha256(json.dumps(series, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_previous():
    """Turso first, bounded individual source reads; atomic disk on failure.

    A full derived snapshot exceeds Hrana's 1MiB response bound. Retrieve its
    small metadata plus each raw input independently, and compare the stored
    derived-series hash rather than downloading the derived history again.
    """
    try:
        rows = hrana_query(
            "SELECT scan_time, json_extract(payload, '$.source_last_modified'), "
            "json_extract(payload, '$.source_dates'), json_extract(payload, '$.series_hash') "
            "FROM scan_snapshots WHERE service = ? ORDER BY scan_time DESC LIMIT 1", (SERVICE,))
        if rows:
            stamp, modified, dates, fingerprint = rows[0]
            sources = {}
            for symbol in SYMBOLS:
                raw = hrana_query(
                    "SELECT json_extract(payload, ?) FROM scan_snapshots "
                    "WHERE service = ? AND scan_time = ? LIMIT 1",
                    (f'$.source_series.{symbol}', SERVICE, stamp))
                sources[symbol] = json.loads(raw[0][0]) if raw and raw[0][0] else []
                if not _validated(sources[symbol], symbol):
                    raise ValueError('Stored VIX model source series is unavailable')
            return {'scan_time': stamp, 'source_last_modified': json.loads(modified or '{}'),
                    'source_dates': json.loads(dates or '{}'), 'series_hash': fingerprint,
                    'source_series': sources}
    except Exception as exc:
        print(f'[{SERVICE}] prior snapshot unavailable: {type(exc).__name__}', file=sys.stderr)
    return _read_disk_previous()


def _probe_ib():
    """Check coverage through the existing pool, never open a new IB socket."""
    import requests
    state = ib_auth_state()
    if state and state != 'authenticated':
        return {'status': 'skipped', 'auth_state': state}
    try:
        headers = {'User-Agent': 'radon/2.0'}
        if os.environ.get('MDW_API_KEY'):
            headers['X-API-Key'] = os.environ['MDW_API_KEY']
        response = requests.post('http://127.0.0.1:8321/historical/bars', headers=headers,
                                 json={'contract': {'sec_type': 'IND', 'symbol': 'VIXEQ',
                                                    'exchange': 'CBOE', 'currency': 'USD'},
                                       'duration': '2 D', 'bar_size': '1 day'}, timeout=15)
        response.raise_for_status()
        bars = response.json().get('bars')
        if not isinstance(bars, list):
            return {'status': 'unavailable'}
        valid = _validated([{'date': bar.get('date'), 'value': bar.get('close')}
                            for bar in bars if isinstance(bar, dict)], 'VIXEQ')
        return {'status': 'available' if valid else 'empty', 'count': len(valid)}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # No upstream body, URL, account identifier or credential in diagnostics.
        return {'status': 'unavailable'}


def run(client=None, previous=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    owned_client = client is None
    client = None if owned_client else client
    try:
        probe = _probe_ib()
        if owned_client:
            client = CboeClient()
        previous = previous if previous is not None else _read_previous()
        previous = previous or {}
        stamps = previous.get('source_last_modified') or {}
        retained = previous.get('source_series') or {}
        raw_sources, new_stamps = {}, {}
        for symbol in SYMBOLS:
            text, stamp = client.fetch_history(symbol, if_modified_since=stamps.get(symbol))
            raw = _validated(retained.get(symbol) or [], symbol) if text is None else parse_index_csv(text, symbol)
            if text is None and not raw:
                text, stamp = client.fetch_history(symbol)
                if text is None:
                    raise ValueError(f'{symbol} returned 304 without retained source rows')
                raw = parse_index_csv(text, symbol)
            if not raw:
                raise ValueError(f'{symbol} source has no valid rows')
            if raw[-1]['date'] > last_completed_session_date(now):
                raise ValueError(f'{symbol} source date is beyond the last completed session')
            old_date = (previous.get('source_dates') or {}).get(symbol)
            if old_date and raw[-1]['date'] < old_date:
                raise ValueError(f'{symbol} source date regressed')
            raw_sources[symbol] = raw
            new_stamps[symbol] = stamp
        series = compute_series(*(raw_sources[symbol] for symbol in SYMBOLS))
        if not series:
            raise ValueError('No aligned VIX model observations')
        payload = build_output(series, scan_time, new_stamps)
        payload['source_dates'] = {s: rows[-1]['date'] for s, rows in raw_sources.items()}
        payload['source_series'] = raw_sources
        payload['ib_availability'] = probe
        payload['series_hash'] = _series_hash(series)
        prior_hash = previous.get('series_hash')
        changed = payload['series_hash'] != prior_hash if prior_hash else series != previous.get('series')
        persist_result(payload, rows_changed=changed)
        return payload
    except Exception as exc:
        _record_error(scan_time, exc)
        raise
    finally:
        if owned_client and client is not None:
            client.close()


def main():
    parser = argparse.ArgumentParser(description='Personal-use VIX model spread')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        payload = run()
    except Exception as exc:
        print(f'[{SERVICE}] {exc}', file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, allow_nan=False))
    else:
        print(f"VIX MODEL: {payload['data_date']} spread {payload['current']['spread']:.2f}; "
              f"Data Freshness: {payload['status']} (expected {payload['expected_session']})", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
