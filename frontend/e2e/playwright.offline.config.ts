import { defineConfig, devices } from '@playwright/test';
import * as path from 'node:path';

/**
 * P4-OFF WS-6.3 — SW-shell lane (SEPARATE config).
 *
 * webServers are config-level and the main config already owns :4111, so the
 * service-worker lane needs its own config + port. It builds the PRODUCTION
 * configuration (real ngsw — NOT the `ct` build, which sets serviceWorker:false)
 * and serves it in SPA mode on :4173. Unlike every other lane it does NOT block
 * service workers and does NOT intercept the API — it asserts only that the app
 * shell boots from the SW cache when offline. Route interception + SWs interact
 * badly, so the offline-LOGIC assertions live in the SW-blocked main config.
 *
 * Run: `pnpm e2e:sw`. May be nightly-only if flaky (SW timing).
 */
const FRONTEND_DIR = path.join(__dirname, '..');
const isCI = !!process.env['CI'];
const PORT = 4173;
const BASE = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './sw-lane',
  outputDir: '../e2e-results/sw-artifacts',
  fullyParallel: false,
  workers: 1,
  retries: isCI ? 1 : 0,
  timeout: 60_000,
  reporter: [['list']],
  use: {
    baseURL: BASE,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    // Intentionally NOT blocking service workers — this lane exercises the SW.
  },
  projects: [{ name: 'sw-shell', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // Production build → real ngsw (no `ct` serviceWorker:false override).
    command: 'pnpm exec ng build && pnpm exec serve -s dist/frontend/browser -l 4173',
    cwd: FRONTEND_DIR,
    url: BASE,
    reuseExistingServer: !isCI,
    timeout: 180_000,
  },
});
