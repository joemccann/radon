"""R-314 / REL-317: execute the actual browser request owner with fake I/O.

Native Node/tsx runs production TypeScript. No provider, database or browser
session is read; clocks and fetch are injected before the function is called.
Vitest remains CI work, as required by the nightly runner contract.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HARNESS = r'''
import assert from 'node:assert/strict';
import { requestAssistantTurn } from './lib/chat.ts';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const mode = process.argv[1];
const realSetTimeout = globalThis.setTimeout;
const created = new Set();
globalThis.setTimeout = (callback, ms, ...args) => {
  const id = realSetTimeout(callback, ms === 330000 ? 20 : ms, ...args);
  if (ms === 330000) created.add(id);
  return id;
};
const realClearTimeout = globalThis.clearTimeout;
globalThis.clearTimeout = id => { created.delete(id); realClearTimeout(id); };
const calls = [];
let cancelled = 0;
globalThis.fetch = async (url, init) => {
  calls.push({url, init});
  if (mode === 'headers') return new Promise((resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), {once: true});
  });
  if (mode === 'stream' || mode === 'done-without-eof') {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: start\ndata: {}\n\n'));
        if (mode === 'done-without-eof') controller.enqueue(new TextEncoder().encode(
          'event: done\ndata: {"content":"complete","proposal":null,"toolEvents":[]}\n\n'));
        init.signal?.addEventListener('abort', () => {
          cancelled++;
          try { controller.error(new DOMException('aborted', 'AbortError')); } catch {}
        }, {once: true});
      },
      cancel() { cancelled++; },
    });
    return new Response(body, {headers: {'Content-Type':'text/event-stream'}});
  }
  return new Response(JSON.stringify({content:'recovered', proposal:null, toolEvents:[]}),
                      {headers:{'Content-Type':'application/json'}});
};
if (mode.startsWith('server-')) {
  const source = fs.readFileSync('./app/api/assistant/route.ts', 'utf8');
  const chunk = source.slice(source.indexOf('export const MAX_MESSAGES_PER_TURN'), source.indexOf('function decodedBase64Bytes'));
  const context = {exports: {}, TextEncoder};
  const policy = './lib/assistant/requestBudget.ts';
  if (fs.existsSync(policy)) {
    const owned = {exports: {}, TextEncoder};
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(policy, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022}}).outputText, owned);
    Object.assign(context, owned.exports);
  }
  vm.runInNewContext(ts.transpileModule(chunk + '\nexport {turnPayloadViolation};', {compilerOptions:{module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022}}).outputText, context);
  const messages = mode === 'server-message'
    ? [{role:'user', content:'字'.repeat(12000)}]
    : Array.from({length:5}, () => ({role:'assistant', content:'x'.repeat(30000)}));
  assert.equal(context.exports.turnPayloadViolation(messages)?.status, 413, 'model admission accepted excessive text');
  console.log('result:' + JSON.stringify({calls:0}));
  process.exit(0);
}
const history = mode === 'history' ? Array.from({length:50}, (_, i) => ({role:'assistant', content:`turn-${i}:` + 'x'.repeat(5000)})) : [];
const prompt = mode === 'message' ? '字'.repeat(12000) : 'current prompt';
const pending = requestAssistantTurn(history, prompt);
const result = await Promise.race([pending, new Promise(resolve => realSetTimeout(() => resolve('unbounded'), 150))]);
assert.notEqual(result, 'unbounded', 'request owner never settled after its deadline/terminal frame');
if (mode === 'message') {
  assert.equal(result.failed, true);
  assert.equal(calls.length, 0, 'oversized current prompt reached the wire');
} else {
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/assistant');
  assert.equal(calls[0].init.method, 'POST');
  const messages = JSON.parse(calls[0].init.body).messages;
  assert.equal(messages.at(-1).content, prompt);
  if (mode === 'history') {
    assert(messages.length <= 40);
    assert(messages.reduce((n,m) => n + Buffer.byteLength(m.content), 0) <= 128000);
    assert(messages[0].content !== history[0].content);
    assert(messages.at(-2).content === history.at(-1).content);
  } else if (mode === 'done-without-eof') {
    assert.equal(result.content, 'complete');
  } else if (mode === 'headers' || mode === 'stream') {
    assert.equal(result.failed, true);
    assert.equal(result.proposal, null);
    assert.equal(calls[0].init.signal.aborted, true);
  } else {
    assert.equal(result.content, 'recovered');
  }
}
assert.equal(created.size, 0, 'owned deadline timer leaked after settlement');
console.log('result:' + JSON.stringify({failed:result.failed ?? false, calls:calls.length, cancelled}));
'''


@pytest.mark.parametrize('fault', ['headers', 'stream', 'done-without-eof', 'history', 'message', 'success', 'server-message', 'server-transcript'])
def test_request_bounds_and_recovery_are_enforced_at_the_wire(fault):
    node = shutil.which('node')
    assert node, 'native Node is required for the offline TypeScript acceptance'
    result = subprocess.run([node, '--import', 'tsx', '--input-type=module', '-e', HARNESS, fault],
                            cwd=ROOT / 'web', env={'PATH': str(Path(node).parent)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    output = next(line[7:] for line in result.stdout.splitlines() if line.startswith('result:'))
    assert json.loads(output)['calls'] <= 1
