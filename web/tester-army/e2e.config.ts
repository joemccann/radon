import { randomUUID } from 'node:crypto';
import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
export const host = process.env.TESTER_ARMY_SERVER_MODE === 'start' ? '127.0.0.1' : 'localhost';
const port = process.env.TESTER_ARMY_PORT ?? '3341';
const token = process.env.RADON_AUTHLESS_TEST_TOKEN ?? randomUUID();
process.env.RADON_AUTHLESS_TEST_TOKEN = token;
const shared = globalThis as typeof globalThis & { __radonTesterEngine?: ReturnType<typeof web> };
export const engine = shared.__radonTesterEngine ??= web({ browser: 'chromium', headers: { 'x-radon-authless-test': token }, viewport: { width: 1440, height: 1000 } });
export default {
  projectId: 'radon-workstation', tests: 'tests/**/*.e2e.ts', workers: 1, retries: 0,
  timeout: 180000, actionTimeout: 30000, assertionTimeout: 30000,
  cache: 'off', trace: 'retain-on-failure', reporters: ['list', 'junit', 'markdown'],
  targets: [{ name: 'chromium', engine, app: {
    url: `http://${host}:${port}`, readyUrl: `http://${host}:${port}/manifest.webmanifest`,
    command: { executable: 'node', args: ['node_modules/next/dist/bin/next', process.env.TESTER_ARMY_SERVER_MODE === 'start' ? 'start' : 'dev', '--hostname', host, '-p', port],
      env: { TURSO_DB_URL: '', TURSO_AUTH_TOKEN: '', TURSO_DEMO_DB_URL: '', TURSO_DEMO_AUTH_TOKEN: '', RADON_API_URL: 'http://127.0.0.1:1', RADON_AUTHLESS_TEST: '1', RADON_AUTHLESS_TEST_TOKEN: token, NEXT_DIST_DIR: process.env.NEXT_DIST_DIR ?? '.next-tester-army', NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: 'pk_test_cmFkb24tZTJlLXN0dWIuY2xlcmsuYWNjb3VudHMuZGV2JA' }, // gitleaks:allow -- synthetic Clerk publishable identifier from the canonical Playwright fixture
      cwd: '..', startupTimeout: 180000, log: '.e2e/app.log' }
  } }],
} satisfies E2EConfig;
