"""REL-021b / R-034: execute relay cache code with fake disk, clocks and timers."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'ib_realtime_server.js'
HARNESS = r'''
import fs from 'node:fs';
import vm from 'node:vm';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.path, 'utf8');
const begin = source.indexOf('const CLOSE_CACHE_PATH =');
const end = source.indexOf('/* ─── Option session last', begin);
if (begin < 0 || end < begin) throw Error('cache source bounds missing');
let day = '2026-10-01';
let disk = JSON.stringify({'SPY_20260930_500_C': 1, 'SPY_20261001_500_C': 2,
                           'SPY_20261002_500_P': 3, 'SPY_20270115_500_C': 4});
const timers = [];
const context = vm.createContext({
  path: {resolve: () => 'fake-cache', dirname: () => 'fake-dir'},
  process: {cwd: () => '/fake'},
  fs: {existsSync: () => true, readFileSync: () => disk,
       writeFileSync: (_p, value) => { disk = value; }, mkdirSync(){}},
  console: {log(){}, warn(){}}, verbose(){},
  setTimeout: fn => {timers.push(fn); return timers.length;},
  etDateString: () => day,
});
vm.runInContext(source.slice(begin, end), context);
if (input.rollover) day = '2026-10-02';
if (!input.loadOnly) vm.runInContext(`
  const oldData = {symbol: 'SPY_20260930_500_C'};
  const boundaryData = {symbol: 'SPY_20261001_500_C'};
  applyCachedClose(oldData);
  applyCachedClose(boundaryData);
  updateOptionCloseCache('SPY_20260930_500_C', 9);
`, context);
for (let i = 0; i < timers.length; i++) timers[i]();
console.log(JSON.stringify({disk: JSON.parse(disk),
  memory: vm.runInContext('Object.fromEntries(optionCloseCache)', context),
  old: input.loadOnly ? {} : vm.runInContext('oldData', context), boundary: input.loadOnly ? {} : vm.runInContext('boundaryData', context)}));
'''


@pytest.mark.parametrize('rollover,load_only', [(False, False), (True, False), (False, True)])
def test_expired_closes_evicted_from_memory_and_disk(rollover, load_only):
    node = shutil.which('node')
    assert node, 'Node is required for isolated relay cache tests'
    result = subprocess.run([node, '--input-type=module', '-e', HARNESS],
                            input=json.dumps({'path': str(SOURCE), 'rollover': rollover, 'loadOnly': load_only}),
                            text=True, capture_output=True, check=True, timeout=10,
                            env={'PATH': str(Path(node).parent)})
    outcome = json.loads(result.stdout)
    expected = {'SPY_20261002_500_P': 3, 'SPY_20270115_500_C': 4}
    if not rollover:
        expected['SPY_20261001_500_C'] = 2
    assert outcome['memory'] == expected
    assert outcome['disk'] == expected
    assert 'close' not in outcome['old']
    if not load_only:
        assert outcome['boundary'].get('close') == (None if rollover else 2)
