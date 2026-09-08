/**
 * Review (fecore) — the last-known-value cache has no age bound and the
 * status-0 branch replays it while OfflineState still says "online" (the
 * banner — the only staleness indicator — is driven by an async probe that
 * may well succeed a moment later).
 */
import 'fake-indexeddb/auto';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { environment } from '../../../environments/environment';
import * as cache from './api-cache';
import { offlineCacheInterceptor } from './offline-cache.interceptor';
import { OfflineState } from './offline-state.service';

const API = environment.apiBaseUrl;

function jwt(userId: number): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64({ user_id: userId })}.sig`;
}

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

describe('review-fecore: offlineCacheInterceptor staleness', () => {
  let http: HttpClient;
  let ctrl: HttpTestingController;
  let offline: OfflineState;

  beforeEach(async () => {
    useMemoryLocalStorage();
    window.localStorage.setItem('hf.access', jwt(42));
    await cache.clearScope('42');
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([offlineCacheInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    ctrl = TestBed.inject(HttpTestingController);
    offline = TestBed.inject(OfflineState);
    // Neutralise the real network probe the interceptor triggers via reportFailure().
    offline.probe = async () => undefined;
  });

  it('evidence: a 45-day-old portfolio snapshot is replayed as a 200 while mode is still "online"', async () => {
    const savedAt = Date.now() - 45 * 24 * 60 * 60 * 1000;
    await cache.put({
      key: cache.cacheKey(`${API}/portfolio/`),
      url: `${API}/portfolio/`,
      body: { nav: '1000000.00', positions: [{ ticker: 'NVDA', quantity: '100' }] },
      status: 200,
      savedAt,
      schemaVersion: cache.SCHEMA_VERSION,
    });
    offline.mode.set('online');

    let body: unknown;
    http.get(`${API}/portfolio/`).subscribe((b) => (body = b));
    // One transient connection reset (status 0) — e.g. the backend restarting.
    ctrl.expectOne(`${API}/portfolio/`).error(new ProgressEvent('err'), { status: 0 });
    await vi.waitFor(() => expect(body).toBeDefined(), { timeout: 2000, interval: 10 });

    expect(body).toEqual({ nav: '1000000.00', positions: [{ ticker: 'NVDA', quantity: '100' }] });
    // The store consumers read the body only (HttpClient.get<T>), so the
    // X-HF-Cache / X-HF-Cached-At markers are invisible; and the mode signal
    // that drives the banner was not flipped by the interceptor.
    expect(offline.mode()).toBe('online');
  });
});
