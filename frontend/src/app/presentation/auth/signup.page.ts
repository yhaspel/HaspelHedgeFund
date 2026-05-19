import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

interface Persona {
  k: string; mono: string; name: string; sub: string; stance: 'bull' | 'bear' | 'neut'; conf: number; thesis: string;
}

@Component({
  selector: 'hf-signup',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="auth">
      <section class="auth-form-col">
        <div class="auth-form-wrap">
          <div style="display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text-2)">
            <div style="width:28px;height:28px;background:var(--surface-2);border:1px solid var(--border-2);border-radius:6px;display:grid;place-items:center">
              <svg width="16" height="16"><use href="/icons.svg#i-logo" /></svg>
            </div>
            <span>Haspel</span>
            <span style="color:var(--text-3)">·</span>
            <span class="mono">v 0.9</span>
            <span style="color:var(--text-3)">·</span>
            <span class="mono">paper</span>
            <span style="margin-left:auto;font-size:12px">
              Already have access?
              <a routerLink="/login" style="color:var(--acc-info-fg)">Sign in →</a>
            </span>
          </div>

          <div class="stepper">
            <div class="dot" [class.done]="step()>1" [class.now]="step()===1">
              @if(step()>1){<svg width="11" height="11"><use href="/icons.svg#i-check" /></svg>}@else{1}
            </div>
            <div class="line" [class.active]="step()>=2"></div>
            <div class="dot" [class.done]="step()>2" [class.now]="step()===2">
              @if(step()>2){<svg width="11" height="11"><use href="/icons.svg#i-check" /></svg>}@else{2}
            </div>
            <div class="line" [class.active]="step()>=3"></div>
            <div class="dot" [class.now]="step()===3">3</div>
          </div>

          <div>
            <h1>Request access</h1>
            <p class="sub" style="margin-top:8px">
              Haspel is invitation-gated for institutional desks while we build out compliance &amp; data licensing. We turn around most requests within one business day.
            </p>
          </div>

          <form (ngSubmit)="next()" style="display:flex;flex-direction:column;gap:14px">
            @if(step()===1){
              <div class="field">
                <label class="lbl">Work email</label>
                <input class="input sans" type="email" name="email" [(ngModel)]="email"
                  (blur)="detectOrg()" placeholder="evelyn.haspel@bridgewater-quants.com" required />
              </div>
              @if(org()){
                <div style="display:flex;align-items:center;gap:8px;background:var(--acc-long-soft);border:1px solid var(--acc-long-soft);border-radius:6px;padding:8px 10px;font-size:12px;color:var(--acc-long-fg)">
                  <svg width="14" height="14"><use href="/icons.svg#i-check" /></svg>
                  Detected <strong style="font-weight:600">{{ org() }}</strong> · SAML SSO available · join existing workspace
                </div>
              } @else if(email().includes('@')){
                <div style="font-size:12px;color:var(--text-3)">No existing workspace — you'll create a new one.</div>
              }
            }

            @if(step()===2){
              <div class="field">
                <label class="lbl">Full name</label>
                <input class="input sans" type="text" name="name" [(ngModel)]="name" placeholder="Evelyn Haspel" required />
              </div>
              <div class="field">
                <label class="lbl">Role</label>
                <div class="seg">
                  @for(r of roles; track r){
                    <button type="button" class="opt" [class.on]="role()===r" (click)="role.set(r)">{{ r }}</button>
                  }
                </div>
              </div>
              <div class="field">
                <label class="lbl">What will you evaluate first? <span style="color:var(--text-3);text-transform:none;letter-spacing:0;font-weight:400">· pick any · informs your tour</span></label>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
                  @for(u of usecases; track u){
                    <label style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--surface);border:1px solid var(--border);border-radius:6px;font-size:13px;cursor:pointer"
                      [style.borderColor]="ucSel().has(u) ? 'var(--acc-info)' : 'var(--border)'"
                      [style.background]="ucSel().has(u) ? 'var(--acc-info-soft)' : 'var(--surface)'">
                      <input type="checkbox" [checked]="ucSel().has(u)" (change)="toggleUc(u)" style="accent-color:var(--acc-info)" />
                      {{ u }}
                    </label>
                  }
                </div>
              </div>
            }

            @if(step()===3){
              <div class="field">
                <label class="lbl">Workspace name</label>
                <input class="input sans" type="text" name="ws" [(ngModel)]="workspace" placeholder="Bridgewater Quants" />
              </div>
              <div class="field">
                <label class="lbl">AUM (approx)</label>
                <input class="input" type="text" name="aum" [(ngModel)]="aum" placeholder="$ 1.2 B" />
              </div>
              <div class="field">
                <label class="lbl">Admin contact</label>
                <input class="input sans" type="email" name="admin" [(ngModel)]="admin" placeholder="admin@…" />
              </div>
              <div class="field">
                <label class="lbl">Password</label>
                <input class="input sans" type="password" name="password" [(ngModel)]="password" minlength="8" required />
              </div>
            }

            @if(error()){
              <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
            }

            <button class="btn primary" type="submit" style="width:100%;height:36px;justify-content:center">
              @if(step()<3){Continue · {{step()}} of 3}@else{Submit request}
              <span class="kbd">⌘↵</span>
            </button>

            <p style="font-size:11px;color:var(--text-3);line-height:16px">
              By continuing you accept the research-use terms and acknowledge Haspel runs on paper accounts only — no orders are routed to a broker.
            </p>
          </form>

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
            <span>The council, summarised</span>
            <span style="margin-left:auto">AAPL · 2026-05-18 · run #2147</span>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;flex:1;overflow:hidden">
            @for(p of personas; track p.k; let i = $index){
              <article class="p-card" style="padding:10px;animation: pmFade 500ms ease-out both"
                [style.animationDelay]="(i * 110) + 'ms'">
                <header>
                  <span class="mono-tile" [class]="'mt-' + p.k">{{ p.mono }}</span>
                  <div style="display:flex;flex-direction:column;line-height:1.2">
                    <span class="name" style="font-size:12px">{{ p.name }}</span>
                    <span class="sub" style="font-size:10.5px">{{ p.sub }}</span>
                  </div>
                  <span class="stance" [class.bull]="p.stance==='bull'" [class.bear]="p.stance==='bear'" [class.neut]="p.stance==='neut'"
                    style="margin-left:auto">{{ p.stance.toUpperCase() }} · {{ p.conf }}</span>
                </header>
                <p style="font-size:11.5px;line-height:16px;color:var(--text-2);margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{{ p.thesis }}</p>
              </article>
            }
          </div>

          <blockquote style="font-family:var(--font-serif);font-size:22px;line-height:28px;letter-spacing:-0.012em;color:var(--text);margin:0">
            Five of eight favour a buy. <em style="color:var(--text-2)">The PM sizes it 2.4%, the CIO ratifies.</em>
            <span style="display:block;font-size:14px;line-height:20px;color:var(--text-2);font-style:italic;margin-top:6px">Your job becomes editorial — not synthesis.</span>
          </blockquote>

          <div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-3)">
            <div class="mono-tile lg">H</div>
            <span>What you'll see on day one · live demo data</span>
          </div>

          <div class="tape" style="border-top:1px solid var(--border);padding-top:10px;margin-top:auto">
            <span><span class="mono" style="color:var(--text)">SPY</span> <span class="mono">587.20</span> <span class="up mono">▲ 0.21%</span></span>
            <span><span class="mono" style="color:var(--text)">QQQ</span> <span class="mono">502.40</span> <span class="up mono">▲ 0.42%</span></span>
            <span><span class="mono" style="color:var(--text)">VIX</span> <span class="mono">13.84</span> <span class="down mono">▼ 2.1%</span></span>
            <span><span class="mono" style="color:var(--text)">10Y</span> <span class="mono">4.21%</span> <span class="down mono">▼ 3 bps</span></span>
          </div>
        </div>
      </aside>
    </div>

    <style>
      @keyframes pmFade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
    </style>
  `,
})
export class SignupPage {
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);

  step = signal(1);
  email = signal('');
  org = signal<string | null>(null);
  name = signal('');
  role = signal<string>('PM');
  workspace = signal('');
  aum = signal('');
  admin = signal('');
  password = '';
  error = signal<string | null>(null);
  ucSel = signal(new Set<string>(['Long/short equity', 'Sector rotation · ETFs']));

  readonly roles = ['PM', 'Analyst', 'CIO', 'Other'];
  readonly usecases = ['Long/short equity', 'Market-neutral pairs', 'Sector rotation · ETFs', 'Concentrated long-only'];
  readonly personas: Persona[] = [
    { k: 'c1', mono: 'WB', name: 'Buffett', sub: 'Macro / quality', stance: 'bull', conf: 84, thesis: 'Services compounding faster than the market is modelling. Hardware is the rails; software is the toll.' },
    { k: 'c2', mono: 'CM', name: 'Munger', sub: 'Mental models', stance: 'bull', conf: 78, thesis: 'Inversion test: for this to be a sell, $3T of ecosystem has to be disintermediated in 36m. Base rate ≈ 0.' },
    { k: 'c3', mono: 'BG', name: 'Graham', sub: 'Quality / net-net', stance: 'neut', conf: 41, thesis: 'No margin of safety in the classical sense. Wait for a 12% drawdown to add.' },
    { k: 'c4', mono: 'CW', name: 'Wood', sub: 'Disruption', stance: 'bull', conf: 69, thesis: 'On-device AI is the second screen rotation of the decade. 17 Pro tier is the unlock.' },
    { k: 'c5', mono: 'SD', name: 'Druckenmiller', sub: 'Macro flow', stance: 'bull', conf: 66, thesis: 'Mega-cap quality is the right ride for an easing cycle that is still front-loaded.' },
    { k: 'c6', mono: 'MB', name: 'Burry', sub: 'Contrarian', stance: 'bear', conf: 58, thesis: 'Consensus long, sell-side raising on every print, factor crowded — that\'s a setup, not a thesis.' },
    { k: 'c7', mono: 'AD', name: 'Damodaran', sub: 'DCF / risk', stance: 'bull', conf: 73, thesis: 'Three methods triangulate to $222. Most sensitive: terminal services margin. 200bp haircut → $196.' },
    { k: 'c8', mono: 'PL', name: 'Lynch', sub: 'GARP', stance: 'neut', conf: 48, thesis: 'PEG ~1.6, stalwart category. Hold what we own; ten-baggers don\'t live in $3T market caps.' },
  ];

  detectOrg(): void {
    const e = this.email();
    const m = e.match(/@([^.]+)\./);
    this.org.set(m ? m[1].split('-').map((s) => s[0].toUpperCase() + s.slice(1)).join(' ') + ' LLC' : null);
  }

  toggleUc(u: string): void {
    const s = new Set(this.ucSel());
    s.has(u) ? s.delete(u) : s.add(u);
    this.ucSel.set(s);
  }

  next(): void {
    if (this.step() < 3) {
      this.step.set(this.step() + 1);
      return;
    }
    this.submit();
  }

  submit(): void {
    this.error.set(null);
    this.auth.signup(this.email(), this.password).subscribe({
      next: () => {
        this.auth.login(this.email(), this.password).subscribe({
          next: () => this.router.navigateByUrl('/'),
        });
      },
      error: () => this.error.set('Could not create account'),
    });
  }
}
