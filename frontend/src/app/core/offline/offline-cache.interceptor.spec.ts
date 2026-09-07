import 'fake-indexeddb/auto';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../environments/environment';
import * as cache from './api-cache';
import { offlineCacheInterceptor } from './offline-cache.interceptor';
import { OfflineState } from './offline-state.service';

const API = environment.apiBaseUrl;
const tick = () => new Promise((r) => setTimeout(r, 25));

function jwt(userId: number): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64({ user_id: userId })}.sig`;
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

describe('offlineCacheInterceptor', () => {
  let http: HttpClient;
  let ctrl: HttpTestingController;
  let offline: OfflineState;

  beforeEach(async () => {
    useMemoryLocalStorage();
    window.localStorage.setItem('hf.access', jwt(7));
    await cache.clearScope('7');
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([offlineCacheInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    ctrl = TestBed.inject(HttpTestingController);
    offline = TestBed.inject(OfflineState);
  });

  it('caches a 2xx API GET body', async () => {
    http.get(`${API}/fund/`).subscribe();
    ctrl.expectOne(`${API}/fund/`).flush({ nav: 100 });
    await tick();
    const got = await cache.get(cache.cacheKey(`${API}/fund/`));
    expect(got?.body).toEqual({ nav: 100 });
  });

  it('replays cache on status 0 with X-HF-Cache: stale', async () => {
    await cache.put({
      key: cache.cacheKey(`${API}/fund/`),
      url: `${API}/fund/`,
      body: { nav: 42 },
      status: 200,
      savedAt: Date.now(),
      schemaVersion: cache.SCHEMA_VERSION,
    });
    let resp: unknown;
    http.get(`${API}/fund/`, { observe: 'response' }).subscribe((r) => (resp = r));
    ctrl.expectOne(`${API}/fund/`).error(new ProgressEvent('err'), { status: 0 });
    await tick();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const r = resp as any;
    expect(r.body).toEqual({ nav: 42 });
    expect(r.headers.get('X-HF-Cache')).toBe('stale');
    expect(r.headers.get('X-HF-Cached-At')).toBeTruthy();
  });

  it.each([503, 504])('replays cache on %i when already offline', async (status) => {
    offline.mode.set('offline-l2');
    await cache.put({
      key: cache.cacheKey(`${API}/x/`),
      url: `${API}/x/`,
      body: { v: 1 },
      status: 200,
      savedAt: Date.now(),
      schemaVersion: cache.SCHEMA_VERSION,
    });
    let ok = false;
    http.get(`${API}/x/`).subscribe(() => (ok = true));
    ctrl.expectOne(`${API}/x/`).flush(null, { status, statusText: 'x' });
    await tick();
    expect(ok).toBe(true);
  });

  it('does NOT serve stale-as-live on a per-route 504 while online', async () => {
    // Finding-5 safety: health is green (mode online) but one heavy route 504s —
    // propagate an honest error, never present stale cache as a fresh 200.
    offline.mode.set('online');
    await cache.put({
      key: cache.cacheKey(`${API}/x/`),
      url: `${API}/x/`,
      body: { v: 1 },
      status: 200,
      savedAt: Date.now(),
      schemaVersion: cache.SCHEMA_VERSION,
    });
    let errored = false;
    http.get(`${API}/x/`).subscribe({ error: () => (errored = true) });
    ctrl.expectOne(`${API}/x/`).flush(null, { status: 504, statusText: 'Gateway Timeout' });
    await tick();
    expect(errored).toBe(true);
  });

  it('does NOT replay cache on 401', async () => {
    await cache.put({
      key: cache.cacheKey(`${API}/me/`),
      url: `${API}/me/`,
      body: { stale: true },
      status: 200,
      savedAt: Date.now(),
      schemaVersion: cache.SCHEMA_VERSION,
    });
    let errored = false;
    http.get(`${API}/me/`).subscribe({ error: () => (errored = true) });
    ctrl.expectOne(`${API}/me/`).flush(null, { status: 401, statusText: 'Unauthorized' });
    await tick();
    expect(errored).toBe(true);
  });

  it('does NOT replay cache on 404/500', async () => {
    for (const status of [404, 500]) {
      let errored = false;
      http.get(`${API}/z/`).subscribe({ error: () => (errored = true) });
      ctrl.expectOne(`${API}/z/`).flush(null, { status, statusText: 'x' });
      await tick();
      expect(errored).toBe(true);
    }
  });

  it('honors the denylist: /auth/ and /health/ are never cached', async () => {
    http.get(`${API}/auth/refresh/`).subscribe();
    ctrl.expectOne(`${API}/auth/refresh/`).flush({ access: 'tok' });
    http.get(`${API}/health/`).subscribe();
    ctrl.expectOne(`${API}/health/`).flush({ status: 'ok' });
    await tick();
    expect(await cache.get(cache.cacheKey(`${API}/auth/refresh/`))).toBeNull();
    expect(await cache.get(cache.cacheKey(`${API}/health/`))).toBeNull();
  });

  it('leaves non-GET requests untouched', async () => {
    http.post(`${API}/fund/`, { a: 1 }).subscribe();
    ctrl.expectOne(`${API}/fund/`).flush({ ok: true });
    await tick();
    expect(await cache.get(cache.cacheKey(`${API}/fund/`))).toBeNull();
  });

  it('forced offline serves cache without any network call', async () => {
    offline.setForced(true);
    await cache.put({
      key: cache.cacheKey(`${API}/fund/`),
      url: `${API}/fund/`,
      body: { forced: true },
      status: 200,
      savedAt: Date.now(),
      schemaVersion: cache.SCHEMA_VERSION,
    });
    let body: unknown;
    http.get(`${API}/fund/`).subscribe((b) => (body = b));
    await tick();
    ctrl.expectNone(`${API}/fund/`); // no network touched
    expect(body).toEqual({ forced: true });
    offline.setForced(false);
  });
});
