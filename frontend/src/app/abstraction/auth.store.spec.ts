import { HttpClient, provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { TokenStorage } from '../core/auth/token-storage';
import { AuthStore } from './auth.store';

class FakeTokenStorage {
  private a: string | null = null;
  private r: string | null = null;
  getAccess() { return this.a; }
  getRefresh() { return this.r; }
  set(a: string, r: string) { this.a = a; this.r = r; }
  clear() { this.a = null; this.r = null; }
}

describe('AuthStore', () => {
  let store: AuthStore;
  let http: HttpTestingController;
  let tokens: FakeTokenStorage;
  let navigatedTo: string[];

  beforeEach(() => {
    tokens = new FakeTokenStorage();
    navigatedTo = [];
    const router = {
      navigateByUrl: (url: string) => {
        navigatedTo.push(url);
        return Promise.resolve(true);
      },
    };
    TestBed.configureTestingModule({
      providers: [
        AuthStore,
        { provide: TokenStorage, useValue: tokens },
        { provide: Router, useValue: router },
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    store = TestBed.inject(AuthStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('stores user signal after login + /me roundtrip', () => {
    store.login('a@b.com', 'supersecret').subscribe();
    http
      .expectOne((r) => r.url.endsWith('/auth/login/'))
      .flush({ access: 'a', refresh: 'r' });
    http
      .expectOne((r) => r.url.endsWith('/me/'))
      .flush({ id: 1, email: 'a@b.com', date_joined: '2026-05-15' });

    expect(store.isAuthenticated()).toBe(true);
    expect(store.user()?.email).toBe('a@b.com');
    expect(tokens.getAccess()).toBe('a');
  });

  it('logout revokes the refresh token server-side, then clears tokens/user and redirects', () => {
    tokens.set('a', 'r');
    store.logout();
    // The refresh token is read BEFORE the local clear and posted to the
    // blacklist endpoint, so signing out really ends the session.
    const req = http.expectOne((r) => r.url.endsWith('/auth/logout/'));
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ refresh: 'r' });
    req.flush(null, { status: 205, statusText: 'Reset Content' });

    expect(tokens.getAccess()).toBeNull();
    expect(store.user()).toBeNull();
    expect(navigatedTo).toContain('/login');
  });

  it('logout still ends the local session when the revoke call fails', () => {
    tokens.set('a', 'r');
    store.logout();
    http
      .expectOne((r) => r.url.endsWith('/auth/logout/'))
      .flush(null, { status: 0, statusText: 'Unknown Error' });
    expect(tokens.getAccess()).toBeNull();
    expect(navigatedTo).toContain('/login');
  });

  it('refreshMe leaves the session intact when /me/ fails (interceptor owns logout)', () => {
    tokens.set('tok', 'r');
    store.refreshMe();
    http
      .expectOne((r) => r.url.endsWith('/me/'))
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    // A transient error must not clear tokens or redirect — only the interceptor
    // (on a failed refresh) or an explicit logout() ends the session.
    expect(store.user()).toBeNull();
    expect(tokens.getAccess()).toBe('tok');
    expect(navigatedTo).toEqual([]);
  });
});
