import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { StrategiesStore } from '../../abstraction/strategies.store';
import {
  DEFAULT_SCREENER_WEIGHTS,
  SCREENER_WEIGHT_LABELS,
  SCREENER_WEIGHT_TOOLTIPS,
  STRATEGY_KIND_DESCRIPTIONS,
  STRATEGY_KIND_OPTIONS,
  StrategyKind,
} from '../../core/models/strategy.model';
import { InfoTooltipComponent } from '../shared/info-tooltip.component';

@Component({
  selector: 'hf-strategies-new',
  standalone: true,
  imports: [FormsModule, RouterLink, InfoTooltipComponent],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">New strategy</h1>
        <a routerLink="/strategies" class="text-blue-600 hover:underline">Strategies</a>
      </header>

      <form (ngSubmit)="submit()" class="bg-white p-6 rounded shadow max-w-3xl space-y-4">
        <label class="block text-sm font-medium">
          Name
          <hf-info text="A human-friendly label for this strategy. Used in the dashboard and the strategies list — doesn't affect behaviour." />
          <input name="name" [(ngModel)]="name" required
                 class="mt-1 w-full border rounded px-3 py-2" />
        </label>

        <label class="block text-sm font-medium">
          Strategy kind
          <hf-info text="Long-only: longs only (no shorts). Short-only: shorts only (no longs). Long/Short: both sides, directional net. Market-neutral: both sides, net=0." />
          <select name="kind" [(ngModel)]="kind" (ngModelChange)="onKindChange($event)"
                  class="mt-1 w-full border rounded px-3 py-2">
            @for (k of kindOptions; track k.value) {
              <option [value]="k.value">{{ k.label }}</option>
            }
          </select>
          <p class="mt-2 text-xs font-normal text-gray-600">{{ kindDescription() }}</p>
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="block text-sm font-medium">
            Universe
            <hf-info text="The investable pool of tickers the screener evaluates each cycle. Pick a named universe (e.g. sp500_top_200) — only its active members on the as-of date are scored." />
            <select name="universe" [(ngModel)]="universe" required
                    class="mt-1 w-full border rounded px-3 py-2">
              @for (u of store.universes(); track u.id) {
                <option [value]="u.id">{{ u.name }} ({{ u.member_count }})</option>
              }
            </select>
          </label>
          <label class="block text-sm font-medium">
            Portfolio
            <hf-info text="The simulated account this strategy trades against. Holds cash + positions; the rebalancer computes orders relative to it. Create a starter one if you don't have any." />
            <select name="portfolio" [(ngModel)]="portfolio" required
                    class="mt-1 w-full border rounded px-3 py-2">
              @for (p of store.portfolios(); track p.id) {
                <option [value]="p.id">{{ p.name }} (\${{ p.cash_balance }})</option>
              }
            </select>
            <button type="button" (click)="createPortfolio()"
                    class="text-xs text-blue-600 hover:underline mt-1">
              + Create starter portfolio
            </button>
          </label>
        </div>

        <fieldset class="border rounded p-4 grid grid-cols-2 gap-3">
          <legend class="text-sm font-medium px-1">Construction targets</legend>
          <label class="text-sm">
            Target gross
            <hf-info text="Total dollar exposure as a fraction of portfolio value (|longs| + |shorts|). 1.50 means 150% gross — e.g. 100% long + 50% short." />
            <input name="g" type="number" step="0.05" min="0.5" max="3.0"
                   [(ngModel)]="targetGross"
                   class="mt-1 w-full border rounded px-2 py-1" />
          </label>
          @if (kind !== 'market_neutral') {
            <label class="text-sm">
              Target net
              <hf-info text="Long exposure minus short exposure as a fraction of portfolio value. 0.50 = +50% net (long-biased). 0 = market neutral. Negative = short-biased." />
              <input name="n" type="number" step="0.05" min="-1.0" max="2.0"
                     [(ngModel)]="targetNet"
                     class="mt-1 w-full border rounded px-2 py-1" />
            </label>
          }
          <label class="text-sm">
            Max position pct
            <hf-info text="No single name can exceed this fraction of portfolio value. 0.03 = 3% per name. Overflow above the cap is redistributed to other names." />
            <input name="mp" type="number" step="0.005" min="0.005" max="0.20"
                   [(ngModel)]="maxPosition"
                   class="mt-1 w-full border rounded px-2 py-1" />
          </label>
          <label class="text-sm">
            Max sector pct
            <hf-info text="No single GICS sector can exceed this fraction of |gross|. Breaches are scaled down proportionally with one pass." />
            <input name="ms" type="number" step="0.05" min="0.05" max="0.60"
                   [(ngModel)]="maxSector"
                   class="mt-1 w-full border rounded px-2 py-1" />
          </label>
          @if (kind !== 'short_only') {
            <label class="text-sm">
              Top K longs
              <hf-info text="How many top-ranked long candidates the screener surfaces each cycle. Each one gets a full council run, so higher K = more cost + latency." />
              <input name="kl" type="number" min="1" max="50"
                     [(ngModel)]="topLongs"
                     class="mt-1 w-full border rounded px-2 py-1" />
            </label>
          }
          @if (kind !== 'long_only') {
            <label class="text-sm">
              Top K shorts
              <hf-info text="How many top-ranked short candidates the screener surfaces each cycle. Each gets a council run; non-locatable names (HTB) are dropped automatically." />
              <input name="ks" type="number" min="0" max="50"
                     [(ngModel)]="topShorts"
                     class="mt-1 w-full border rounded px-2 py-1" />
            </label>
          }
          <label class="text-sm">
            Cost ceiling per cycle (USD)
            <hf-info text="Hard cap on LLM spend per cycle. If the estimated cost for K longs + K shorts exceeds this, the cycle trims K (proportionally) before dispatching the council fan-out." />
            <input name="cc" type="number" step="0.5" min="0.5"
                   [(ngModel)]="costCeiling"
                   class="mt-1 w-full border rounded px-2 py-1" />
          </label>
          <label class="text-sm">
            Model preset
            <hf-info text="Which model preset the council uses. 'frugal' = OpenRouter Qwen/Llama (cheap), 'hybrid' = mix, 'quality' = Sonnet-only, 'dev' = cheapest, 'research' = Sonnet personas + Haiku analysts." />
            <select name="pre" [(ngModel)]="modelPreset"
                    class="mt-1 w-full border rounded px-2 py-1">
              <option value="dev">dev</option>
              <option value="frugal">frugal</option>
              <option value="hybrid">hybrid</option>
              <option value="research">research</option>
              <option value="quality">quality</option>
            </select>
          </label>
        </fieldset>

        <fieldset class="border rounded p-4">
          <legend class="text-sm font-medium px-1">Screener weights</legend>
          <p class="text-xs text-gray-500 mb-2">
            How much each feature contributes to the ranking. Higher = more weight.
            Hover the (!) icon on each row for what it does.
          </p>
          <div class="grid grid-cols-2 gap-2">
            @for (key of weightKeys; track key) {
              <label class="text-xs flex items-center gap-2">
                <span class="w-56 inline-flex items-center">
                  {{ label(key) }}
                  <hf-info [text]="tooltip(key)" />
                </span>
                <input type="number" step="0.05" min="0" max="5"
                       [ngModel]="weights[key]"
                       (ngModelChange)="weights[key] = $event"
                       [name]="'w_' + key"
                       class="w-20 border rounded px-2 py-1" />
              </label>
            }
          </div>
        </fieldset>

        @if (error()) {
          <p class="text-red-600 text-sm">{{ error() }}</p>
        }

        <button type="submit" [disabled]="submitting()"
                class="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50">
          {{ submitting() ? 'Creating…' : 'Create strategy' }}
        </button>
      </form>
    </div>
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
