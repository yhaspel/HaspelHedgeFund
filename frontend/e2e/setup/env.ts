/**
 * Single source of truth for lane flags, base URLs, the frozen clock instant,
 * and the storage keys Playwright writes into `storageState`. Imported by the
 * config, the global setup, the mock layer, and the Page Objects.
 */
import * as path from 'node:path';

/** `mock` = Lane A (every-PR, intercepted /api/**). `live` = Lane B (seeded stack). */
export const LANE = (process.env['E2E_LANE'] ?? 'mock') as 'mock' | 'live';

/** The static prod bundle (Lane A) or the compose `frontend` service (Lane B). */
export const BASE_URL = process.env['E2E_BASE_URL'] ?? 'http://localhost:4111';

/**
 * Where Lane B reaches the real API for the request-based login in global setup.
 * The prod build talks to a relative `/api`, which the compose frontend proxies,
 * so we default to BASE_URL. Override for a split-origin dev box.
 */
export const API_BASE = process.env['E2E_API_BASE'] ?? BASE_URL;

/**
 * Frozen instant applied suite-wide (`page.clock.setFixedTime`) so relative
 * timestamps ("2h ago"), the news chyron, and "today" defaults are stable for
 * assertions and screenshots. Matches the seeded world's as-of date.
 */
export const FROZEN_TIME = new Date('2026-06-06T17:30:00.000Z');

/** The JWT keys owned by `core/auth/token-storage.ts` (TokenStorage). */
export const TOKEN_KEYS = { access: 'hf.access', refresh: 'hf.refresh' } as const;

/** Lane B credentials — must match `manage.py seed_e2e`. */
export const LIVE_USER = {
  email: process.env['E2E_EMAIL'] ?? 'e2e@local',
  password: process.env['E2E_PASSWORD'] ?? 'e2e-password-123',
};

/** Generated storageState location (git-ignored). */
export const STORAGE_STATE = path.join(__dirname, 'auth.storageState.json');
