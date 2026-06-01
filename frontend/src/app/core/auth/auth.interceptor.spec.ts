import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
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

describe('authInterceptor', () => {
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

  it('refreshes and replays the original request on a 401', () => {
    tokens.set('expired', 'r1');
    let body: unknown;
    http.get('/api/data/').subscribe((b) => (body = b));

    // Original request goes out with the (now-expired) access token, then 401s.
    const first = httpTesting.expectOne('/api/data/');
    expect(first.request.headers.get('Authorization')).toBe('Bearer expired');
    first.flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    // Interceptor silently refreshes using the stored refresh token.
    const refresh = httpTesting.expectOne(REFRESH_URL);
    expect(refresh.request.body).toEqual({ refresh: 'r1' });
    refresh.flush({ access: 'fresh', refresh: 'r2' });

    // Original request is replayed with the new access token and succeeds.
    const replay = httpTesting.expectOne('/api/data/');
    expect(replay.request.headers.get('Authorization')).toBe('Bearer fresh');
    replay.flush({ ok: true });

    expect(body).toEqual({ ok: true });
    expect(tokens.getAccess()).toBe('fresh');
    expect(tokens.getRefresh()).toBe('r2'); // rotated refresh token stored
    expect(navigatedTo).toEqual([]); // user never bounced to login
  });

  it('logs out only when the refresh itself fails', () => {
    tokens.set('expired', 'stale');
    let errored = false;
    http.get('/api/data/').subscribe({ error: () => (errored = true) });

    httpTesting
      .expectOne('/api/data/')
      .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });
    httpTesting
      .expectOne(REFRESH_URL)
      .flush({ detail: 'invalid' }, { status: 401, statusText: 'Unauthorized' });

    expect(errored).toBe(true);
    expect(tokens.getAccess()).toBeNull();
    expect(tokens.getRefresh()).toBeNull();
    expect(navigatedTo).toEqual([['/login']]);
  });

  it('bounces to login immediately on a 401 when no refresh token exists', () => {
    // Access token present but no refresh token — nothing to recover with.
    tokens.set('expired', null as unknown as string);
    let errored = false;
    http.get('/api/data/').subscribe({ error: () => (errored = true) });

    httpTesting
      .expectOne('/api/data/')
      .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    expect(errored).toBe(true);
    expect(navigatedTo).toEqual([['/login']]);
    httpTesting.expectNone(REFRESH_URL); // no refresh attempted
  });

  it('shares a single refresh across concurrent 401s', () => {
    tokens.set('expired', 'r1');
    const bodies: unknown[] = [];
    http.get('/api/a/').subscribe((b) => bodies.push(b));
    http.get('/api/b/').subscribe((b) => bodies.push(b));

    // Both in-flight requests fail with 401 at the same time.
    httpTesting
      .expectOne('/api/a/')
      .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });
    httpTesting
      .expectOne('/api/b/')
      .flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    // Exactly ONE refresh call is made despite two concurrent failures.
    const refreshes = httpTesting.match(REFRESH_URL);
    expect(refreshes.length).toBe(1);
    refreshes[0].flush({ access: 'fresh', refresh: 'r2' });

    // Both original requests are replayed with the new token.
    const replays = httpTesting.match(
      (r) => r.url === '/api/a/' || r.url === '/api/b/',
    );
    expect(replays.length).toBe(2);
    replays.forEach((req, i) => {
      expect(req.request.headers.get('Authorization')).toBe('Bearer fresh');
      req.flush({ ok: i });
    });

    expect(bodies.length).toBe(2);
    expect(tokens.getAccess()).toBe('fresh');
  });

  it('does not attach a token or intercept the refresh endpoint itself', () => {
    tokens.set('expired', 'r1');
    http.post('http://localhost:8811/api/auth/refresh/', { refresh: 'r1' }).subscribe({
      error: () => undefined,
    });

    const req = httpTesting.expectOne(REFRESH_URL);
    expect(req.request.headers.has('Authorization')).toBe(false);
    // A 401 from the refresh endpoint is not retried (would loop forever).
    req.flush({ detail: 'invalid' }, { status: 401, statusText: 'Unauthorized' });
    httpTesting.expectNone(REFRESH_URL);
  });
});
