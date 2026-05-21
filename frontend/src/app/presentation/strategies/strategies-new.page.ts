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
                <hf-info text="Pick the overall flavor of the strategy. The description right below this dropdown updates to explain the one you've picked in plain language." />
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
            @if (kind !== 'market_neutral' && kind !== 'concentrated_long' && kind !== 'pairs') {
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
              <input class="input" name="mp" type="number" step="0.005" min="0.005" max="0.30" [(ngModel)]="maxPosition" />
            </div>
            <div class="field">
              <label class="lbl">
                Max sector pct
                <hf-info text="No single GICS sector can exceed this fraction of |gross|. Breaches are scaled down proportionally with one pass." />
              </label>
              <input class="input" name="ms" type="number" step="0.05" min="0.05" max="0.60" [(ngModel)]="maxSector" />
            </div>
            @if (kind === 'sector_rotation' || kind === 'global_macro' || kind === 'risk_parity') {
              <div class="field">
                <label class="lbl">
                  Max ETFs held
                  <hf-info text="Maximum number of sector / thematic ETFs the rotation book can hold at once. Default 6." />
                </label>
                <input class="input" name="me" type="number" min="2" max="20" [(ngModel)]="maxEtfsHeld" />
              </div>
              <div class="field">
                <label class="lbl">
                  Per-ETF max %
                  <hf-info text="Hard cap per ETF as a fraction of gross. Default 0.30 (30%)." />
                </label>
                <input class="input" name="pmx" type="number" step="0.05" min="0.10" max="0.60" [(ngModel)]="perEtfMax" />
              </div>
              <div class="field">
                <label class="lbl">
                  Per-ETF min %
                  <hf-info text="Floor per ETF. ETFs below this floor are raised to it (and excess pulled from larger positions). Default 0.05 (5%)." />
                </label>
                <input class="input" name="pmn" type="number" step="0.01" min="0.01" max="0.20" [(ngModel)]="perEtfMin" />
              </div>
              @if (kind !== 'risk_parity') {
              <div class="field">
                <label class="lbl">
                  Council v2 (screener-led)
                  <hf-info text="When on: the screener's sector ranking drives selection; the council can only veto on a strongly bearish persona vote (≥ threshold) or risk-manager veto. When off: full per-name council runs on each ETF (legacy P2h behaviour — frequently yields empty books)." />
                </label>
                <input class="check" name="scv2" type="checkbox" [(ngModel)]="useSectorCouncilV2" />
              </div>
              <div class="field">
                <label class="lbl">
                  Bearish veto threshold
                  <hf-info text="A persona must vote bearish at this confidence (0–1) or higher to veto a screener pick. Default 0.70. Lower = council blocks more often; higher = council rarely blocks." />
                </label>
                <input class="input" name="bvt" type="number" step="0.05" min="0.30" max="0.95" [(ngModel)]="bearishVetoThreshold" [disabled]="!useSectorCouncilV2" />
              </div>
              }
            }
            @if (kind === 'risk_parity') {
              <div class="field">
                <label class="lbl">
                  Vol lookback (days)
                  <hf-info text="Trailing-window size used to estimate each sleeve's daily-return volatility. Default 60." />
                </label>
                <input class="input" name="vw" type="number" min="20" max="252" [(ngModel)]="volWindowDays" />
              </div>
              <div class="field">
                <label class="lbl">
                  Rebalance band (relative)
                  <hf-info text="Sleeves are not re-traded unless any sleeve has drifted more than this fraction from its target. 0.05 = 5%. Keeps turnover low (vol drifts daily, weights drift daily; without a band you trade every day and die by costs)." />
                </label>
                <input class="input" name="rb" type="number" step="0.01" min="0.01" max="0.30" [(ngModel)]="rebalanceBand" />
              </div>
              <div class="field">
                <label class="lbl">
                  Council veto enabled
                  <hf-info text="Off (default): pure deterministic inverse-vol — zero LLM cost, identical to a baseline 'just inverse-vol weight a sector basket' strategy. On: the trimmed council may vote to exclude a sleeve with a reason (sleeve's weight is redistributed across the rest)." />
                </label>
                <input class="check" name="ecv" type="checkbox" [(ngModel)]="enableCouncilVeto" />
              </div>
            }
            @if (kind === 'pairs') {
              <div class="field">
                <label class="lbl">
                  Open-trade trigger
                  <hf-info text="How unusually wide the gap between the two stocks has to be before opening a trade — measured in 'how many normal-day-sized moves' the gap currently is. 2.0 means open when the gap is twice its typical size. Higher = wait for a bigger dislocation (fewer trades, but each one is more dramatic). Default 2.0." />
                </label>
                <input class="input" name="pez" type="number" step="0.1" min="0.5" max="5.0" [(ngModel)]="pairEntryZ" />
              </div>
              <div class="field">
                <label class="lbl">
                  Close-trade trigger
                  <hf-info text="How close the gap has to be back to its normal size before the trade is closed and profit taken. 0.5 = close once the gap is half a normal-day-move from average. Lower = waits for fuller mean-reversion (bigger wins per trade but holds longer). Default 0.5." />
                </label>
                <input class="input" name="pxz" type="number" step="0.1" min="0.0" max="2.0" [(ngModel)]="pairExitZ" />
              </div>
              <div class="field">
                <label class="lbl">
                  Bail-out trigger
                  <hf-info text="If the gap keeps widening instead of closing, this is the 'admit defeat' line. 4.0 means once the gap is 4× its normal size, the trade is force-closed at a loss — the assumption is the relationship has broken and waiting longer only hurts more. Default 4.0." />
                </label>
                <input class="input" name="psz" type="number" step="0.5" min="2.0" max="10.0" [(ngModel)]="pairStopZ" />
              </div>
              <div class="field">
                <label class="lbl">
                  Max simultaneous pairs
                  <hf-info text="The most pairs the strategy will hold open at the same time. More pairs = more diversification but more trading. Default 8." />
                </label>
                <input class="input" name="pmh" type="number" min="1" max="30" [(ngModel)]="pairMaxHeld" />
              </div>
              <div class="field">
                <label class="lbl">
                  Capital per pair
                  <hf-info text="How much of the portfolio each pair gets (split between its two legs). 0.05 = 5% of the account per pair, so 8 pairs ≈ 40% long + 40% short = 80% gross exposure. Default 0.05 (5%)." />
                </label>
                <input class="input" name="pnp" type="number" step="0.01" min="0.01" max="0.30" [(ngModel)]="pairNotionalPct" />
              </div>
              <div class="field">
                <label class="lbl">
                  Statistical-fit cutoff
                  <hf-info text="A numerical sanity check: how confident the math has to be that this pair's gap actually mean-reverts (rather than drifting apart forever). Lower = stricter, fewer false positives. 0.05 = roughly 'less than 5% chance this is a spurious match.' Default 0.05." />
                </label>
                <input class="input" name="ppm" type="number" step="0.01" min="0.01" max="0.50" [(ngModel)]="pairCointegrationPMax" />
              </div>
              <div class="field">
                <label class="lbl">
                  Minimum co-movement
                  <hf-info text="How tightly the two stocks have to have moved together historically. 0.70 means 'their day-to-day returns are 70%+ correlated.' Higher = only the very tightest pairs qualify. Default 0.70." />
                </label>
                <input class="input" name="pcm" type="number" step="0.05" min="0.30" max="0.99" [(ngModel)]="pairCorrelationMin" />
              </div>
              <div class="field">
                <label class="lbl">
                  History window (days)
                  <hf-info text="How many trading days of past prices to look at when measuring how tightly the pair moves together and what the 'normal' gap is. 252 ≈ one year. More days = more stable estimates but slower to react to a changed relationship. Default 252." />
                </label>
                <input class="input" name="pld" type="number" min="60" max="504" [(ngModel)]="pairLookbackDays" />
              </div>
              <div class="field">
                <label class="lbl">
                  AI council sanity-check
                  <hf-info text="Off (default): the strategy is fully mechanical, no LLM cost. On: before opening each pair, an AI panel reads the news and earnings on both companies and votes 'trade' (the gap is just noise) or 'skip' (one company has a real problem driving the gap — it won't close). Skipped candidates are dropped. Adds LLM cost per cycle but filters out trap trades." />
                </label>
                <input class="check" name="epc" type="checkbox" [(ngModel)]="enablePairCouncil" />
              </div>
              @if (enablePairCouncil) {
                <div class="field">
                  <label class="lbl">
                    Council confidence floor
                    <hf-info text="How sure the AI council has to be before a 'trade' verdict counts. 0.50 = simple majority by confidence. Higher = the council has to really agree before a pair is opened. Default 0.50." />
                  </label>
                  <input class="input" name="pcc" type="number" step="0.05" min="0.30" max="0.95" [(ngModel)]="pairCouncilMinConfidence" />
                </div>
              }
            }
            @if (kind === 'concentrated_long') {
              <div class="field">
                <label class="lbl">
                  Min positions
                  <hf-info text="Minimum number of high-conviction names that must clear the confidence bar before the strategy emits any target. If fewer clear, no new target is emitted and the existing book is held — concentrated managers would rather hold cash than buy a 4th-best idea." />
                </label>
                <input class="input" name="minP" type="number" min="1" max="20" [(ngModel)]="minPositions" />
              </div>
              <div class="field">
                <label class="lbl">
                  Max positions
                  <hf-info text="Hard cap on the book size. Plan default is 15 (Buffett/Ackman territory)." />
                </label>
                <input class="input" name="maxP" type="number" min="2" max="25" [(ngModel)]="maxPositions" />
              </div>
              <div class="field">
                <label class="lbl">
                  Min aggregate confidence
                  <hf-info text="A candidate's council aggregate confidence must clear this bar (0–1) to enter the book. Default 0.65 — higher than a diversified book because each position is larger." />
                </label>
                <input class="input" name="minC" type="number" step="0.05" min="0.30" max="0.95" [(ngModel)]="minAggregateConfidence" />
              </div>
            }
            @if (kind !== 'short_only' && kind !== 'concentrated_long' && kind !== 'sector_rotation' && kind !== 'global_macro' && kind !== 'risk_parity' && kind !== 'pairs') {
              <div class="field">
                <label class="lbl">
                  Top K longs
                  <hf-info text="How many top-ranked long candidates the screener surfaces each cycle. Each one gets a full council run, so higher K = more cost + latency." />
                </label>
                <input class="input" name="kl" type="number" min="1" max="50" [(ngModel)]="topLongs" />
              </div>
            }
            @if (kind !== 'long_only' && kind !== 'concentrated_long' && kind !== 'sector_rotation' && kind !== 'global_macro' && kind !== 'risk_parity' && kind !== 'pairs') {
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
          <div class="card-hd">
            <span class="title">Personas</span>
            <span class="eyebrow" style="margin-left:auto;color:var(--text-3)">
              {{ selectedPersonas.length }} selected
            </span>
          </div>
          <div class="card-bd">
            <p style="font-size:11.5px;color:var(--text-3);margin:0 0 10px">
              Which personas debate each candidate. Leaving all selected = full council.
              @if (kind === 'sector_rotation') {
                <span>Sector rotation defaults to the macro trio (Druckenmiller, Damodaran, Burry) — name-centric value investors aren't a great fit for ETF baskets.</span>
              }
            </p>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 24px">
              @for (p of ALL_PERSONAS; track p) {
                <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-2);cursor:pointer">
                  <input type="checkbox" [checked]="selectedPersonas.includes(p)"
                         (change)="togglePersona(p)" [name]="'p_' + p" />
                  <span style="text-transform:capitalize">{{ p }}</span>
                </label>
              }
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
  minPositions = 5;
  maxPositions = 10;
  minAggregateConfidence = 0.65;
  maxEtfsHeld = 6;
  perEtfMax = 0.30;
  perEtfMin = 0.05;
  useSectorCouncilV2 = true;
  bearishVetoThreshold = 0.70;
  volWindowDays = 60;
  rebalanceBand = 0.05;
  enableCouncilVeto = false;
  pairEntryZ = 2.0;
  pairExitZ = 0.5;
  pairStopZ = 4.0;
  pairMaxHeld = 8;
  pairNotionalPct = 0.05;
  pairCointegrationPMax = 0.05;
  pairLookbackDays = 252;
  pairCorrelationMin = 0.70;
  enablePairCouncil = false;
  pairCouncilMinConfidence = 0.50;

  // Persona picker. Recommended defaults per kind are applied in onKindChange
  // and on first render via the constructor below.
  readonly ALL_PERSONAS = [
    'buffett', 'munger', 'graham', 'lynch',
    'wood', 'druckenmiller', 'burry', 'damodaran',
  ];
  readonly RECOMMENDED_PERSONAS: Record<StrategyKind, string[]> = {
    long_only: this.ALL_PERSONAS,
    short_only: this.ALL_PERSONAS,
    long_short: this.ALL_PERSONAS,
    market_neutral: this.ALL_PERSONAS,
    concentrated_long: this.ALL_PERSONAS,
    sector_rotation: ['druckenmiller', 'damodaran', 'burry'],
    global_macro: ['druckenmiller', 'damodaran', 'burry'],
    risk_parity: ['buffett', 'druckenmiller', 'burry'],
    pairs: this.ALL_PERSONAS,
  };
  selectedPersonas: string[] = [...this.ALL_PERSONAS];

  togglePersona(p: string): void {
    const i = this.selectedPersonas.indexOf(p);
    if (i >= 0) this.selectedPersonas.splice(i, 1);
    else this.selectedPersonas.push(p);
  }
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
    } else if (k === 'concentrated_long') {
      this.topShorts = 0;
      this.targetGross = 1.0;
      this.targetNet = 1.0;
      this.maxPosition = 0.25;
      this.maxSector = 0.60;
    } else if (k === 'sector_rotation') {
      this.topLongs = 0;
      this.topShorts = 0;
      this.targetGross = 1.0;
      this.targetNet = 1.0;
      this.maxPosition = 0.30;
      this.maxSector = 1.0;
      const sectorUni = this.store.universes().find((u) => u.name === 'sector_etfs');
      if (sectorUni) this.universe = sectorUni.id;
    } else if (k === 'global_macro') {
      this.topLongs = 0;
      this.topShorts = 0;
      this.targetGross = 1.0;
      this.targetNet = 1.0;
      this.maxPosition = 0.30;
      this.maxSector = 1.0;
      this.maxEtfsHeld = 8;
      this.perEtfMax = 0.30;
      this.perEtfMin = 0.05;
      const macroUni = this.store.universes().find((u) => u.name === 'macro_etfs');
      if (macroUni) this.universe = macroUni.id;
    } else if (k === 'pairs') {
      this.topLongs = 0;
      this.topShorts = 0;
      this.targetGross = 0.50;
      this.targetNet = 0.0;
      this.maxPosition = 0.05;
      this.maxSector = 1.0;
    } else if (k === 'risk_parity') {
      this.topLongs = 0;
      this.topShorts = 0;
      this.targetGross = 1.0;
      this.targetNet = 1.0;
      this.perEtfMax = 0.50;
      this.perEtfMin = 0.02;
      this.maxEtfsHeld = 12;
      const rpUni = this.store.universes().find((u) => u.name === 'risk_parity_sleeves');
      if (rpUni) this.universe = rpUni.id;
    }
    this.selectedPersonas = [...this.RECOMMENDED_PERSONAS[k]];
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
    const payload: Record<string, unknown> = {
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
      // Empty list = no persona filter on the backend (full default council);
      // otherwise pass the explicit picks. Either way, the user owns the choice.
      personas: this.selectedPersonas.length === this.ALL_PERSONAS.length
        ? []
        : [...this.selectedPersonas],
    };
    if (this.kind === 'concentrated_long') {
      payload['min_positions'] = this.minPositions;
      payload['max_positions'] = this.maxPositions;
      payload['min_aggregate_confidence'] = String(this.minAggregateConfidence);
    }
    if (this.kind === 'sector_rotation') {
      payload['max_etfs_held'] = this.maxEtfsHeld;
      payload['per_etf_max_pct'] = String(this.perEtfMax);
      payload['per_etf_min_pct'] = String(this.perEtfMin);
      payload['use_sector_council_v2'] = this.useSectorCouncilV2;
      payload['bearish_veto_threshold'] = String(this.bearishVetoThreshold);
    }
    if (this.kind === 'global_macro') {
      payload['max_etfs_held'] = this.maxEtfsHeld;
      payload['per_etf_max_pct'] = String(this.perEtfMax);
      payload['per_etf_min_pct'] = String(this.perEtfMin);
      payload['bearish_veto_threshold'] = String(this.bearishVetoThreshold);
    }
    if (this.kind === 'risk_parity') {
      payload['per_etf_max_pct'] = String(this.perEtfMax);
      payload['per_etf_min_pct'] = String(this.perEtfMin);
      payload['vol_window_days'] = this.volWindowDays;
      payload['rebalance_band_pct'] = String(this.rebalanceBand);
      payload['enable_council_veto'] = this.enableCouncilVeto;
    }
    if (this.kind === 'pairs') {
      payload['pair_entry_z'] = String(this.pairEntryZ);
      payload['pair_exit_z'] = String(this.pairExitZ);
      payload['pair_stop_z'] = String(this.pairStopZ);
      payload['pair_max_held'] = this.pairMaxHeld;
      payload['pair_notional_pct'] = String(this.pairNotionalPct);
      payload['pair_cointegration_p_max'] = String(this.pairCointegrationPMax);
      payload['pair_correlation_min'] = String(this.pairCorrelationMin);
      payload['pair_lookback_days'] = this.pairLookbackDays;
      payload['enable_pair_council'] = this.enablePairCouncil;
      payload['pair_council_min_confidence'] = String(this.pairCouncilMinConfidence);
    }
    this.store.create(payload as any).subscribe({
      next: (s) => this.router.navigate(['/strategies', s.id]),
      error: (e) => {
        this.submitting.set(false);
        this.error.set(e?.error?.detail || JSON.stringify(e?.error) || 'Failed');
      },
    });
  }
}
