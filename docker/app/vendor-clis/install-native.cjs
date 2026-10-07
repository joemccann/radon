// Materialize only the lockfile-verified native payloads; never run npm hooks.
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

if (process.platform !== 'linux' || !['x64', 'arm64'].includes(process.arch)) {
  throw new Error(`Unsupported trusted CLI platform: ${process.platform}/${process.arch}`);
}
const binDir = path.join(__dirname, 'bin');
fs.mkdirSync(binDir, { recursive: true, mode: 0o755 });
function packageDir(name) {
  return path.dirname(require.resolve(`${name}/package.json`));
}
const claude = path.join(packageDir(`@anthropic-ai/claude-code-linux-${process.arch}`), 'claude');
fs.copyFileSync(claude, path.join(binDir, 'claude'));
fs.chmodSync(path.join(binDir, 'claude'), 0o755);
const grok = path.join(packageDir(`@xai-official/grok-linux-${process.arch}`), 'bin', 'grok.br');
fs.writeFileSync(path.join(binDir, 'grok'), zlib.brotliDecompressSync(fs.readFileSync(grok)), { mode: 0o755 });
// Preserve Codex's launcher, which locates its locked optional native package
// and adds the bundled resource tools to PATH.
fs.symlinkSync('../node_modules/.bin/codex', path.join(binDir, 'codex'));
