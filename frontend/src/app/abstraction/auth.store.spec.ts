import { HttpClient, provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
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

  beforeEach(() => {
    tokens = new FakeTokenStorage();
    TestBed.configureTestingModule({
      providers: [
        AuthStore,
        { provide: TokenStorage, useValue: tokens },
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

  it('logout clears tokens and user', () => {
    tokens.set('a', 'r');
    store.logout();
    expect(tokens.getAccess()).toBeNull();
    expect(store.user()).toBeNull();
  });
});
