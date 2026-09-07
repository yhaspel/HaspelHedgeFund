/**
 * Review (fecore) — proof tests for the auth interceptor / refresh service.
 *
 * Each `it.fails` documents a CONFIRMED defect: the assertion states the
 * correct behaviour, and vitest's `fails` modifier passes only because the
 * current code violates it. Flip `it.fails` → `it` after fixing.
 */
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AuthStore } from '../../abstraction/auth.store';
import { authInterceptor } from './auth.interceptor';
import { TokenStorage } from './token-storage';

class FakeTokenStorage {
  private a: string | null = null;
  private r: string | null = null;
  getAccess() { return this.a; }
  getRefresh() { return this.r; }
  set(a: string, r: string) { this.a = a; this.r = r; }
  clear() { this.a = null; this.r = null; }
}

const REFRESH_URL = (r: { url: string }) => r.url.includes('/auth/refresh/');

describe('review-fecore: authInterceptor', () => {
  let http: HttpClient;
  let httpTesting: HttpTestingController;
  let tokens: FakeTokenStorage;
  let navigatedTo: unknown[][];

  beforeEach(() => {
    tokens = new FakeTokenStorage();
    navigatedTo = [];
    const router = {
      navigate: (commands: unknown[]) => {
        navigatedTo.push(commands);
        return Promise.resolve(true);
      },
    };
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: TokenStorage, useValue: tokens },
        { provide: Router, useValue: router },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it(
    'F: a NON-401 failure of the replayed request (after a SUCCESSFUL refresh) must not log the user out',
    () => {
      tokens.set('expired', 'r1');
      let status = 0;
      http.post('/api/portfolio/positions/', { ticker: 'AAPL' }).subscribe({
        error: (e) => (status = e.status),
      });

      // 1) original request 401s because the access token expired
      httpTesting
        .expectOne('/api/portfolio/positions/')
        .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });
      // 2) refresh SUCCEEDS → session is perfectly healthy
      httpTesting.expectOne(REFRESH_URL).flush({ access: 'fresh', refresh: 'r2' });
      // 3) the replayed request fails with an ordinary validation error
      httpTesting
        .expectOne('/api/portfolio/positions/')
        .flush({ ticker: ['invalid'] }, { status: 400, statusText: 'Bad Request' });

      expect(status).toBe(400);
      // Correct behaviour: the session survives an ordinary 400/500 on the replay.
      // Actual (auth.interceptor.ts:59-62): catchError wraps BOTH refresh() and the
      // replayed next() → bounceToLogin() clears the fresh tokens and navigates.
      expect(tokens.getAccess()).toBe('fresh');
      expect(tokens.getRefresh()).toBe('r2');
      expect(navigatedTo).toEqual([]);
    },
  );

  it('a 404 on the replayed request keeps the refreshed session alive', () => {
    tokens.set('expired', 'r1');
    http.get('/api/tickers/NOPE/profile/').subscribe({ error: () => undefined });
    httpTesting
      .expectOne('/api/tickers/NOPE/profile/')
      .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });
    httpTesting.expectOne(REFRESH_URL).flush({ access: 'fresh', refresh: 'r2' });
    httpTesting
      .expectOne('/api/tickers/NOPE/profile/')
      .flush({ detail: 'not found' }, { status: 404, statusText: 'Not Found' });

    // A missing ticker profile says nothing about the session.
    expect(tokens.getAccess()).toBe('fresh');
    expect(tokens.getRefresh()).toBe('r2');
    expect(navigatedTo).toEqual([]);
  });

  it('a 401 on the REPLAYED request (fresh token already unusable) does end the session', () => {
    tokens.set('expired', 'r1');
    http.get('/api/fund/').subscribe({ error: () => undefined });
    httpTesting.expectOne('/api/fund/').flush(null, { status: 401, statusText: 'Unauthorized' });
    httpTesting.expectOne(REFRESH_URL).flush({ access: 'fresh', refresh: 'r2' });
    httpTesting.expectOne('/api/fund/').flush(null, { status: 401, statusText: 'Unauthorized' });

    expect(tokens.getAccess()).toBeNull();
    expect(navigatedTo).toEqual([['/login']]);
  });

  it('a rejected /auth/refresh/ is terminal — the blacklisted token is never replayed', () => {
    tokens.set('expired', 'r1');
    http.get('/api/fund/').subscribe({ error: () => undefined });
    httpTesting.expectOne('/api/fund/').flush(null, { status: 401, statusText: 'Unauthorized' });
    httpTesting.expectOne(REFRESH_URL).flush(null, { status: 401, statusText: 'Unauthorized' });
    expect(navigatedTo).toEqual([['/login']]);

    // A second 401 must not fire another /auth/refresh/ with the dead token.
    tokens.set('expired', 'r1');
    http.get('/api/runs/').subscribe({ error: () => undefined });
    httpTesting.expectOne('/api/runs/').flush(null, { status: 401, statusText: 'Unauthorized' });
    httpTesting.expectNone(REFRESH_URL);
  });

  it('evidence: after the interceptor bounces to /login, AuthStore still reports isAuthenticated=true', () => {
    const auth = TestBed.inject(AuthStore);
    tokens.set('access', 'refresh');
    auth.refreshMe();
    httpTesting.expectOne((r) => r.url.endsWith('/me/')).flush({ id: 1, email: 'a@b.com' });
    expect(auth.isAuthenticated()).toBe(true);

    // Session dies: 401 + refresh rejected → bounceToLogin() (auth.interceptor.ts:29-32).
    http.get('/api/fund/').subscribe({ error: () => undefined });
    httpTesting.expectOne('/api/fund/').flush(null, { status: 401, statusText: 'Unauthorized' });
    httpTesting.expectOne(REFRESH_URL).flush(null, { status: 401, statusText: 'Unauthorized' });
    expect(navigatedTo).toEqual([['/login']]);
    expect(tokens.getAccess()).toBeNull();

    // …but the in-memory user survives: authGuard (auth.guard.ts:11) will let the
    // next guarded navigation (e.g. browser Back) straight through, and the shell
    // keeps rendering the dead session's email + "Log out".
    expect(auth.isAuthenticated()).toBe(true);
    expect(auth.user()?.email).toBe('a@b.com');
  });

  it('evidence: the bearer token is attached to EVERY HttpClient URL, not only the API origin', () => {
    tokens.set('tok', 'r1');
    http.get('https://third-party.example.com/x').subscribe({ error: () => undefined });
    const req = httpTesting.expectOne('https://third-party.example.com/x');
    expect(req.request.headers.get('Authorization')).toBe('Bearer tok');
    req.flush({});
  });
});
