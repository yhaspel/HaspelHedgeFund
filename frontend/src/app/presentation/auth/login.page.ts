import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-login',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="min-h-screen grid place-items-center bg-bg">
      <section class="w-full max-w-[420px] p-10">
        <div class="auth-form-wrap">
          <div class="flex items-center gap-3.5 text-text-2">
            <img src="/icon-large.svg" alt="" width="52" height="52" class="auth-logo" />
            <span class="auth-brand">Haspel Hedge Fund</span>
          </div>

          <h1>Log in</h1>

          <form (ngSubmit)="submit()" class="flex flex-col gap-3.5">
            <div class="field">
              <label class="lbl" for="login-email">Email</label>
              <input id="login-email" class="input sans" type="email" name="email" autocomplete="email"
                [(ngModel)]="email" required
                [attr.aria-invalid]="error() ? 'true' : null"
                [attr.aria-describedby]="error() ? 'login-error' : null" />
            </div>
            <div class="field">
              <label class="lbl" for="login-password">Password</label>
              <input id="login-password" class="input sans" type="password" name="password" autocomplete="current-password"
                [(ngModel)]="password" required
                [attr.aria-invalid]="error() ? 'true' : null"
                [attr.aria-describedby]="error() ? 'login-error' : null" />
            </div>
            @if (error()) {
              <p id="login-error" role="alert" class="text-[var(--acc-short-fg)] text-2xs">{{ error() }}</p>
            }
            <button class="btn primary w-full h-9 justify-center" type="submit">
              Log in
            </button>
          </form>

          <p class="text-xs text-text-2">
            No account?
            <a routerLink="/signup" class="text-[var(--acc-info-fg)]">Sign up</a>
          </p>
        </div>
      </section>
    </div>
  `,
  styles: [
    `
      .auth-logo {
        display: block;
        border: 1px solid var(--border-2);
        border-radius: 12px;
      }
      .auth-brand {
        font-size: 24px;
        font-weight: 600;
        color: var(--text);
      }
    `,
  ],
})
export class LoginPage {
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  email = '';
  password = '';
  error = signal<string | null>(null);

  submit(): void {
    this.error.set(null);
    this.auth.login(this.email, this.password).subscribe({
      next: () => this.router.navigateByUrl('/'),
      error: () => this.error.set('Invalid email or password'),
    });
  }
}
