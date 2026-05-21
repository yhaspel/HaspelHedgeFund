import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-login',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div style="min-height:100vh;display:grid;place-items:center;background:var(--bg)">
      <section style="width:100%;max-width:420px;padding:40px">
        <div class="auth-form-wrap">
          <div style="display:flex;align-items:center;gap:14px;color:var(--text-2)">
            <img src="/icon-large.svg" alt="" width="52" height="52"
                 style="display:block;border:1px solid var(--border-2);border-radius:12px" />
            <span style="font-size:24px;font-weight:600;color:var(--text-1)">Haspel Hedge Fund</span>
          </div>

          <h1>Log in</h1>

          <form (ngSubmit)="submit()" style="display:flex;flex-direction:column;gap:14px">
            <div class="field">
              <label class="lbl">Email</label>
              <input class="input sans" type="email" name="email" [(ngModel)]="email" required />
            </div>
            <div class="field">
              <label class="lbl">Password</label>
              <input class="input sans" type="password" name="password" [(ngModel)]="password" required />
            </div>
            @if (error()) {
              <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
            }
            <button class="btn primary" type="submit" style="width:100%;height:36px;justify-content:center">
              Log in
            </button>
          </form>

          <p style="font-size:13px;color:var(--text-2)">
            No account?
            <a routerLink="/signup" style="color:var(--acc-info-fg)">Sign up</a>
          </p>
        </div>
      </section>
    </div>
  `,
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
