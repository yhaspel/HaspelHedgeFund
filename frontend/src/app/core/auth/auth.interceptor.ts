import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { TokenStorage } from './token-storage';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const tokens = inject(TokenStorage);
  const router = inject(Router);
  const access = tokens.getAccess();
  const authed = access ? req.clone({ setHeaders: { Authorization: `Bearer ${access}` } }) : req;
  return next(authed).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && access) {
        // Token present but rejected — treat as expired/invalid and bounce to login.
        tokens.clear();
        router.navigate(['/login']);
      }
      return throwError(() => err);
    }),
  );
};
