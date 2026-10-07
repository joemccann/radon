import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { auditOutputTraces } from './audit-output-traces.mjs';

async function fixture(t, files) {
  const root = await mkdtemp(join(tmpdir(), 'radon-alternate-trace-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const webRoot = join(root, 'web');
  const manifest = join(webRoot, '.next-tester-army/server/app/api/example/route.js.nft.json');
  await mkdir(dirname(manifest), { recursive: true });
  for (const [path, contents] of files) {
    await mkdir(dirname(join(root, path)), { recursive: true });
    await writeFile(join(root, path), contents);
  }
  await writeFile(manifest, JSON.stringify({ files: files.map(([path]) => relative(dirname(manifest), join(root, path))) }));
  return { webRoot, distDir: '.next-tester-army' };
}

test('alternate Next output directory preserves bounded route auditing', async t => {
  const options = await fixture(t, [['web/runtime.js', '12345']]);
  assert.equal((await auditOutputTraces(options)).manifestCount, 1);
  await assert.rejects(auditOutputTraces({ ...options, maxBytes: 4 }), /5 bytes/);
  await assert.rejects(auditOutputTraces({ ...options, maxFiles: 0 }), /1 files/);
});
for (const tree of ['db_backups', 'journal_archive']) {
  test(`alternate directory still rejects data/${tree}`, async t => {
    const options = await fixture(t, [[`data/${tree}/snapshot.bin`, 'archive']]);
    await assert.rejects(auditOutputTraces(options), new RegExp(`data/${tree}`));
  });
}

test('NEXT_DIST_DIR selects the same output directory as Next config', async t => {
  const { webRoot, distDir } = await fixture(t, [['web/runtime.js', 'runtime']]);
  const previous = process.env.NEXT_DIST_DIR;
  process.env.NEXT_DIST_DIR = distDir;
  try {
    assert.equal((await auditOutputTraces({ webRoot })).manifestCount, 1);
  } finally {
    if (previous === undefined) delete process.env.NEXT_DIST_DIR;
    else process.env.NEXT_DIST_DIR = previous;
  }
});
