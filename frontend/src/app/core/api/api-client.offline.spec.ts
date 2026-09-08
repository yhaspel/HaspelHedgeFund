import 'fake-indexeddb/auto';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { environment } from '../../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { TokenStorage } from '../auth/token-storage';
import { offlineCacheInterceptor } from '../offline/offline-cache.interceptor';
import { OfflineState } from '../offline/offline-state.service';
import { OfflineWriteBlockedError } from '../offline/offline-write-blocked.error';
import { ApiClient } from './api-client';

const API = environment.apiBaseUrl;

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

describe('ApiClient write-blocking (WS-4.3)', () => {
  let api: ApiClient;
  let ctrl: HttpTestingController;
  let offline: OfflineState;

  beforeEach(() => {
    useMemoryLocalStorage();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiClient);
    ctrl = TestBed.inject(HttpTestingController);
    offline = TestBed.inject(OfflineState);
  });

  afterEach(() => ctrl.verify());

  it('blocks POST at L2 before any HTTP', () => {
    offline.mode.set('offline-l2');
    let err: unknown;
    api.post('/orders/', { qty: 1 }).subscribe({ error: (e) => (err = e) });
    ctrl.expectNone(`${API}/orders/`);
    expect(err).toBeInstanceOf(OfflineWriteBlockedError);
  });

  it('blocks PUT/PATCH/DELETE when forced offline', () => {
    offline.setForced(true);
    for (const call of [
      () => api.put('/a/', {}),
      () => api.patch('/b/', {}),
      () => api.delete('/c/'),
    ]) {
      let err: unknown;
      call().subscribe({ error: (e) => (err = e) });
      expect(err).toBeInstanceOf(OfflineWriteBlockedError);
    }
    ctrl.expectNone(() => true);
    offline.setForced(false);
  });

  it('does NOT block writes at L1 / online', () => {
    offline.mode.set('offline-l1');
    api.post('/orders/', { qty: 1 }).subscribe();
    ctrl.expectOne(`${API}/orders/`).flush({ ok: true });
  });
});

describe('auth invariant under offline (WS-6.2)', () => {
  beforeEach(() => {
    useMemoryLocalStorage();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  });
  afterEach(() => vi.restoreAllMocks());

  it('a status-0 GET never clears tokens or navigates to /login', async () => {
    const navigate = vi.fn();
    window.localStorage.setItem('hf.access', jwt(7));
    window.localStorage.setItem('hf.refresh', 'refresh-tok');
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([offlineCacheInterceptor, authInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: { navigate } },
      ],
    });
    const http = TestBed.inject(HttpClient);
    const ctrl = TestBed.inject(HttpTestingController);
    const tokens = TestBed.inject(TokenStorage);

    let errored = false;
    http.get(`${API}/me/`).subscribe({ error: () => (errored = true) });
    ctrl.expectOne(`${API}/me/`).error(new ProgressEvent('err'), { status: 0 });
    await vi.waitFor(() => expect(errored).toBe(true), { timeout: 2000, interval: 10 });

    expect(tokens.getAccess()).toBe(jwt(7)); // NOT cleared
    expect(navigate).not.toHaveBeenCalled();
  });
});
