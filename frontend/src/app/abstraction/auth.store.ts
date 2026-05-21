import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import { TokenStorage } from '../core/auth/token-storage';
import { AuthTokens, User } from '../core/models/user.model';

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
      error: () => this.logout(),
    });
  }

  logout(): void {
    this.tokens.clear();
    this._user.set(null);
    this.router.navigateByUrl('/login');
  }
}
