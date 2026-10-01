"""REL-052 / NF-6: exercise the real mirror with isolated JS dependency stubs.

Node's module linker prevents any database import or connection. No npm install,
credentials, live database, or Vitest invocation is required.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'db' / 'mirror_market_snapshots_to_demo.js'

HARNESS = r'''
import fs from 'node:fs';
import vm from 'node:vm';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({
  console: {log(){}, warn(){}, error(){}},
  process: {argv: [], env: {}, pid: 1}, TypeError, Error, setTimeout,
});
const mod = new vm.SourceTextModule(fs.readFileSync(input.path, 'utf8'), {context});
await mod.link(async name => {
  const exports = name === 'node:path' ? {resolve}
    : name === 'node:url' ? {pathToFileURL}
    : name === '@libsql/client' ? {createClient(){throw Error('forbidden database client')}}
    : name === './writer.js' ? {recordServiceHealth(){throw Error('forbidden health write')}}
    : null;
  if (!exports) throw Error('unexpected dependency ' + name);
  return new vm.SyntheticModule(Object.keys(exports), function() {
    for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
  }, {context});
});
await mod.evaluate();
const error = input.transport ? new TypeError('fetch failed')
  : new TypeError("Cannot read properties of undefined (reading 'rows')");
const counts = {purge: 0, read: 0, write: 0};
const delays = [], events = [];
const act = (phase, result) => {
  counts[phase]++;
  if (phase === input.phase && (!input.recover || counts[phase] < 3)) throw error;
  return result;
};
let failure = null;
try {
  await mod.namespace.runMarketMirror({
    src: {execute: async () => act('read', {columns:['scan_time'], rows:[{scan_time:'fixture'}]})},
    dst: {execute: async () => act('purge', {}), batch: async () => act('write', {})},
    tables: {latestOne:[{table:'fixture', orderCol:'scan_time'}], perKey:[], history:[],
             purgedAccountTables:['private_fixture']},
    sleep: async delay => delays.push(delay), log: event => events.push(event),
    now: () => 'fixture', runId:'fixture', maxAttempts:3,
  });
} catch (caught) { failure = caught.message; }
console.log(JSON.stringify({counts, delays, events, failure}));
'''


@pytest.mark.parametrize('phase', ['purge', 'read', 'write'])
@pytest.mark.parametrize('transport,recover', [(False, False), (True, False), (True, True)])
def test_retry_only_transport_failures(phase, transport, recover):
    node = shutil.which('node')
    assert node, 'Node is required for this isolated JavaScript fault test'
    result = subprocess.run(
        [node, '--experimental-vm-modules', '--input-type=module', '-e', HARNESS],
        input=json.dumps({'path': str(MODULE), 'phase': phase,
                          'transport': transport, 'recover': recover}),
        text=True, capture_output=True, timeout=15, check=True,
        env={'PATH': str(Path(node).parent)},
    )
    outcome = json.loads(result.stdout)
    assert outcome['counts'][phase] == (3 if transport else 1)
    assert outcome['delays'] == ([500, 2000] if transport else [])
    assert bool(outcome['failure']) is (not recover)
    if phase == 'purge' and not recover:
        assert outcome['counts']['read'] == outcome['counts']['write'] == 0
    if phase == 'read' and not recover:
        assert outcome['counts']['write'] == 0
    events = [e['event'] for e in outcome['events']]
    assert events == (['retry', 'retry'] + ([] if recover else ['failed'])
                      if transport else ['failed'])
