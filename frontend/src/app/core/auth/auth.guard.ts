import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';
import { TokenStorage } from './token-storage';

export const authGuard: CanActivateFn = () => {
  const router = inject(Router);
  const auth = inject(AuthStore);
  const tokens = inject(TokenStorage);

  if (auth.isAuthenticated()) return true;
  if (tokens.getAccess()) {
    auth.refreshMe();
    return true;
  }
  return router.createUrlTree(['/login']);
};
