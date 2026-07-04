import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  ANON_SCOPE,
  cacheKey,
  clearScope,
  currentScope,
  get,
  prune,
  put,
  SCHEMA_VERSION,
} from './api-cache';

/** Build a fake (unsigned) JWT carrying a `user_id` claim. */
function jwt(userId: number): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64({ user_id: userId })}.sig`;
}

function rec(key: string, savedAt = Date.now(), schemaVersion = SCHEMA_VERSION) {
  return { key, url: key, body: { ok: true }, status: 200, savedAt, schemaVersion };
}

/** The ng-test env's global localStorage (Node --localstorage-file) is broken;
 *  install a fresh in-memory Storage on window (what our code reads). */
function useMemoryLocalStorage(): void {
  const store = new Map<string, string>();
  const mock = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(window, 'localStorage', { value: mock, configurable: true });
}

describe('api-cache', () => {
  beforeEach(async () => {
    useMemoryLocalStorage();
    // wipe the whole DB between tests
    await clearScope('u1');
    await clearScope('u2');
    await clearScope(ANON_SCOPE);
  });

  it('currentScope reads the JWT user_id, else anon', () => {
    expect(currentScope()).toBe(ANON_SCOPE);
    window.localStorage.setItem('hf.access', jwt(42));
    expect(currentScope()).toBe('42');
  });

  it('put/get round-trips a record', async () => {
    const k = cacheKey('http://x/api/fund/', '42');
    await put(rec(k));
    const got = await get(k);
    expect(got?.body).toEqual({ ok: true });
  });

  it('drops a record whose schemaVersion mismatches on read', async () => {
    const k = cacheKey('http://x/api/fund/', '42');
    await put(rec(k, Date.now(), SCHEMA_VERSION + 1));
    expect(await get(k)).toBeNull();
  });

  it('clearScope isolates two JWT-derived scopes', async () => {
    const k1 = cacheKey('http://x/api/fund/', 'u1');
    const k2 = cacheKey('http://x/api/fund/', 'u2');
    await put(rec(k1));
    await put(rec(k2));
    await clearScope('u1');
    expect(await get(k1)).toBeNull();
    expect(await get(k2)).not.toBeNull();
  });

  it('clearScope(anon) removes anon residue', async () => {
    const kAnon = cacheKey('http://x/api/me/', ANON_SCOPE);
    await put(rec(kAnon));
    await clearScope(ANON_SCOPE);
    expect(await get(kAnon)).toBeNull();
  });

  it('prune caps to N entries, evicting oldest savedAt first', async () => {
    const now = Date.now();
    for (let i = 0; i < 5; i++) {
      await put(rec(cacheKey(`http://x/api/r${i}/`, 'u1'), now + i));
    }
    await prune(2);
    // the 3 oldest (r0,r1,r2) go; the 2 newest (r3,r4) stay
    expect(await get(cacheKey('http://x/api/r0/', 'u1'))).toBeNull();
    expect(await get(cacheKey('http://x/api/r4/', 'u1'))).not.toBeNull();
    expect(await get(cacheKey('http://x/api/r3/', 'u1'))).not.toBeNull();
  });
});
