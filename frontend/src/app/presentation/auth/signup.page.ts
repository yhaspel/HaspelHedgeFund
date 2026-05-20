import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-signup',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div style="min-height:100vh;display:grid;place-items:center;background:var(--bg)">
      <section style="width:100%;max-width:420px;padding:40px">
        <div style="display:flex;align-items:center;gap:10px;color:var(--text-2);font-size:13px;margin-bottom:18px">
          <div style="width:28px;height:28px;background:var(--surface-2);border:1px solid var(--border-2);border-radius:6px;display:grid;place-items:center">
            <svg width="16" height="16"><use href="/icons.svg#i-logo" /></svg>
          </div>
          <span>Haspel</span>
        </div>

        <h1 style="font-size:28px;line-height:34px;font-weight:600;letter-spacing:-0.018em;margin:0 0 18px">Sign up</h1>

        <form (ngSubmit)="submit()" style="display:flex;flex-direction:column;gap:14px">
          <div class="field">
            <label class="lbl">Email</label>
            <input class="input sans" type="email" name="email" [(ngModel)]="email" required />
          </div>
          <div class="field">
            <label class="lbl">Password <span style="color:var(--text-3);text-transform:none;letter-spacing:0;font-weight:400">· min 8 chars</span></label>
            <input class="input sans" type="password" name="password" [(ngModel)]="password" required minlength="8" />
          </div>
          @if (error()) {
            <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
          }
          <button class="btn primary" type="submit" style="width:100%;height:36px;justify-content:center">
            Sign up
          </button>
        </form>

        <p style="font-size:13px;color:var(--text-2);margin-top:18px">
          Have an account?
          <a routerLink="/login" style="color:var(--acc-info-fg)">Log in</a>
        </p>
      </section>
    </div>
  `,
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
