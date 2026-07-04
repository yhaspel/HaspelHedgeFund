import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import { TokenStorage } from '../core/auth/token-storage';
import { AuthTokens, User } from '../core/models/user.model';
import { ANON_SCOPE, clearScope, currentScope } from '../core/offline/api-cache';

@Injectable({ providedIn: 'root' })
export class AuthStore {
  private readonly api = inject(ApiClient);
  private readonly tokens = inject(TokenStorage);
  private readonly router = inject(Router);

  private readonly _user = signal<User | null>(null);
  readonly user = this._user.asReadonly();
  readonly isAuthenticated = computed(() => this._user() !== null);

  login(email: string, password: string): Observable<AuthTokens> {
    return this.api.post<AuthTokens>('/auth/login/', { email, password }).pipe(
      tap((t) => {
        this.tokens.set(t.access, t.refresh);
        this.refreshMe();
      }),
    );
  }

  signup(email: string, password: string): Observable<User> {
    return this.api.post<User>('/auth/signup/', { email, password });
  }

  refreshMe(): void {
    if (!this.tokens.getAccess()) return;
    this.api.get<User>('/me/').subscribe({
      next: (u) => this._user.set(u),
      // Session termination is owned solely by the auth interceptor: it silently
      // refreshes an expired access token and only redirects to /login when the
      // refresh token itself is dead. A transient /me/ failure must NOT log the
      // user out — they stay signed in until they explicitly click "Log out".
      error: () => undefined,
    });
  }

  logout(): void {
    // P4-OFF WS-3.5: purge the offline cache for this user AND the anon scope
    // BEFORE clearing the token (currentScope reads the JWT) — last-known
    // portfolio values are exactly the residue logout must remove, and the anon
    // scope must never survive a session boundary.
    const scope = currentScope();
    void clearScope(scope);
    void clearScope(ANON_SCOPE);
    this.tokens.clear();
    this._user.set(null);
    this.router.navigateByUrl('/login');
  }
}
