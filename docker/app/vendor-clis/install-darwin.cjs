// Materialize locked native Darwin payloads without executing vendor installers.
// npm ci verifies the registry integrity before this cross-platform step.
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

function regular(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Expected regular package file');
  return fs.readFileSync(file);
}

function macho(bytes) {
  if (bytes.length < 8 || bytes.readUInt32LE(0) !== 0xfeedfacf || bytes.readUInt32LE(4) !== 0x0100000c) {
    throw new Error('Expected native Mach-O arm64 executable');
  }
  return bytes;
}

function materialize({sourceRoot = __dirname, helpersRoot, targetRoot = '/opt/radon-darwin'}) {
  if (!helpersRoot) throw new Error('Public helper directory is required');
  const packageBytes = regular(path.join(sourceRoot, 'package.json'));
  const pins = JSON.parse(packageBytes).dependencies;
  const locked = JSON.parse(regular(path.join(sourceRoot, 'package-lock.json'))).packages;
  const binaries = [
    ['claude', '@anthropic-ai/claude-code', '@anthropic-ai/claude-code-darwin-arm64', 'claude'],
    ['codex', '@openai/codex', '@openai/codex-darwin-arm64', 'vendor/aarch64-apple-darwin/bin/codex'],
    ['rg', '@openai/codex', '@openai/codex-darwin-arm64', 'vendor/aarch64-apple-darwin/codex-path/rg'],
    ['grok', '@xai-official/grok', '@xai-official/grok-darwin-arm64', 'bin/grok.br'],
  ];
  const files = new Map([['package.json', packageBytes]]);
  for (const [name, parent, pkg, relative] of binaries) {
    const pin = pins[parent];
    if (typeof pin !== 'string' || !/^\d+\.\d+\.\d+$/.test(pin)) throw new Error('Stable exact CLI pin is required');
    const expected = parent === '@openai/codex' ? `${pin}-darwin-arm64` : pin;
    const folder = path.join(sourceRoot, 'node_modules', pkg);
    const installed = JSON.parse(regular(path.join(folder, 'package.json')));
    if (installed.version !== expected || locked[`node_modules/${pkg}`]?.version !== expected) {
      throw new Error('Native package does not match the locked CLI pin');
    }
    let bytes = regular(path.join(folder, relative));
    if (name === 'grok') bytes = zlib.brotliDecompressSync(bytes, {maxOutputLength: 512 * 1024 * 1024});
    files.set(`bin/${name}`, macho(bytes));
  }
  for (const name of ['__init__.py', 'client.py', 'cli.py', 'cli_bridge.py']) {
    files.set(`model_gateway/${name}`, regular(path.join(helpersRoot, name)));
  }
  // Validate every input before creating a fresh payload. No node_modules,
  // runtime interpreter, provider backend or credential file enters the bundle.
  fs.mkdirSync(targetRoot, {mode: 0o755});
  fs.mkdirSync(path.join(targetRoot, 'bin'), {mode: 0o755});
  fs.mkdirSync(path.join(targetRoot, 'model_gateway'), {mode: 0o755});
  for (const [relative, bytes] of files) {
    fs.writeFileSync(path.join(targetRoot, relative), bytes, {flag: 'wx', mode: relative.startsWith('bin/') ? 0o755 : 0o644});
  }
}

module.exports = {materialize};
if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.length !== 4 || args[0] !== '--helpers' || args[2] !== '--output') {
    throw new Error('Usage: install-darwin.cjs --helpers DIR --output DIR');
  }
  materialize({helpersRoot: args[1], targetRoot: args[3]});
}
