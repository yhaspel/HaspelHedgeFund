import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-login',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="auth">
      <section class="auth-form-col">
        <div class="auth-form-wrap">
          <div style="display:flex;align-items:center;gap:10px;color:var(--text-2);font-size:13px">
            <div style="width:28px;height:28px;background:var(--surface-2);border:1px solid var(--border-2);border-radius:6px;display:grid;place-items:center">
              <svg width="16" height="16"><use href="/icons.svg#i-logo" /></svg>
            </div>
            <span>Haspel</span>
            <span style="color:var(--text-3)">·</span>
            <span class="mono">v 0.9</span>
            <span style="color:var(--text-3)">·</span>
            <span class="mono">paper</span>
          </div>

          <div>
            <h1>Sign in</h1>
            <p class="sub" style="margin-top:8px">
              Welcome back. Pick up your book where you left it — your council ran 14 minutes ago.
            </p>
          </div>

          <form (ngSubmit)="submit()" style="display:flex;flex-direction:column;gap:14px">
            <div class="field">
              <label class="lbl">Email</label>
              <input class="input sans" type="email" name="email" [(ngModel)]="email"
                placeholder="elena.haspel@haspel.fund" required />
            </div>
            <div class="field">
              <div style="display:flex;align-items:center;justify-content:space-between">
                <label class="lbl">Password</label>
                <a href="#" style="font-size:12px;color:var(--acc-info-fg)">Forgot?</a>
              </div>
              <div style="position:relative">
                <input class="input sans" [type]="showPwd() ? 'text' : 'password'" name="password"
                  [(ngModel)]="password" required style="padding-right:36px" />
                <button type="button" class="icon-btn"
                  style="position:absolute;right:2px;top:0;width:30px;height:30px"
                  (click)="showPwd.set(!showPwd())" aria-label="Toggle password visibility">
                  <svg width="14" height="14"><use href="/icons.svg#i-eye" /></svg>
                </button>
              </div>
            </div>
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-2)">
              <input type="checkbox" name="remember" [(ngModel)]="remember"
                style="accent-color:var(--acc-info)" />
              Keep me signed in on this workstation
            </label>
            @if (error()) {
              <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
            }
            <button class="btn primary" type="submit" style="width:100%;height:36px;justify-content:center">
              Sign in
              <span class="kbd">↵</span>
            </button>
          </form>

          <div style="display:flex;align-items:center;gap:10px;color:var(--text-3);font-size:11px;letter-spacing:.04em;text-transform:uppercase">
            <span style="flex:1;height:1px;background:var(--border)"></span>
            <span>or continue with</span>
            <span style="flex:1;height:1px;background:var(--border)"></span>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
            <button class="btn ghost" style="justify-content:center;border:1px solid var(--border)">SAML SSO</button>
            <button class="btn ghost" style="justify-content:center;border:1px solid var(--border)">Google</button>
            <button class="btn ghost" style="justify-content:center;border:1px solid var(--border)">Okta</button>
          </div>

          <p style="font-size:13px;color:var(--text-2)">
            First time here?
            <a routerLink="/signup" style="color:var(--acc-info-fg)">Request access →</a>
          </p>

          <div style="margin-top:auto;padding-top:32px;display:flex;justify-content:space-between;font-size:11px;color:var(--text-3)">
            <span>© 2026 Haspel Capital · Paper-trading research platform</span>
            <span class="mono">Privacy · Terms · Status</span>
          </div>
        </div>
      </section>

      <aside class="auth-art-col">
        <div class="auth-art-frame">
          <div style="display:flex;align-items:center;gap:8px;font-family:var(--font-mono);font-size:11px;color:var(--text-2);padding-bottom:8px;border-bottom:1px solid var(--border)">
            <span style="width:6px;height:6px;border-radius:50%;background:var(--acc-info);animation:pulse 1.4s ease-in-out infinite"></span>
            <span>S&amp;P 500 · indicative · NY 12:48 ET</span>
            <span style="margin-left:auto">2026-05-19</span>
          </div>

          <svg viewBox="0 0 720 380" preserveAspectRatio="none" style="width:100%;height:300px">
            <defs>
              <pattern id="g" width="60" height="60" patternUnits="userSpaceOnUse">
                <path d="M60 0H0V60" fill="none" stroke="var(--chart-grid)" stroke-width="0.5"/>
              </pattern>
            </defs>
            <rect width="720" height="380" fill="url(#g)"/>
            <rect x="0" y="120" width="720" height="80" fill="rgba(91,141,239,0.05)"/>
            <line x1="0" y1="120" x2="720" y2="120" stroke="var(--acc-info)" stroke-width="0.5" stroke-dasharray="3 3"/>
            <line x1="0" y1="200" x2="720" y2="200" stroke="var(--acc-info)" stroke-width="0.5" stroke-dasharray="3 3"/>
            <polyline fill="none" stroke="var(--text)" stroke-width="1.4" stroke-linecap="round"
              points="0,260 60,250 120,230 180,240 240,210 300,220 360,190 420,200 480,170 540,150 600,140 660,120 720,110">
              <animate attributeName="stroke-dasharray" from="0,2000" to="2000,0" dur="2.4s" begin="0.6s" fill="freeze"/>
            </polyline>
            <g font-family="JetBrains Mono" font-size="9" fill="var(--chart-axis)">
              <text x="6" y="14">5,840</text>
              <text x="6" y="74">5,820</text>
              <text x="6" y="134">5,800</text>
              <text x="6" y="194">5,780</text>
              <text x="6" y="254">5,760</text>
              <text x="40" y="372">Mar 24</text>
              <text x="220" y="372">Apr 14</text>
              <text x="400" y="372">Apr 30</text>
              <text x="600" y="372">May 12</text>
            </g>
            <g>
              <circle cx="720" cy="110" r="3" fill="var(--acc-long)"/>
              <line x1="660" y1="110" x2="720" y2="110" stroke="var(--acc-long)" stroke-dasharray="2 2"/>
              <text x="690" y="100" font-family="JetBrains Mono" font-size="10" fill="var(--acc-long-fg)">5,872</text>
            </g>
          </svg>

          <blockquote style="font-family:var(--font-serif);font-size:32px;line-height:38px;letter-spacing:-0.018em;color:var(--text);margin:0">
            Eight voices, <em style="color:var(--text-2)">one ticket.</em>
            <span style="display:block;font-size:18px;line-height:26px;color:var(--text-2);font-style:italic;margin-top:8px">A council that argues with itself so you don't have to.</span>
          </blockquote>

          <div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-3);margin-top:6px">
            <div class="mono-tile lg">H</div>
            <span>Haspel · institutional research, paper-traded</span>
          </div>

          <div class="tape" style="border-top:1px solid var(--border);padding-top:10px;margin-top:auto">
            <span><span class="mono" style="color:var(--text)">SPY</span> <span class="mono">587.20</span> <span class="up mono">▲ 0.21%</span></span>
            <span><span class="mono" style="color:var(--text)">QQQ</span> <span class="mono">502.40</span> <span class="up mono">▲ 0.42%</span></span>
            <span><span class="mono" style="color:var(--text)">IWM</span> <span class="mono">218.10</span> <span class="down mono">▼ 0.18%</span></span>
            <span><span class="mono" style="color:var(--text)">VIX</span> <span class="mono">13.84</span> <span class="down mono">▼ 2.1%</span></span>
            <span><span class="mono" style="color:var(--text)">DXY</span> <span class="mono">104.12</span> <span class="up mono">▲ 0.08%</span></span>
            <span><span class="mono" style="color:var(--text)">10Y</span> <span class="mono">4.21%</span> <span class="down mono">▼ 3 bps</span></span>
          </div>
        </div>
      </aside>
    </div>
  `,
})
export class LoginPage {
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  email = '';
  password = '';
  remember = true;
  showPwd = signal(false);
  error = signal<string | null>(null);

  submit(): void {
    this.error.set(null);
    this.auth.login(this.email, this.password).subscribe({
      next: () => this.router.navigateByUrl('/'),
      error: () => this.error.set('Invalid email or password'),
    });
  }
}
