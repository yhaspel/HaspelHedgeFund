import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-signup',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="min-h-screen grid place-items-center bg-bg">
      <section class="w-full max-w-[420px] p-10">
        <div class="flex items-center gap-2.5 text-text-2 text-xs mb-[18px]">
          <img src="/icon-large.svg" alt="" width="28" height="28" class="auth-logo-sm" />
          <span>Haspel Hedge Fund</span>
        </div>

        <h1 class="signup-h1">Sign up</h1>

        <form (ngSubmit)="submit()" class="flex flex-col gap-3.5">
          <div class="field">
            <label class="lbl" for="signup-email">Email</label>
            <input id="signup-email" class="input sans" type="email" name="email" autocomplete="email"
              [(ngModel)]="email" required
              [attr.aria-invalid]="error() ? 'true' : null"
              [attr.aria-describedby]="error() ? 'signup-error' : null" />
          </div>
          <div class="field">
            <label class="lbl" for="signup-password">Password <span class="lbl-hint">· min 8 chars</span></label>
            <input id="signup-password" class="input sans" type="password" name="password" autocomplete="new-password"
              [(ngModel)]="password" required minlength="8"
              [attr.aria-invalid]="error() ? 'true' : null"
              [attr.aria-describedby]="error() ? 'signup-error' : null" />
          </div>
          @if (error()) {
            <p id="signup-error" role="alert" class="text-[var(--acc-short-fg)] text-2xs">{{ error() }}</p>
          }
          <button class="btn primary w-full h-9 justify-center" type="submit">
            Sign up
          </button>
        </form>

        <p class="text-xs text-text-2 mt-[18px]">
          Have an account?
          <a routerLink="/login" class="text-[var(--acc-info-fg)]">Log in</a>
        </p>
      </section>
    </div>
  `,
  styles: [
    `
      .auth-logo-sm {
        display: block;
        border: 1px solid var(--border-2);
        border-radius: 6px;
      }
      .signup-h1 {
        font-size: 28px;
        line-height: 34px;
        font-weight: 600;
        letter-spacing: -0.018em;
        margin: 0 0 18px;
      }
      .lbl-hint {
        color: var(--text-3);
        text-transform: none;
        letter-spacing: 0;
        font-weight: 400;
      }
    `,
  ],
})
export class SignupPage {
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  email = '';
  password = '';
  error = signal<string | null>(null);

  submit(): void {
    this.error.set(null);
    this.auth.signup(this.email, this.password).subscribe({
      next: () => {
        this.auth.login(this.email, this.password).subscribe({
          next: () => this.router.navigateByUrl('/'),
        });
      },
      error: () => this.error.set('Could not create account'),
    });
  }
}
