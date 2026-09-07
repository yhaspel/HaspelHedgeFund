/**
 * Review (fecore) — authGuard + AuthStore boot sequence.
 *
 * Evidence tests (plain `it`) document current behaviour that is wasteful
 * but not broken; nothing here is marked as a defect.
 */
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, UrlTree, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AuthStore } from '../../abstraction/auth.store';
import { authGuard } from './auth.guard';
import { TokenStorage } from './token-storage';

class FakeTokenStorage {
  private a: string | null = null;
  private r: string | null = null;
  getAccess() { return this.a; }
  getRefresh() { return this.r; }
  set(a: string, r: string) { this.a = a; this.r = r; }
  clear() { this.a = null; this.r = null; }
}

function runGuard(): boolean | UrlTree {
  return TestBed.runInInjectionContext(
    () => authGuard({} as never, {} as never) as boolean | UrlTree,
  );
}

describe('review-fecore: authGuard / AuthStore boot', () => {
  let http: HttpTestingController;
  let tokens: FakeTokenStorage;

  beforeEach(() => {
    tokens = new FakeTokenStorage();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([{ path: 'login', children: [] }]),
        { provide: TokenStorage, useValue: tokens },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('evidence: cold load issues /me/ twice (app initializer + first guarded navigation)', () => {
    tokens.set('access', 'refresh');
    // provideAppInitializer (app.config.ts:36) → refreshMe()
    TestBed.inject(AuthStore).refreshMe();
    // First guarded route resolves before /me/ returns → auth.guard.ts:12-14 → refreshMe() again
    expect(runGuard()).toBe(true);
    const reqs = http.match((r) => r.url.endsWith('/me/'));
    expect(reqs.length).toBe(2);
    reqs.forEach((r) => r.flush({ id: 1, email: 'a@b.com' }));
  });

  it('evidence: every guarded navigation re-fetches /me/ while the store is unauthenticated', () => {
    tokens.set('access', 'refresh');
    for (let i = 0; i < 5; i++) expect(runGuard()).toBe(true);
    const reqs = http.match((r) => r.url.endsWith('/me/'));
    expect(reqs.length).toBe(5);
    // e.g. /me/ 500s → the user keeps navigating → one /me/ per navigation, forever.
    reqs.forEach((r) => r.flush({ detail: 'boom' }, { status: 500, statusText: 'err' }));
  });

  it('evidence: the redirect to /login drops the requested URL (no returnUrl)', () => {
    const router = TestBed.inject(Router);
    const result = runGuard();
    expect(result).toEqual(router.createUrlTree(['/login']));
    expect((result as UrlTree).queryParams).toEqual({});
  });
});
