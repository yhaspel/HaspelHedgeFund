import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';
import {
  DEFAULT_SCREENER_WEIGHTS, SCREENER_WEIGHT_LABELS, SCREENER_WEIGHT_TOOLTIPS,
  STRATEGY_KIND_DESCRIPTIONS, STRATEGY_KIND_OPTIONS, StrategyKind,
} from '../../core/models/strategy.model';
import { InfoTooltipComponent } from '../shared/info-tooltip.component';

@Component({
  selector: 'hf-strategies-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, InfoTooltipComponent, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Strategy setup</div>
          <h1 style="margin-top:6px">New strategy</h1>
        </div>
      </div>

      <form (ngSubmit)="submit()" style="max-width:840px;display:flex;flex-direction:column;gap:18px">
        <section class="card">
          <div class="card-bd" style="display:flex;flex-direction:column;gap:14px">
            <div class="field">
              <label class="lbl">
                Name
                <hf-info text="A human-friendly label for this strategy. Used in the dashboard and the strategies list — doesn't affect behaviour." />
              </label>
              <input class="input sans" name="name" [(ngModel)]="name" required />
            </div>

            <div class="field">
              <label class="lbl">
                Strategy kind
                <hf-info text="Long-only: longs only (no shorts). Short-only: shorts only (no longs). Long/Short: both sides, directional net. Market-neutral: both sides, net=0." />
              </label>
              <select class="input sans" name="kind" [(ngModel)]="kind" (ngModelChange)="onKindChange($event)">
                @for (k of kindOptions; track k.value) {
                  <option [value]="k.value">{{ k.label }}</option>
                }
              </select>
              <p style="font-size:11.5px;color:var(--text-3);margin:4px 0 0">{{ kindDescription() }}</p>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
              <div class="field">
                <label class="lbl">
                  Universe
                  <hf-info text="The investable pool of tickers the screener evaluates each cycle. Pick a named universe (e.g. sp500_top_200) — only its active members on the as-of date are scored." />
                </label>
                <select class="input sans" name="universe" [(ngModel)]="universe" required>
                  @for (u of store.universes(); track u.id) {
                    <option [value]="u.id">{{ u.name }} ({{ u.member_count }})</option>
                  }
                </select>
              </div>
              <div class="field">
                <label class="lbl">
                  Portfolio
                  <hf-info text="The simulated account this strategy trades against. Holds cash + positions; the rebalancer computes orders relative to it." />
                </label>
                <select class="input sans" name="portfolio" [(ngModel)]="portfolio" required>
                  @for (p of store.portfolios(); track p.id) {
                    <option [value]="p.id">{{ p.name }} ($ {{ p.cash_balance }})</option>
                  }
                </select>
                <button type="button" (click)="createPortfolio()"
                  style="font-size:11.5px;color:var(--acc-info-fg);background:transparent;border:0;cursor:pointer;padding:0;margin-top:4px;text-align:left">
                  + Create starter portfolio
                </button>
              </div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="title">Construction targets</span></div>
          <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
            <div class="field">
              <label class="lbl">
                Target gross
                <hf-info text="Total dollar exposure as a fraction of portfolio value (|longs| + |shorts|). 1.50 means 150% gross — e.g. 100% long + 50% short." />
              </label>
              <input class="input" name="g" type="number" step="0.05" min="0.5" max="3.0" [(ngModel)]="targetGross" />
            </div>
            @if (kind !== 'market_neutral') {
              <div class="field">
                <label class="lbl">
                  Target net
                  <hf-info text="Long exposure minus short exposure as a fraction of portfolio value. 0.50 = +50% net (long-biased). 0 = market neutral. Negative = short-biased." />
                </label>
                <input class="input" name="n" type="number" step="0.05" min="-1.0" max="2.0" [(ngModel)]="targetNet" />
              </div>
            }
            <div class="field">
              <label class="lbl">
                Max position pct
                <hf-info text="No single name can exceed this fraction of portfolio value. 0.03 = 3% per name. Overflow above the cap is redistributed to other names." />
              </label>
              <input class="input" name="mp" type="number" step="0.005" min="0.005" max="0.20" [(ngModel)]="maxPosition" />
            </div>
            <div class="field">
              <label class="lbl">
                Max sector pct
                <hf-info text="No single GICS sector can exceed this fraction of |gross|. Breaches are scaled down proportionally with one pass." />
              </label>
              <input class="input" name="ms" type="number" step="0.05" min="0.05" max="0.60" [(ngModel)]="maxSector" />
            </div>
            @if (kind !== 'short_only') {
              <div class="field">
                <label class="lbl">
                  Top K longs
                  <hf-info text="How many top-ranked long candidates the screener surfaces each cycle. Each one gets a full council run, so higher K = more cost + latency." />
                </label>
                <input class="input" name="kl" type="number" min="1" max="50" [(ngModel)]="topLongs" />
              </div>
            }
            @if (kind !== 'long_only') {
              <div class="field">
                <label class="lbl">
                  Top K shorts
                  <hf-info text="How many top-ranked short candidates the screener surfaces each cycle. Each gets a council run; non-locatable names (HTB) are dropped automatically." />
                </label>
                <input class="input" name="ks" type="number" min="0" max="50" [(ngModel)]="topShorts" />
              </div>
            }
            <div class="field">
              <label class="lbl">
                Cost ceiling per cycle (USD)
                <hf-info text="Hard cap on LLM spend per cycle. If the estimated cost for K longs + K shorts exceeds this, the cycle trims K (proportionally) before dispatching the council fan-out." />
              </label>
              <input class="input" name="cc" type="number" step="0.5" min="0.5" [(ngModel)]="costCeiling" />
            </div>
            <div class="field">
              <label class="lbl">
                Model preset
                <hf-info text="Which model preset the council uses. 'frugal' = OpenRouter Qwen/Llama (cheap), 'hybrid' = mix, 'quality' = Sonnet-only, 'dev' = cheapest, 'research' = Sonnet personas + Haiku analysts." />
              </label>
              <select class="input sans" name="pre" [(ngModel)]="modelPreset">
                <option value="dev">dev</option>
                <option value="frugal">frugal</option>
                <option value="hybrid">hybrid</option>
                <option value="research">research</option>
                <option value="quality">quality</option>
              </select>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="title">Screener weights</span></div>
          <div class="card-bd">
            <p style="font-size:11.5px;color:var(--text-3);margin:0 0 10px">
              How much each feature contributes to the ranking. Higher = more weight. Hover the (!) icon on each row for what it does.
            </p>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 24px">
              @for (key of weightKeys; track key) {
                <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-2)">
                  <span style="flex:1;display:inline-flex;align-items:center;gap:4px">
                    {{ label(key) }}
                    <hf-info [text]="tooltip(key)" />
                  </span>
                  <input class="input mono" type="number" step="0.05" min="0" max="5"
                    [ngModel]="weights[key]"
                    (ngModelChange)="weights[key] = $event"
                    [name]="'w_' + key"
                    style="width:80px;height:26px;padding:0 8px;font-size:12px" />
                </label>
              }
            </div>
          </div>
        </section>

        @if (error()) {
          <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
        }

        <div style="display:flex;gap:8px">
          <a class="btn ghost" routerLink="/strategies">Cancel</a>
          <button type="submit" class="btn primary" [disabled]="submitting()"
            style="flex:1;height:36px;justify-content:center">
            {{ submitting() ? 'Creating…' : 'Create strategy' }}
          </button>
        </div>
      </form>
    </hf-app-shell>
  `,
})
export class StrategiesNewPage implements OnInit {
  readonly store = inject(StrategiesStore);
  private readonly router = inject(Router);

  name = 'Daily L/S 100/50';
  kind: StrategyKind = 'long_short';
  readonly kindOptions = STRATEGY_KIND_OPTIONS;
  universe: number | null = null;
  portfolio: number | null = null;
  targetGross = 1.5;
  targetNet = 0.5;
  maxPosition = 0.03;
  maxSector = 0.25;
  topLongs = 10;
  topShorts = 5;
  costCeiling = 2.0;
  modelPreset = 'frugal';
  weights: Record<string, number> = { ...DEFAULT_SCREENER_WEIGHTS };
  weightKeys = Object.keys(DEFAULT_SCREENER_WEIGHTS);
  submitting = signal(false);
  error = signal<string | null>(null);

  kindDescription(): string { return STRATEGY_KIND_DESCRIPTIONS[this.kind]; }

  onKindChange(k: StrategyKind): void {
    if (k === 'long_only') {
      this.topShorts = 0;
      if (this.targetNet < 0) this.targetNet = Math.abs(this.targetNet);
    } else if (k === 'short_only') {
      this.topLongs = 0;
      if (this.targetNet > 0) this.targetNet = -Math.abs(this.targetNet);
    } else if (k === 'market_neutral') {
      this.targetNet = 0;
    }
  }

  label(k: string): string { return SCREENER_WEIGHT_LABELS[k] ?? k; }
  tooltip(k: string): string { return SCREENER_WEIGHT_TOOLTIPS[k] ?? ''; }

  ngOnInit(): void {
    this.store.loadUniverses().subscribe((us) => {
      if (us.length && this.universe === null) this.universe = us[0].id;
    });
    this.store.loadPortfolios().subscribe((ps) => {
      if (ps.length && this.portfolio === null) this.portfolio = ps[0].id;
    });
  }

  createPortfolio(): void {
    this.store.createPortfolio({
      name: 'Default paper portfolio',
      cash_balance: 100000,
    }).subscribe(() => this.store.loadPortfolios().subscribe((ps) => {
      if (ps.length) this.portfolio = ps[ps.length - 1].id;
    }));
  }

  submit(): void {
    if (this.universe === null || this.portfolio === null) {
      this.error.set('Pick a universe and a portfolio first.');
      return;
    }
    this.error.set(null);
    this.submitting.set(true);
    this.store.create({
      name: this.name,
      kind: this.kind,
      universe: this.universe,
      portfolio: this.portfolio,
      target_gross_pct: String(this.targetGross),
      target_net_pct: String(this.targetNet),
      max_position_pct: String(this.maxPosition),
      max_sector_pct: String(this.maxSector),
      top_k_longs: this.topLongs,
      top_k_shorts: this.topShorts,
      cost_ceiling_per_cycle_usd: String(this.costCeiling),
      model_preset: this.modelPreset,
      screener_weights: this.weights,
    }).subscribe({
      next: (s) => this.router.navigate(['/strategies', s.id]),
      error: (e) => {
        this.submitting.set(false);
        this.error.set(e?.error?.detail || JSON.stringify(e?.error) || 'Failed');
      },
    });
  }
}
