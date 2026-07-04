import { defineConfig, devices } from '@playwright/test';
import * as path from 'node:path';
import { BASE_URL, LANE, STORAGE_STATE } from './setup/env';

const FRONTEND_DIR = path.join(__dirname, '..');
const isCI = !!process.env['CI'];

/**
 * Two-lane Playwright harness (ADR 0019). Lane A (mock) is the default and runs
 * `chromium` + `visual` + `axe` on every PR; the full browser/mobile matrix and
 * Lane B (`live-smoke`) run nightly. See e2e/README.md.
 */
export default defineConfig({
  testDir: './specs',
  outputDir: '../e2e-results/artifacts',
  snapshotPathTemplate: '{testDir}/../visual/__screenshots__/{projectName}/{testFilePath}/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: isCI,
  // CI shards + retries twice. Locally a single retry absorbs the occasional
  // timing flake from running the whole suite against one shared dev server.
  retries: isCI ? 2 : 1,
  // Cap local workers so the shared dev server on :4111 isn't overloaded (which
  // caused timing flakes); CI shards + uses 50% of its runners.
  workers: isCI ? '50%' : 4,
  timeout: 30_000,
  expect: {
    timeout: 7_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled' },
  },
  reporter: [
    ['list'],
    ['html', { outputFolder: '../e2e-results/html', open: 'never' }],
    ['junit', { outputFile: '../e2e-results/junit.xml' }],
    ['blob', { outputDir: '../e2e-results/blob' }],
  ],
  globalSetup: require.resolve('./setup/global-setup.ts'),
  globalTeardown: require.resolve('./setup/global-teardown.ts'),
  use: {
    baseURL: BASE_URL,
    storageState: STORAGE_STATE,
    testIdAttribute: 'data-testid',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // P4-OFF WS-2: the `ct` build sets serviceWorker:false, but block SWs at the
    // context level too so route interception never races a SW-mediated request.
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      testDir: './specs',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      testDir: './specs',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      testDir: './specs',
      use: { ...devices['Desktop Safari'] },
    },
    {
      // Mobile lanes run only the @mobile key-flow smoke (auth / dashboard /
      // runs / portfolio / screener / news render+nav). Dense surfaces (graph
      // editor, settings) await the v0.2 mobile-reflow pass — see README §5.4.
      name: 'mobile-chrome',
      testDir: './specs',
      grep: /@mobile/,
      use: { ...devices['Pixel 7'] },
    },
    {
      name: 'mobile-safari',
      testDir: './specs',
      grep: /@mobile/,
      use: { ...devices['iPhone 14'] },
    },
    {
      name: 'visual',
      testDir: './visual',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },
    {
      name: 'axe',
      testDir: './axe',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'live-smoke',
      testDir: './specs',
      grep: /@smoke/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Lane A (mock) owns its server: build the `ct` configuration (production + the
  // WS-18 /__ct harness route) and serve it in SPA mode. `serve -s` = single-page-app
  // fallback (every unknown path → index.html), which the Angular SPA needs for deep
  // links. (http-server's `-s` is *silent*, not SPA — it 404s deep links.)
  // Lane B (live) is served by the docker-compose `frontend` service the CI job brings
  // up on :4111, so Playwright must NOT start (or rebuild) a server there.
  webServer:
    LANE === 'live'
      ? undefined
      : {
          command:
            'pnpm exec ng build --configuration ct && pnpm exec serve -s dist/frontend/browser -l 4111',
          cwd: FRONTEND_DIR,
          url: BASE_URL,
          reuseExistingServer: !isCI,
          timeout: 180_000,
        },
});
