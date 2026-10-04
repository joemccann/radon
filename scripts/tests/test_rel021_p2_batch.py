#!/usr/bin/env python3
"""REL-021 (partial) — P2 batch items with behavior changes
(RELIABILITY_AUDIT.md R-031, R-033, R-038, R-045, R-029-partial).

Each class pins one item; see RELIABILITY_LOG.md for the batch map.
"""

import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAtomicSaveFsync:
    """R-031: atomic_save must fsync file and directory so a power loss
    cannot persist the rename with unwritten content."""

    def test_fsync_called_on_file_and_directory(self, tmp_path, monkeypatch):
        from utils import atomic_io

        synced_fds = []
        real_fsync = os.fsync
        monkeypatch.setattr(atomic_io.os, "fsync", lambda fd: synced_fds.append(fd) or real_fsync(fd))

        atomic_io.atomic_save(str(tmp_path / "state.json"), {"a": 1})
        # At least two fsyncs: the temp file's fd and the directory fd.
        assert len(synced_fds) >= 2


class TestLegacyExitServiceDisabled:
    """R-038: the legacy standalone exit-order placer must refuse to start
    its daemon mode — two concurrent placers can double-place exits."""

    def test_run_daemon_refuses(self):
        """Bounded subprocess so the RED state (current code: infinite
        daemon loop) fails fast instead of hanging pytest."""
        import subprocess as sp

        proc = sp.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'scripts'); "
             "import exit_order_service; exit_order_service.run_daemon()"],
            cwd=str(Path(__file__).parent.parent.parent),
            capture_output=True, timeout=10, text=True,
        )
        assert proc.returncode != 0
        assert "monitor daemon" in (proc.stdout + proc.stderr).lower()


class TestDaemonStateCorruptionBackup:
    """R-045: a corrupt daemon_state.json must be preserved as .corrupt
    (forensics + fill-dedupe recovery), not silently blanked."""

    def test_corrupt_state_backed_up(self, tmp_path):
        from monitor_daemon.daemon import MonitorDaemon

        state_file = tmp_path / "daemon_state.json"
        state_file.write_text("{corrupt json")

        daemon = MonitorDaemon(state_file=state_file, respect_market_hours=False)
        daemon.load_state()

        backups = list(tmp_path.glob("daemon_state.json.corrupt*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "{corrupt json"

    def test_sigterm_saves_state(self, tmp_path):
        """Every deploy SIGTERMs the daemon; only KeyboardInterrupt used
        to reach the final save."""
        from monitor_daemon import daemon as daemon_mod

        daemon = daemon_mod.MonitorDaemon(
            state_file=tmp_path / "daemon_state.json", respect_market_hours=False
        )
        saved = []
        daemon.save_state = lambda: saved.append(True)

        daemon.install_signal_handlers()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)
        assert saved == [True]


class TestPriceCachePruneOnWrite:
    """R-033: the 500-file cap is only enforced by the shelved performance
    rebuild — the write path itself must prune."""

    def test_write_path_prunes(self, tmp_path, monkeypatch):
        from utils import price_cache

        stocks = tmp_path / "stocks"
        stocks.mkdir(parents=True)
        monkeypatch.setattr(price_cache, "STOCKS_DIR", stocks)
        prune = MagicMock()
        monkeypatch.setattr(price_cache, "prune_cache", prune)
        # R-174 made the write-path prune single-flight and rate-limited (it
        # runs inside the PARALLEL fetch path, which its own docstring
        # forbids). R-033's contract — an ordinary write maintains the cap —
        # is unchanged; the interval just has to be clear for this call.
        monkeypatch.setattr(price_cache, "_last_prune_at", 0.0)

        price_cache.write_cache(stocks, "AAPL_2026-01-01_2026-02-01",
                                {"2026-01-02": 100.0}, source="ib", ttl=900)
        prune.assert_called_once()


class TestExitOrderTimestampsUtc:
    """R-029 (partial): exit_orders wrote naive local timestamps into
    placed_at/written_at, breaking lexicographic-chronological ordering
    against the UTC-Z rows every other writer produces."""

    def test_update_journal_trade_writes_utc_z(self):
        sys.path.insert(0, str(Path(__file__).parent / "test_monitor_daemon"))
        from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
        from test_exit_orders import FakeJournalDb

        db = FakeJournalDb([
            {
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {"price": 15.0, "status": "PENDING", "order_id": None}
                },
            }
        ])
        handler = ExitOrdersHandler(db=db)
        ok = handler._update_journal_trade(8, "target", 99, "trade-8")
        assert ok
        placed_at = db.trades["trade-8"]["exit_orders"]["target"]["placed_at"]
        assert placed_at.endswith("Z") or "+00:00" in placed_at


class TestRelaySubscriptionAdmission:
    """R-036 / REL-021b: client quotas execute at the actual relay wire funnel."""

    def _run(self, case):
        import shutil
        import subprocess

        node = shutil.which("node")
        assert node, "offline relay fault injection requires Node"
        root = Path(__file__).resolve().parents[2]
        script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as limits from './scripts/lib/relayLimits.js';
const source = fs.readFileSync('./scripts/ib_realtime_server.js', 'utf8');
const cap = limits.MAX_CLIENT_SUBSCRIPTIONS ?? 32;
assert.ok(cap > 0 && cap <= 40);
function fn(name) {
  const match = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(source);
  assert.ok(match, name);
  const rest = source.slice(match.index + match[0].length);
  const next = /\n(?:async )?function \w+\(/.exec(rest);
  return source.slice(match.index, next ? match.index + match[0].length + next.index : undefined);
}
const wire = [], errors = [], stopped = [];
const clientSymbols = new Map(), symbolSubscribers = new Map(), symbolStates = new Map();
const context = {
  console, Date, Map, Set, MAX_CLIENT_SUBSCRIPTIONS: cap,
  clientSymbols, symbolSubscribers, symbolStates, requestIdToSymbol: new Map(),
  snapshotRequests: new Map(), clients: new Set(), clientOwnerIds: new Map(),
  clientLastPong: new Map(), clientDroppedFrames: new Map(),
  removeBatchBuffer: () => {}, clearTimeout: () => {},
  setTimeout: () => 1, SNAPSHOT_TIMEOUT_MS: 1000,
  createPriceData: symbol => ({ symbol }),
  nextRequestId: 0, ibConnected: true, DEPTH_ENABLED: false,
  DEPTH_FUTURES_SYMBOLS: new Set(['ES']), FORWARD_PRICED_INDICES: new Set(),
  fundamentalsStore: new Map(),
  ib: { reqMktData: (...args) => wire.push(args), cancelMktData: () => {} },
  parseActionMessage: data => ({ symbols: [], contracts: [], indexes: [], ...data }),
  stockContract: symbol => ({ symbol, secType: 'STK' }),
  optionContract: (symbol, expiry, strike, right) => ({ symbol, expiry, strike, right, secType: 'OPT' }),
  indexContract: symbol => ({ symbol, secType: 'IND' }),
  optionKey: c => `${c.symbol}:${c.expiry}:${c.strike}:${c.right}`,
  verbose: () => {}, nowIso: () => '2026-10-04T00:00:00Z',
  sendMessage: (client, message) => { if (message.type === 'error') errors.push([client, message]); },
  sendSubscribedConfirmation: () => {}, sendUnsubscribedConfirmation: () => {},
  safeInitialState: data => data, requestFundamentals: () => {},
  stopLiveSubscription: key => { stopped.push(key); symbolStates.delete(key); },
  releaseForwardsForSubject: () => {},
  farmStateAfterIdleDrain: () => null, lastFarmStateCode: null,
  ensureSymbolState: (key, contract) => {
    if (!symbolStates.has(key)) symbolStates.set(key, { tickerId: null, contract, data: {} });
    return symbolStates.get(key);
  },
};
vm.createContext(context);
vm.runInContext(['subscribeClientToSymbol', 'unsubscribeClientFromSymbol', 'startLiveSubscription', 'handleClientMessage', 'clearSnapshot', 'disconnectClient', 'handleSnapshotRequest'].map(fn).join('\n'), context);
const client = {}, other = {};
const stock = n => ({ action: 'subscribe', symbols: Array.from({ length: n }, (_, i) => `S${i}`) });
const options = n => ({ action: 'subscribe', contracts: Array.from({ length: n }, (_, i) => ({ symbol: 'SYN', expiry: '20261218', strike: i + 1, right: 'C' })) });
const indexes = n => ({ action: 'subscribe', indexes: Array.from({ length: n }, (_, i) => ({ symbol: `IDX${i}`, exchange: 'TEST' })) });
const scenario = process.argv[1];
if (scenario === 'queue-cap') {
  let pump;
  const RateLimiter = vm.runInNewContext(
    fs.readFileSync('./scripts/lib/rate-limiter.js', 'utf8').replace('export class', 'class') + '; RateLimiter',
    { setInterval: fn => { pump = fn; return 1; }, clearInterval: () => {} },
  );
  const limiter = new RateLimiter(50, { maxQueue: limits.MAX_SNAPSHOT_QUEUE });
  const pending = Array.from({ length: limits.MAX_SNAPSHOT_QUEUE }, (_, i) => limiter.submit(() => i));
  await assert.rejects(limiter.submit(() => { throw Error('overflow ran'); }), /queue full/);
  assert.equal(limiter.pending, limits.MAX_SNAPSHOT_QUEUE);
  while (limiter.pending) pump();
  assert.equal((await Promise.all(pending)).length, limits.MAX_SNAPSHOT_QUEUE);
  assert.equal(limiter.pending, 0);
  process.exit(0);
}
if (scenario === 'snapshot-cancel') {
  const queued = [];
  context.snapshotLimiter = { submit: fn => new Promise(resolve => queued.push({ fn, resolve })) };
  context.clients.add(client); context.clients.add(other);
  const departed = context.handleSnapshotRequest(client, ['GONE']);
  const remaining = context.handleSnapshotRequest(other, ['OTHER']);
  context.clients.delete(client);
  context.disconnectClient(client);
  for (const task of queued) { task.fn(); task.resolve(); }
  await Promise.all([departed, remaining]);
  assert.equal(wire.length, 1, 'cancelled queued snapshot reached broker');
  assert.deepEqual(wire[0].slice(1), [{ symbol: 'OTHER', secType: 'STK' }, '', true, false]);
  assert.equal(context.snapshotRequests.size, 1);
  assert.equal([...context.snapshotRequests.values()][0].client, other);
  process.exit(0);
}
if (['stocks', 'options', 'indexes'].includes(scenario)) {
  const data = { stocks: stock, options, indexes }[scenario](cap + 1);
  await context.handleClientMessage(client, data);
  assert.equal(clientSymbols.get(client).size, cap);
  assert.equal(wire.length, cap, 'overflow reached broker');
  assert.equal(symbolStates.size, cap, 'overflow allocated state');
  assert.equal(symbolSubscribers.size, cap, 'overflow registered subscriber');
  assert.equal(errors.length, 1);
  assert.equal(errors[0][1].code, 'SUBSCRIPTION_LIMIT');
  await context.handleClientMessage(other, { action: 'subscribe', symbols: ['OTHER'] });
  assert.equal(wire.length, cap + 1, 'one client exhausted another client');
  assert.equal(clientSymbols.get(other).size, 1);
  assert.deepEqual(wire.at(-1).slice(1), [{ symbol: 'OTHER', secType: 'STK' }, '233,165', false, false]);
} else if (scenario === 'messages-and-recovery') {
  await context.handleClientMessage(client, stock(cap));
  await context.handleClientMessage(client, options(1));
  assert.equal(wire.length, cap, 'another message bypassed quota');
  await context.handleClientMessage(client, { action: 'subscribe', symbols: ['S0'] });
  assert.equal(clientSymbols.get(client).size, cap, 'duplicate consumed quota');
  assert.equal(wire.length, cap + 1, 'existing subject cannot refresh at quota');
  await context.handleClientMessage(client, { action: 'unsubscribe', symbols: ['S0'] });
  await context.handleClientMessage(client, options(1));
  assert.equal(wire.length, cap + 2);
  assert.equal(clientSymbols.get(client).size, cap);
  assert.deepEqual(stopped, ['S0']);
} else if (scenario === 'offline') {
  context.ibConnected = false;
  await context.handleClientMessage(client, stock(cap));
  await context.handleClientMessage(client, indexes(1));
  assert.equal(wire.length, 0);
  assert.equal(clientSymbols.get(client).size, cap);
  assert.equal(symbolStates.size, cap, 'offline registration bypassed quota');
  assert.equal(errors.length, 1);
} else if (['concurrent-future', 'future-unsubscribed', 'future-disconnected'].includes(scenario)) {
  await context.handleClientMessage(client, stock(cap - 1));
  let release;
  context.resolveFuturesFrontMonth = () => new Promise(resolve => { release = resolve; });
  const pending = context.handleClientMessage(client, { action: 'subscribe', symbols: ['ES'] });
  if (scenario === 'future-unsubscribed') {
    await context.handleClientMessage(client, { action: 'unsubscribe', symbols: ['ES'] });
    await context.handleClientMessage(client, options(1));
    release({ symbol: 'ES', secType: 'FUT' });
    await pending;
    assert.equal(wire.length, cap, 'cancelled future spent another reserved line');
    assert.ok(!symbolStates.has('ES'));
    process.exit(0);
  }
  if (scenario === 'future-disconnected') {
    context.disconnectClient(client);
    release({ symbol: 'ES', secType: 'FUT' });
    await pending;
    assert.equal(wire.length, cap - 1, 'departed client started a broker line');
    assert.ok(!symbolStates.has('ES'));
    process.exit(0);
  }
  await context.handleClientMessage(client, options(1));
  assert.equal(clientSymbols.get(client).size, cap, 'await released subscription reservation');
  assert.equal(wire.length, cap - 1, 'concurrent overflow reached broker');
  release({ symbol: 'ES', secType: 'FUT' });
  await pending;
  assert.equal(wire.length, cap);
}
'''
        result = subprocess.run([node, "--input-type=module", "-e", script, case],
                                cwd=root, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("case", ["stocks", "options", "indexes", "messages-and-recovery", "offline", "concurrent-future", "future-unsubscribed", "future-disconnected", "queue-cap", "snapshot-cancel"])
    def test_subscription_quota_at_the_broker_wire(self, case):
        self._run(case)
