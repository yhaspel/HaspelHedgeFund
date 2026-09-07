import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthRefreshService } from './auth-refresh.service';
import { TokenStorage } from './token-storage';

/** The auth endpoints must never carry a bearer token or trigger refresh-retry. */
function isAuthEndpoint(url: string): boolean {
  return (
    url.includes('/auth/login') ||
    url.includes('/auth/refresh') ||
    url.includes('/auth/logout') ||
    url.includes('/auth/signup')
  );
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const tokens = inject(TokenStorage);
  const router = inject(Router);
  const refresher = inject(AuthRefreshService);

  const authEndpoint = isAuthEndpoint(req.url);
  const access = tokens.getAccess();
  const authed =
    access && !authEndpoint
      ? req.clone({ setHeaders: { Authorization: `Bearer ${access}` } })
      : req;

  const bounceToLogin = () => {
    tokens.clear();
    router.navigate(['/login']);
  };

  return next(authed).pipe(
    catchError((err: unknown) => {
      const is401 = err instanceof HttpErrorResponse && err.status === 401;

      // Only the expired-access-token case is recoverable. Auth endpoints and
      // non-401s fall straight through.
      if (!is401 || authEndpoint) {
        return throwError(() => err);
      }

      // A 401 with no refresh token means there's nothing to recover with.
      if (!tokens.getRefresh()) {
        bounceToLogin();
        return throwError(() => err);
      }

      // Try to silently mint a new access token and replay the original request.
      // Only if the refresh itself fails (refresh token expired/revoked/blacklisted)
      // — or the REPLAY itself 401s — do we actually log the user out.
      return refresher.refresh().pipe(
        // This catchError sits UPSTREAM of the switchMap, so it only ever sees a
        // failure of /auth/refresh/ itself. Rotated refresh tokens are blacklisted
        // server-side, so a 401 here is terminal: never retry, just end the session.
        catchError((refreshErr) => {
          bounceToLogin();
          return throwError(() => refreshErr);
        }),
        switchMap((newAccess) =>
          next(
            req.clone({ setHeaders: { Authorization: `Bearer ${newAccess}` } }),
          ).pipe(
            // The replay ran with a demonstrably fresh access token. An ordinary
            // 400/404/409/5xx therefore says nothing about the session and MUST
            // NOT clear it; only another 401 means the new token is unusable.
            catchError((replayErr: unknown) => {
              if (replayErr instanceof HttpErrorResponse && replayErr.status === 401) {
                bounceToLogin();
              }
              return throwError(() => replayErr);
            }),
          ),
        ),
      );
    }),
  );
};
