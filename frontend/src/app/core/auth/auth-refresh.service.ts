import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, finalize, map, shareReplay, tap, throwError } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthTokens } from '../models/user.model';
import { TokenStorage } from './token-storage';

/**
 * Coordinates silent access-token renewal against /auth/refresh/.
 *
 * The access token lives 30 minutes; the refresh token lives 7 days (and is
 * rotated on every refresh, so an active session never lapses). When a request
 * 401s, the interceptor asks this service to swap the expired access token for
 * a fresh one and then replays the request.
 *
 * Concurrency matters: when a token expires, several queued requests can 401 at
 * once. We must NOT fire one /auth/refresh/ per failure — with rotation enabled
 * the second call would present an already-rotated refresh token. `inFlight`
 * holds a single shared observable so every concurrent caller awaits the same
 * refresh and receives the same new access token.
 */
@Injectable({ providedIn: 'root' })
export class AuthRefreshService {
  private readonly http = inject(HttpClient);
  private readonly tokens = inject(TokenStorage);

  private inFlight: Observable<string> | null = null;
  /**
   * The refresh token whose /auth/refresh/ call was rejected. Rotated refresh
   * tokens are blacklisted server-side, so a rejection is TERMINAL for that
   * token: presenting it again can only ever fail. Remembering it keeps a burst
   * of queued 401s from firing one doomed refresh each (and, with the throttled
   * refresh view, from burning the auth throttle budget on a dead session).
   */
  private deadToken: string | null = null;

  /** Returns the new access token, or errors if no/expired refresh token. */
  refresh(): Observable<string> {
    if (this.inFlight) return this.inFlight;

    const refreshToken = this.tokens.getRefresh();
    if (!refreshToken) {
      return throwError(() => new Error('No refresh token available'));
    }
    if (refreshToken === this.deadToken) {
      return throwError(() => new Error('Refresh token was already rejected'));
    }

    this.inFlight = this.http
      .post<AuthTokens>(`${environment.apiBaseUrl}/auth/refresh/`, {
        refresh: refreshToken,
      })
      .pipe(
        // Persist the new access token, plus the rotated refresh token when the
        // backend returns one (falls back to the current one if rotation is off).
        tap((t) => this.tokens.set(t.access, t.refresh ?? refreshToken)),
        map((t) => t.access),
        // A rejected refresh token is burnt: record it so no later 401 replays it.
        catchError((err: unknown) => {
          this.deadToken = refreshToken;
          return throwError(() => err);
        }),
        // Reset the gate when the call settles (success or failure) so a later
        // expiry can start a brand-new refresh.
        finalize(() => {
          this.inFlight = null;
        }),
        shareReplay(1),
      );

    return this.inFlight;
  }
}
