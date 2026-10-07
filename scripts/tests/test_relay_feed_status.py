"""NF-3 / REL-318: actual relay status and cadence with fake subscribers/I/O."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as policy from './scripts/lib/staleDataMachine.js';
const mode = process.argv[1];
const source = fs.readFileSync('./scripts/ib_realtime_server.js','utf8');
const now = 200000;
const wire = [];
const state = {tickerId:1, lastTickAt:mode === 'fresh' ? now : now - 60000};
const subscribers = new Map([['AAPL',new Set(['client'])]]);
const context = {...policy, Date:{now:()=>now}, symbolSubscribers:subscribers,
  symbolStates:new Map([['AAPL',state]]), sendMessage:(client,payload)=>wire.push(payload),
  ibConnected:true, ibConnectionIssue:null, operatorHoldActive:mode==='held',
  isUSMarketHours:()=>mode!=='closed', clients:new Set(['client']), lastMarketDataDegraded:null};
if (mode==='nulled') state.tickerId=null;
if (mode==='idle') subscribers.clear();
const begin=source.indexOf('function sendStatus(client)');
const end=source.indexOf('\nfunction clearSnapshot',begin);
vm.runInNewContext(source.slice(begin,end), context);
context.sendStatus('client');
assert.equal(wire.length,1);
assert.equal(wire[0].ib_connected,true);
assert.equal(wire[0].market_data_degraded,['stale','nulled','held','cadence'].includes(mode));
if (mode==='cadence') {
  wire.length=0;
  let tick;
  context.setInterval=(fn,ms)=>{assert.equal(ms,5000);tick=fn;return 1;};
  const first=source.indexOf('statusBroadcastTick = setInterval');
  const last=source.indexOf('\n/*',first);
  vm.runInNewContext(source.slice(first,last),context);
  tick();
  assert.equal(wire.length,1,'connected stale transition was never broadcast');
  assert.equal(wire[0].market_data_degraded,true);
  tick();assert.equal(wire.length,1,'unchanged status unnecessarily rebroadcast');
  state.lastTickAt=now;
  tick();assert.equal(wire.length,2,'recovery status was never broadcast');
  assert.equal(wire[1].market_data_degraded,false);
}
console.log(JSON.stringify({calls:wire.length}));
'''


@pytest.mark.parametrize('mode', ['stale', 'fresh', 'nulled', 'held', 'closed', 'idle', 'cadence'])
def test_socket_liveness_does_not_hide_data_plane_faults(mode):
    node = shutil.which('node')
    assert node, 'offline relay fault injection needs native Node'
    run = subprocess.run([node, '--input-type=module', '-e', HARNESS, mode], cwd=ROOT,
                         env={'PATH': str(Path(node).parent)}, capture_output=True,
                         text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)['calls'] in (1, 2)
