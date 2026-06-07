/**
 * Runs once before the suite. Writes Playwright `storageState` with the JWT keys
 * owned by TokenStorage (`hf.access` / `hf.refresh`) so every authed project
 * starts logged-in with zero UI re-login (§2.5).
 *
 *   Lane A (mock): synthetic tokens — `GET /me/` is intercepted, so any
 *                  non-empty access token makes the guard pass.
 *   Lane B (live): a real request-based login against the seeded stack.
 */
import { type FullConfig, request } from '@playwright/test';
import * as fs from 'node:fs';
import { LANE, BASE_URL, API_BASE, LIVE_USER, TOKEN_KEYS, STORAGE_STATE } from './env';

async function liveTokens(): Promise<{ access: string; refresh: string }> {
  const ctx = await request.newContext({ baseURL: API_BASE });
  const res = await ctx.post('/api/auth/login/', { data: LIVE_USER });
  if (!res.ok()) {
    throw new Error(`[global-setup] Lane B login failed (${res.status()}). Did seed_e2e run?`);
  }
  const t = (await res.json()) as { access: string; refresh: string };
  await ctx.dispose();
  return { access: t.access, refresh: t.refresh };
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const tokens =
    LANE === 'live'
      ? await liveTokens()
      : { access: 'e2e.mock.access.token', refresh: 'e2e.mock.refresh.token' };

  const state = {
    cookies: [],
    origins: [
      {
        origin: new URL(BASE_URL).origin,
        localStorage: [
          { name: TOKEN_KEYS.access, value: tokens.access },
          { name: TOKEN_KEYS.refresh, value: tokens.refresh },
          { name: 'hf.theme', value: 'dark' },
        ],
      },
    ],
  };

  fs.writeFileSync(STORAGE_STATE, JSON.stringify(state, null, 2));
  // eslint-disable-next-line no-console
  console.log(`[global-setup] wrote ${STORAGE_STATE} (lane=${LANE})`);
}
