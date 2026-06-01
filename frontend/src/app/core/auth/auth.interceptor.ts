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
      // Only if the refresh itself fails (refresh token expired/revoked) do we
      // actually log the user out — otherwise the session is invisible to them.
      return refresher.refresh().pipe(
        switchMap((newAccess) =>
          next(
            req.clone({ setHeaders: { Authorization: `Bearer ${newAccess}` } }),
          ),
        ),
        catchError((refreshErr) => {
          bounceToLogin();
          return throwError(() => refreshErr);
        }),
      );
    }),
  );
};
