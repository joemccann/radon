"""REL-021b / R-040: rejected positional depth cannot report a healthy feed."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
import fs from 'node:fs';
import vm from 'node:vm';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.root + '/scripts/ib_realtime_server.js', 'utf8');
const reducer = fs.readFileSync(input.root + '/scripts/lib/depthLadder.js', 'utf8').replace('export function', 'function');
function declaration(name) {
  const start = source.indexOf('function ' + name + '(');
  const end = source.indexOf('\n}', start);
  if (start < 0 || end < 0) throw Error('missing declaration ' + name);
  return source.slice(start, end + 2);
}
const callbacks = new Map(), requests = [], cancels = [], books = [], notices = [];
let ticks = 0, now = 100000;
const client = {};
const context = vm.createContext({
  DEPTH_ENABLED: true, DEPTH_NUM_ROWS_EQUITY: 40, DEPTH_NUM_ROWS_FUTURES: 10,
  MAX_CONCURRENT_DEPTH: 3, nextRequestId: 0, ibConnected: true, ibClientGeneration: 1,
  symbolDepthStates: new Map(), depthRequestIdToSymbol: new Map(),
  depthSubscribers: new Map([['TEST', new Set([client])]]),
  clientDepthBuffers: new Map([[client, new Map([['TEST', {stale: true}], ['OTHER', {valid: true}]])]]),
  Date: {now: () => now}, performance: {now: () => now},
  ib: {on: (name, fn) => callbacks.set(name, fn),
    reqMktDepth: (...args) => requests.push(args),
    cancelMktDepth: (...args) => {cancels.push(args); if (input.cancelFails) throw Error('cancel failed');}},
  EventName: new Proxy({}, {get: (_, key) => key}),
  collectActiveDepthTickets: () => [], planDepthAdmission: () => ({admit: true, evictKeys: []}),
  hydrateAndBroadcastDepth: key => books.push(key),
  emitDepthUnavailable: (...args) => notices.push(args),
  markTick: () => ticks++, verbose(){}, console: {warn(){}, error(){}},
});
vm.runInContext(reducer + '\n' + ['wireIBEvents','startDepthSubscription','applyDepthDelta'].map(declaration).join('\n') +
  '\nwireIBEvents(); startDepthSubscription("TEST", {symbol:"TEST"}, {kind:"stock", isFutures:false});', context);
const event = callbacks.get(input.l2 ? 'updateMktDepthL2' : 'updateMktDepth');
const send = (id, position, operation = 1) => input.l2
  ? event(id, position, 'TESTEX', operation, 1, 100, 1, true)
  : event(id, position, operation, 1, 100, 1);
send(1, 5); // Missing prior inserts: this positional update cannot be applied.
const staleBuffered = context.clientDepthBuffers.get(client).has('TEST');
const otherBuffered = context.clientDepthBuffers.get(client).has('OTHER');
const first = {ticks, requests: requests.length, cancels: cancels.length, books: books.length};
for (let i = 0; i < 100; i++) send(requests.length, 5);
const burst = {ticks, requests: requests.length, cancels: cancels.length, books: books.length};
now += 30001;
send(requests.length, 5);
const recoveredId = requests.length;
send(recoveredId, 0, 0); // A new book starts from a valid insert.
send(1, 0, 0); // Late event for the invalidated ticket must be ignored.
console.log(JSON.stringify({first, burst, ticks, requests, cancels, books, notices, staleBuffered, otherBuffered}));
'''


@pytest.mark.parametrize('l2', [False, True])
@pytest.mark.parametrize('cancel_fails', [False, True])
def test_desync_does_not_refresh_health_and_recovery_is_bounded(l2, cancel_fails):
    node = shutil.which('node')
    assert node, 'Node required for isolated relay fault injection'
    result = subprocess.run([node, '--input-type=module', '-e', HARNESS],
                            input=json.dumps({'root': str(ROOT), 'l2': l2, 'cancelFails': cancel_fails}),
                            text=True, capture_output=True, timeout=10, check=True,
                            env={'PATH': str(Path(node).parent)})
    outcome = json.loads(result.stdout)
    assert outcome['staleBuffered'] is False
    assert outcome['otherBuffered'] is True
    assert outcome['first']['ticks'] == 0
    assert outcome['first']['books'] == 0
    assert outcome['first']['cancels'] == 1
    assert outcome['burst'] == outcome['first']
    assert outcome['notices'][0] == ['TEST', 'desynchronized']
    if cancel_fails:
        assert len(outcome['requests']) == 1, 'unknown cancellation cannot allocate another ticket'
        assert outcome['ticks'] == 0
        assert outcome['books'] == []
    else:
        assert len(outcome['requests']) == 3
        assert len(outcome['cancels']) == 2
        assert outcome['ticks'] == 1
        assert outcome['books'] == ['TEST']
