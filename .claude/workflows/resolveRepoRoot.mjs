// Resolve the radon clone root for native security-audit workflows.
// Order: RADON_REPO_ROOT, RADON_WEEKEND_REPO, git toplevel, process.cwd().
// An explicit env that is not a radon checkout fails closed.

import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const RADON_REPO_ROOT_ENV = 'RADON_REPO_ROOT'
export const RADON_WEEKEND_REPO_ENV = 'RADON_WEEKEND_REPO'

export function isRadonCheckout(dir) {
  if (!dir) return false
  const root = resolve(dir)
  const claude = join(root, 'CLAUDE.md')
  if (!existsSync(claude)) return false
  if (!/RADON/i.test(readFileSync(claude, 'utf8'))) return false
  if (!existsSync(join(root, 'scripts', 'evaluate.py'))) return false
  return existsSync(join(root, '.claude', 'workflows', 'security-audit.mjs'))
}

export function gitToplevel(cwd) {
  try {
    const result = spawnSync('git', ['rev-parse', '--show-toplevel'], {
      cwd,
      encoding: 'utf8',
      timeout: 5000,
    })
    if (result.status === 0) return String(result.stdout || '').trim()
  } catch {
    // git missing or not a repository
  }
  return ''
}

export function resolveRadonRepoRoot({
  env = process.env,
  cwd = process.cwd(),
  gitToplevelFn = gitToplevel,
} = {}) {
  const explicit = String(env[RADON_REPO_ROOT_ENV] || '').trim()
  const weekend = String(env[RADON_WEEKEND_REPO_ENV] || '').trim()
  if (explicit) return requireRadon(explicit, RADON_REPO_ROOT_ENV)
  if (weekend) return requireRadon(weekend, RADON_WEEKEND_REPO_ENV)
  const git = String(gitToplevelFn(cwd) || '').trim()
  if (git && isRadonCheckout(git)) return resolve(git)
  if (isRadonCheckout(cwd)) return resolve(cwd)
  throw new Error(
    `security-audit: resolved directory is not a radon checkout (cwd=${cwd}). ` +
      `Set ${RADON_REPO_ROOT_ENV} to the clone root.`,
  )
}

function requireRadon(path, source) {
  const resolved = resolve(path)
  if (isRadonCheckout(resolved)) return resolved
  throw new Error(
    `security-audit: ${source}=${resolved} is not a radon checkout. ` +
      `Set ${RADON_REPO_ROOT_ENV} to the clone root.`,
  )
}

const invoked = process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])
if (invoked && process.argv.includes('--print')) {
  try {
    process.stdout.write(`${resolveRadonRepoRoot()}\n`)
  } catch (err) {
    process.stderr.write(`${err && err.message ? err.message : err}\n`)
    process.exit(2)
  }
}
