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
import { GlossaryTermComponent } from '../shared/glossary-term.component';
import { PersonaCardComponent } from '../shared/persona-card.component';
import { ALL_PERSONAS as PERSONA_META } from '../../core/models/run.model';
import { STRATEGY_KIND_GUIDE } from '../../core/models/info.model';

@Component({
  selector: 'hf-strategies-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, InfoTooltipComponent, GlossaryTermComponent, AppShellComponent, PersonaCardComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Strategy setup</div>
          <h1 class="mt-1.5">New strategy</h1>
        </div>
      </div>

      <form (ngSubmit)="submit()" class="max-w-[840px] flex flex-col gap-[18px]">
        <section class="card">
          <div class="card-bd flex flex-col gap-3.5">
            <div class="field">
              <label class="lbl" for="strat-name">
                Name
                <hf-info text="Auto-suggested from the strategy kind, universe, and the time you opened this page — and re-suggested whenever you change kind or universe. Type your own name any time to keep it. Used in the dashboard and the strategies list; doesn't affect behaviour." />
              </label>
              <input class="input sans" name="name" [(ngModel)]="name"
                     (ngModelChange)="onNameChange($event)" required  id="strat-name"/>
            </div>

            <div class="field">
              <label class="lbl" for="strat-kind">
                Strategy kind
                <hf-info text="Pick the overall flavor of the strategy. The description right below this dropdown updates to explain the one you've picked in plain language." />
              </label>
              <select class="input sans" name="kind" [(ngModel)]="kind" (ngModelChange)="onKindChange($event)" id="strat-kind">
                @for (k of kindOptions; track k.value) {
                  <option [value]="k.value">{{ k.label }}</option>
                }
              </select>
              <p class="text-[11.5px] text-text-3 m-0 mt-1">{{ kindDescription() }}</p>
              <p class="text-[11.5px] m-0 mt-1.5">
                <a [routerLink]="['/info', kindGuideSlug()]" class="text-[var(--acc-info-fg)]">
                  Read the full guide →
                </a>
              </p>
            </div>

            <div class="grid grid-cols-2 gap-3.5">
              <div class="field">
                <label class="lbl" for="strat-universe">
                  <hf-term key="universe">Universe</hf-term>
                  <hf-info text="The investable pool of tickers the screener evaluates each cycle. Pick a named universe (e.g. sp500_top_200) — only its active members on the as-of date are scored." />
                </label>
                <select class="input sans" name="universe" [(ngModel)]="universe"
                        (ngModelChange)="onUniverseChange()" required id="strat-universe">
                  @for (u of store.universes(); track u.id) {
                    <option [value]="u.id">{{ u.name }} ({{ u.member_count }})</option>
                  }
                </select>
              </div>
              <div class="field">
                <label class="lbl" for="strat-portfolio">
                  Portfolio
                  <hf-info text="The simulated account this strategy trades against. Holds cash + positions; the rebalancer computes orders relative to it." />
                </label>
                <select class="input sans" name="portfolio" [(ngModel)]="portfolio" required id="strat-portfolio">
                  @for (p of strategyPortfolios(); track p.id) {
                    <option [value]="p.id">{{ p.name }} ($ {{ p.cash_balance }})</option>
                  }
                </select>
                <button type="button" (click)="createPortfolio()" class="strat-link-button">
                  + Create starter portfolio
                </button>
              </div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="title">Construction targets</span></div>
          <div class="card-bd grid grid-cols-2 gap-3.5">
            <div class="field">
              <label class="lbl" for="strat-g">
                Target <hf-term key="gross-exposure">gross</hf-term>
                <hf-info text="Total dollar exposure as a fraction of portfolio value (|longs| + |shorts|). 1.50 means 150% gross — e.g. 100% long + 50% short." />
              </label>
              <input class="input" name="g" type="number" step="0.05" min="0.5" max="3.0" [(ngModel)]="targetGross"  id="strat-g"/>
            </div>
            @if (kind !== 'market_neutral' && kind !== 'concentrated_long' && kind !== 'pairs') {
              <div class="field">
                <label class="lbl" for="strat-n">
                  Target <hf-term key="net-exposure">net</hf-term>
                  <hf-info text="Long exposure minus short exposure as a fraction of portfolio value. 0.50 = +50% net (long-biased). 0 = market neutral. Negative = short-biased." />
                </label>
                <input class="input" name="n" type="number" step="0.05" min="-1.0" max="2.0" [(ngModel)]="targetNet"  id="strat-n"/>
              </div>
            }
            <div class="field">
              <label class="lbl" for="strat-mp">
                Max position pct
                <hf-info text="No single name can exceed this fraction of portfolio value. 0.03 = 3% per name. Overflow above the cap is redistributed to other names." />
              </label>
              <input class="input" name="mp" type="number" step="0.005" min="0.005" max="0.30" [(ngModel)]="maxPosition"  id="strat-mp"/>
            </div>
            <div class="field">
              <label class="lbl" for="strat-ms">
                Max sector pct
                <hf-info text="No single GICS sector can exceed this fraction of |gross|. Breaches are scaled down proportionally with one pass." />
              </label>
              <input class="input" name="ms" type="number" step="0.05" min="0.05" max="0.60" [(ngModel)]="maxSector"  id="strat-ms"/>
            </div>
            @if (kind === 'sector_rotation' || kind === 'global_macro' || kind === 'risk_parity') {
              <div class="field">
                <label class="lbl" for="strat-me">
                  Max ETFs held
                  <hf-info text="Maximum number of sector / thematic ETFs the rotation book can hold at once. Default 6." />
                </label>
                <input class="input" name="me" type="number" min="2" max="20" [(ngModel)]="maxEtfsHeld"  id="strat-me"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pmx">
                  Per-ETF max %
                  <hf-info text="Hard cap per ETF as a fraction of gross. Default 0.30 (30%)." />
                </label>
                <input class="input" name="pmx" type="number" step="0.05" min="0.10" max="0.60" [(ngModel)]="perEtfMax"  id="strat-pmx"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pmn">
                  Per-ETF min %
                  <hf-info text="Floor per ETF. ETFs below this floor are raised to it (and excess pulled from larger positions). Default 0.05 (5%)." />
                </label>
                <input class="input" name="pmn" type="number" step="0.01" min="0.01" max="0.20" [(ngModel)]="perEtfMin"  id="strat-pmn"/>
              </div>
              @if (kind !== 'risk_parity') {
              <div class="field">
                <label class="lbl" for="strat-scv2">
                  Council v2 (screener-led)
                  <hf-info text="When on: the screener's sector ranking drives selection; the council can only veto on a strongly bearish persona vote (≥ threshold) or risk-manager veto. When off: full per-name council runs on each ETF (legacy P2h behaviour — frequently yields empty books)." />
                </label>
                <input class="check" name="scv2" type="checkbox" [(ngModel)]="useSectorCouncilV2"  id="strat-scv2"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-bvt">
                  Bearish veto threshold
                  <hf-info text="A persona must vote bearish at this confidence (0–1) or higher to veto a screener pick. Default 0.70. Lower = council blocks more often; higher = council rarely blocks." />
                </label>
                <input class="input" name="bvt" type="number" step="0.05" min="0.30" max="0.95" [(ngModel)]="bearishVetoThreshold" [disabled]="!useSectorCouncilV2"  id="strat-bvt"/>
              </div>
              }
            }
            @if (kind === 'global_macro') {
              <!-- P02i review: asset-class cap editor + inverse policy controls. -->
              <div class="field" data-test="gm-prefer-inverse">
                <label class="lbl" for="strat-pis">
                  Prefer inverse ETF over short
                  <hf-info text="When on (default), bearish macro views prefer holding an inverse ETF (e.g. SH for short SPY exposure) instead of opening a hard short. Inverse ETFs avoid borrow risk but carry tracking decay over time." />
                </label>
                <input class="check" name="pis" type="checkbox" [(ngModel)]="preferInverseEtfOverShort"  id="strat-pis"/>
              </div>
              <div class="field" data-test="gm-max-inverse-days">
                <label class="lbl" for="strat-mih">
                  Max inverse ETF hold (days)
                  <hf-info text="Inverse ETFs decay over long holds. The cycle warns when an inverse position has been held longer than this; the next cycle reduces or replaces it. Default 14." />
                </label>
                <input class="input" name="mih" type="number" min="1" max="90" [(ngModel)]="maxInverseEtfHoldDays"  id="strat-mih"/>
              </div>
              <div class="field" data-test="gm-asset-class-cap-equity">
                <label class="lbl" for="strat-acce">
                  Equity cap
                  <hf-info text="Max fraction of gross exposure allocated to the equity asset class (SPY/QQQ/etc.). 0.50 = 50% cap. Combined with the other class caps, this enforces diversification across asset classes." />
                </label>
                <input class="input" name="acce" type="number" step="0.05" min="0.05" max="1.0"
                  [(ngModel)]="assetClassCapEquity"  id="strat-acce"/>
              </div>
              <div class="field" data-test="gm-asset-class-cap-rates">
                <label class="lbl" for="strat-accr">
                  Rates cap
                  <hf-info text="Max fraction of gross exposure to the rates / duration asset class (TLT/IEF/SHY). Default 0.40." />
                </label>
                <input class="input" name="accr" type="number" step="0.05" min="0.05" max="1.0"
                  [(ngModel)]="assetClassCapRates"  id="strat-accr"/>
              </div>
              <div class="field" data-test="gm-asset-class-cap-commodity">
                <label class="lbl" for="strat-accc">
                  Commodity / FX cap
                  <hf-info text="Max fraction of gross exposure to gold, broad commodities, and FX proxies. Default 0.30." />
                </label>
                <input class="input" name="accc" type="number" step="0.05" min="0.05" max="1.0"
                  [(ngModel)]="assetClassCapCommodity"  id="strat-accc"/>
              </div>
            }
            @if (kind === 'risk_parity') {
              <div class="field">
                <label class="lbl" for="strat-vw">
                  Vol lookback (days)
                  <hf-info text="Trailing-window size used to estimate each sleeve's daily-return volatility. Default 60." />
                </label>
                <input class="input" name="vw" type="number" min="20" max="252" [(ngModel)]="volWindowDays"  id="strat-vw"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-rb">
                  Rebalance band (relative)
                  <hf-info text="Sleeves are not re-traded unless any sleeve has drifted more than this fraction from its target. 0.05 = 5%. Keeps turnover low (vol drifts daily, weights drift daily; without a band you trade every day and die by costs)." />
                </label>
                <input class="input" name="rb" type="number" step="0.01" min="0.01" max="0.30" [(ngModel)]="rebalanceBand"  id="strat-rb"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-ecv">
                  Council veto enabled
                  <hf-info text="Off (default): pure deterministic inverse-vol — zero LLM cost, identical to a baseline 'just inverse-vol weight a sector basket' strategy. On: the trimmed council may vote to exclude a sleeve with a reason (sleeve's weight is redistributed across the rest)." />
                </label>
                <input class="check" name="ecv" type="checkbox" [(ngModel)]="enableCouncilVeto"  id="strat-ecv"/>
              </div>
            }
            @if (kind === 'pairs') {
              <div class="field">
                <label class="lbl" for="strat-pez">
                  Open-trade trigger
                  <hf-info text="How unusually wide the gap between the two stocks has to be before opening a trade — measured in 'how many normal-day-sized moves' the gap currently is. 2.0 means open when the gap is twice its typical size. Higher = wait for a bigger dislocation (fewer trades, but each one is more dramatic). Default 2.0." />
                </label>
                <input class="input" name="pez" type="number" step="0.1" min="0.5" max="5.0" [(ngModel)]="pairEntryZ"  id="strat-pez"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pxz">
                  Close-trade trigger
                  <hf-info text="How close the gap has to be back to its normal size before the trade is closed and profit taken. 0.5 = close once the gap is half a normal-day-move from average. Lower = waits for fuller mean-reversion (bigger wins per trade but holds longer). Default 0.5." />
                </label>
                <input class="input" name="pxz" type="number" step="0.1" min="0.0" max="2.0" [(ngModel)]="pairExitZ"  id="strat-pxz"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-psz">
                  Bail-out trigger
                  <hf-info text="If the gap keeps widening instead of closing, this is the 'admit defeat' line. 4.0 means once the gap is 4× its normal size, the trade is force-closed at a loss — the assumption is the relationship has broken and waiting longer only hurts more. Default 4.0." />
                </label>
                <input class="input" name="psz" type="number" step="0.5" min="2.0" max="10.0" [(ngModel)]="pairStopZ"  id="strat-psz"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pmh">
                  Max simultaneous pairs
                  <hf-info text="The most pairs the strategy will hold open at the same time. More pairs = more diversification but more trading. Default 8." />
                </label>
                <input class="input" name="pmh" type="number" min="1" max="30" [(ngModel)]="pairMaxHeld"  id="strat-pmh"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pnp">
                  Capital per pair
                  <hf-info text="How much of the portfolio each pair gets (split between its two legs). 0.05 = 5% of the account per pair, so 8 pairs ≈ 40% long + 40% short = 80% gross exposure. Default 0.05 (5%)." />
                </label>
                <input class="input" name="pnp" type="number" step="0.01" min="0.01" max="0.30" [(ngModel)]="pairNotionalPct"  id="strat-pnp"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-ppm">
                  Statistical-fit cutoff
                  <hf-info text="A numerical sanity check: how confident the math has to be that this pair's gap actually mean-reverts (rather than drifting apart forever). Lower = stricter, fewer false positives. 0.05 = roughly 'less than 5% chance this is a spurious match.' Default 0.05." />
                </label>
                <input class="input" name="ppm" type="number" step="0.01" min="0.01" max="0.50" [(ngModel)]="pairCointegrationPMax"  id="strat-ppm"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pcm">
                  Minimum co-movement
                  <hf-info text="How tightly the two stocks have to have moved together historically. 0.70 means 'their day-to-day returns are 70%+ correlated.' Higher = only the very tightest pairs qualify. Default 0.70." />
                </label>
                <input class="input" name="pcm" type="number" step="0.05" min="0.30" max="0.99" [(ngModel)]="pairCorrelationMin"  id="strat-pcm"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-pld">
                  History window (days)
                  <hf-info text="How many trading days of past prices to look at when measuring how tightly the pair moves together and what the 'normal' gap is. 252 ≈ one year. More days = more stable estimates but slower to react to a changed relationship. Default 252." />
                </label>
                <input class="input" name="pld" type="number" min="60" max="504" [(ngModel)]="pairLookbackDays"  id="strat-pld"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-epc">
                  AI council sanity-check
                  <hf-info text="Off (default): the strategy is fully mechanical, no LLM cost. On: before opening each pair, an AI panel reads the news and earnings on both companies and votes 'trade' (the gap is just noise) or 'skip' (one company has a real problem driving the gap — it won't close). Skipped candidates are dropped. Adds LLM cost per cycle but filters out trap trades." />
                </label>
                <input class="check" name="epc" type="checkbox" [(ngModel)]="enablePairCouncil"  id="strat-epc"/>
              </div>
              @if (enablePairCouncil) {
                <div class="field">
                  <label class="lbl" for="strat-pcc">
                    Council confidence floor
                    <hf-info text="How sure the AI council has to be before a 'trade' verdict counts. 0.50 = simple majority by confidence. Higher = the council has to really agree before a pair is opened. Default 0.50." />
                  </label>
                  <input class="input" name="pcc" type="number" step="0.05" min="0.30" max="0.95" [(ngModel)]="pairCouncilMinConfidence"  id="strat-pcc"/>
                </div>
              }
            }
            <!-- P02f review: surface market-neutral controls -->
            @if (kind === 'market_neutral') {
              <div class="field" data-test="mn-benchmark">
                <label class="lbl" for="strat-bt">
                  Benchmark ticker
                  <hf-info text="Benchmark for beta estimation. Default SPY. Used to compute each candidate's market beta and to estimate the book's portfolio beta." />
                </label>
                <input class="input sans" name="bt" type="text" maxlength="8" [(ngModel)]="benchmarkTicker"  id="strat-bt"/>
              </div>
              <div class="field" data-test="mn-beta-window">
                <label class="lbl" for="strat-bwd">
                  Beta lookback (days)
                  <hf-info text="Rolling-window size used to estimate beta. Default 252 (≈ 1 year of trading days). Shorter windows react faster to regime change but are noisier." />
                </label>
                <input class="input" name="bwd" type="number" min="60" max="504" [(ngModel)]="betaWindowDays"  id="strat-bwd"/>
              </div>
              <div class="field" data-test="mn-tol-dollar">
                <label class="lbl" for="strat-ntd">
                  Dollar neutrality tolerance
                  <hf-info text="Maximum |net dollar exposure| as a fraction of gross. 0.02 = ±2%. Targets outside the band trigger a neutrality breach diagnostic." />
                </label>
                <input class="input" name="ntd" type="number" step="0.005" min="0.005" max="0.10" [(ngModel)]="neutralityToleranceDollar"  id="strat-ntd"/>
              </div>
              <div class="field" data-test="mn-tol-beta">
                <label class="lbl" for="strat-ntb">
                  Beta neutrality tolerance
                  <hf-info text="Maximum |portfolio beta|. 0.05 = ±0.05β. Targets outside the band trigger a breach diagnostic and the constructor scales the book to fit." />
                </label>
                <input class="input" name="ntb" type="number" step="0.01" min="0.01" max="0.30" [(ngModel)]="neutralityToleranceBeta"  id="strat-ntb"/>
              </div>
              <div class="field" data-test="mn-drop-unreliable">
                <label class="lbl" for="strat-dub">
                  Drop on unreliable beta
                  <hf-info text="When on: drop candidates whose beta R² is below the reliability threshold (more conservative, smaller book). When off (default): retain them with β=1 fallback and flag in diagnostics." />
                </label>
                <input class="check" name="dub" type="checkbox" [(ngModel)]="dropOnUnreliableBeta"  id="strat-dub"/>
              </div>
            }
            @if (kind === 'concentrated_long') {
              <div class="field">
                <label class="lbl" for="strat-minP">
                  Min positions
                  <hf-info text="Minimum number of high-conviction names that must clear the confidence bar before the strategy emits any target. If fewer clear, no new target is emitted and the existing book is held — concentrated managers would rather hold cash than buy a 4th-best idea." />
                </label>
                <input class="input" name="minP" type="number" min="1" max="20" [(ngModel)]="minPositions"  id="strat-minP"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-maxP">
                  Max positions
                  <hf-info text="Hard cap on the book size. Plan default is 15 (Buffett/Ackman territory)." />
                </label>
                <input class="input" name="maxP" type="number" min="2" max="25" [(ngModel)]="maxPositions"  id="strat-maxP"/>
              </div>
              <div class="field">
                <label class="lbl" for="strat-minC">
                  Min aggregate confidence
                  <hf-info text="A candidate's council aggregate confidence must clear this bar (0–1) to enter the book. Default 0.65 — higher than a diversified book because each position is larger." />
                </label>
                <input class="input" name="minC" type="number" step="0.05" min="0.30" max="0.95" [(ngModel)]="minAggregateConfidence"  id="strat-minC"/>
              </div>
            }
            @if (kind !== 'short_only' && kind !== 'concentrated_long' && kind !== 'sector_rotation' && kind !== 'global_macro' && kind !== 'risk_parity' && kind !== 'pairs') {
              <div class="field">
                <label class="lbl" for="strat-kl">
                  Top K longs
                  <hf-info text="How many top-ranked long candidates the screener surfaces each cycle. Each one gets a full council run, so higher K = more cost + latency." />
                </label>
                <input class="input" name="kl" type="number" min="1" max="50" [(ngModel)]="topLongs"  id="strat-kl"/>
              </div>
            }
            @if (kind !== 'long_only' && kind !== 'concentrated_long' && kind !== 'sector_rotation' && kind !== 'global_macro' && kind !== 'risk_parity' && kind !== 'pairs') {
              <div class="field">
                <label class="lbl" for="strat-ks">
                  Top K shorts
                  <hf-info text="How many top-ranked short candidates the screener surfaces each cycle. Each gets a council run; non-locatable names (HTB) are dropped automatically." />
                </label>
                <input class="input" name="ks" type="number" min="0" max="50" [(ngModel)]="topShorts"  id="strat-ks"/>
              </div>
            }
            <div class="field">
              <label class="lbl" for="strat-cc">
                Cost ceiling per cycle (USD)
                <hf-info text="Hard cap on LLM spend per cycle. If the estimated cost for K longs + K shorts exceeds this, the cycle trims K (proportionally) before dispatching the council fan-out." />
              </label>
              <input class="input" name="cc" type="number" step="0.5" min="0.5" [(ngModel)]="costCeiling"  id="strat-cc"/>
            </div>
            <div class="field">
              <label class="lbl" for="strat-pre">
                <hf-term key="preset">Model preset</hf-term>
                <hf-info text="Which model preset the council uses. 'frugal' = OpenRouter Qwen/Llama (cheap), 'hybrid' = mix, 'quality' = Sonnet-only, 'dev' = cheapest, 'research' = Sonnet personas + Haiku analysts." />
              </label>
              <select class="input sans" name="pre" [(ngModel)]="modelPreset" id="strat-pre">
                <option value="dev">dev</option>
                <option value="frugal">frugal</option>
                <option value="hybrid">hybrid</option>
                <option value="research">research</option>
                <option value="quality">quality</option>
              </select>
            </div>
            <div class="field col-span-full">
              <label class="flex items-center gap-2 text-2xs text-text-2 cursor-pointer">
                <input type="checkbox" name="autoRunCouncil" [(ngModel)]="autoRunCouncil" />
                <span>
                  Automatically run the council after screening
                  <hf-info text="When ON, the cycle screens and immediately dispatches the council on every shortlisted name (default behavior). When OFF, the cycle stops after screening — you'll see a 'Review' panel on the strategy page where you can approve a subset before any LLM cost is incurred." />
                </span>
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-1 ml-[22px]">
                @if (autoRunCouncil) {
                  The cycle will spend up to your cost ceiling running the council on every screened candidate.
                } @else {
                  The cycle stops at <em>awaiting review</em> after the cheap screener pass. You approve a subset before any LLM cost is incurred.
                }
              </p>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd">
            <span class="title">Personas</span>
            <span class="pill"><span class="dot"></span>{{ selectedPersonas.length }} of {{ ALL_PERSONAS.length }}</span>
          </div>
          <div class="card-bd">
            <p class="text-[11.5px] text-text-3 m-0 mb-2.5">
              Which personas debate each candidate. Leaving all selected = full council.
              @if (kind === 'sector_rotation') {
                <span>Sector rotation defaults to the macro trio (Druckenmiller, Damodaran, Burry) — name-centric value investors aren't a great fit for ETF baskets.</span>
              }
            </p>
            <div class="persona-grid">
              @for (p of personaMeta; track p.id) {
                <hf-persona-card
                  [persona]="p"
                  [selected]="selectedPersonas.includes(p.id)"
                  (toggled)="togglePersona(p.id)" />
              }
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="title">Screener weights</span></div>
          <div class="card-bd">
            <p class="text-[11.5px] text-text-3 m-0 mb-2.5">
              How much each feature contributes to the ranking. Higher = more weight. Hover the (!) icon on each row for what it does.
            </p>
            <div class="grid grid-cols-2 gap-y-2 gap-x-6">
              @for (key of weightKeys; track key) {
                <label class="flex items-center gap-2 text-2xs text-text-2">
                  <span class="flex-1 inline-flex items-center gap-1">
                    {{ label(key) }}
                    <hf-info [text]="tooltip(key)" />
                  </span>
                  <input class="input mono w-20 h-[26px] py-0 px-2 text-2xs" type="number" step="0.05" min="0" max="5"
                    [ngModel]="weights[key]"
                    (ngModelChange)="weights[key] = $event"
                    [name]="'w_' + key" />
                </label>
              }
            </div>
          </div>
        </section>

        @if (error()) {
          <p class="text-[var(--acc-short-fg)] text-2xs">{{ error() }}</p>
        }

        <div class="flex gap-2">
          <a class="btn ghost" routerLink="/strategies">Cancel</a>
          <button type="submit" class="btn primary flex-1 h-9 justify-center" [disabled]="submitting()">
            {{ submitting() ? 'Creating…' : 'Create strategy' }}
          </button>
        </div>
      </form>
    </hf-app-shell>
  `,
  styles: [
    `
      .persona-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
      }
      @media (max-width: 540px) {
        .persona-grid { grid-template-columns: 1fr; }
      }
      .strat-link-button {
        font-size: 11.5px;
        color: var(--acc-info-fg);
        background: transparent;
        border: 0;
        cursor: pointer;
        padding: 0;
        margin-top: 4px;
        text-align: left;
      }
    `,
  ],
})
export class StrategiesNewPage implements OnInit {
  readonly store = inject(StrategiesStore);
  private readonly router = inject(Router);

  /** Strategies cannot point at the per-user Manual Book (P3 isolation
   *  guarantee, enforced by `StrategySerializer.validate_portfolio`).
   *  Filter it out here so it never appears in the dropdown. */
  strategyPortfolios(): { id: number; name: string; cash_balance: string }[] {
    return this.store.portfolios().filter((p) => p.kind !== 'manual');
  }

  /** Auto-suggested from kind + universe + nameStamp until the user edits it. */
  name = '';
  /** Last value produced by buildAutoName(); while `name` still equals it the
   *  field is considered untouched and keeps re-suggesting on kind/universe changes. */
  private autoName = '';
  /** Flips true once the user types their own name — auto-naming then stops. */
  private nameIsCustom = false;
  /** Timestamp suffix, fixed when the page opens so it stays stable while editing. */
  private readonly nameStamp = this.buildStamp();
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
  // P02f review: market-neutral controls exposed in the UI.
  benchmarkTicker = 'SPY';
  betaWindowDays = 252;
  neutralityToleranceDollar = 0.02;
  neutralityToleranceBeta = 0.05;
  dropOnUnreliableBeta = false;
  // P02i review: global-macro asset-class caps + inverse policy.
  preferInverseEtfOverShort = true;
  maxInverseEtfHoldDays = 14;
  assetClassCapEquity = 0.50;
  assetClassCapRates = 0.40;
  assetClassCapCommodity = 0.30;
  /** P2l: false → cycle pauses at awaiting_review, true → council fans out
   *  immediately after screening (default — preserves pre-P2l behavior). */
  autoRunCouncil = true;

  // Persona picker. Recommended defaults per kind are applied in onKindChange
  // and on first render via the field initializer below. personaMeta drives the
  // rich persona cards; ALL_PERSONAS is the id list used by the per-kind
  // recommendation logic and the submit payload.
  readonly personaMeta = PERSONA_META;
  readonly ALL_PERSONAS = PERSONA_META.map((p) => p.id);
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
  kindGuideSlug(): string { return STRATEGY_KIND_GUIDE[this.kind]; }

  /** Compact, stable timestamp suffix, e.g. "2026-05-21 14:32". */
  private buildStamp(): string {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /** Suggested strategy name: "<kind> · <universe> · <timestamp>". */
  private buildAutoName(): string {
    const kindLabel = this.kindOptions.find((o) => o.value === this.kind)?.label ?? this.kind;
    const uni = this.universe === null
      ? null
      : this.store.universes().find((u) => u.id === this.universe);
    const parts = [kindLabel];
    if (uni) parts.push(uni.name);
    parts.push(this.nameStamp);
    return parts.join(' · ');
  }

  /** Re-suggest the name from kind + universe, unless the user has overridden it. */
  private refreshAutoName(): void {
    if (this.nameIsCustom) return;
    this.autoName = this.buildAutoName();
    this.name = this.autoName;
  }

  /** The Name field becomes the user's own once it differs from the suggestion;
   *  clearing it hands control back to the auto-suggester. */
  onNameChange(value: string): void {
    this.nameIsCustom = value.trim().length > 0 && value !== this.autoName;
  }

  /** Universe changed in the dropdown — refresh the suggested name. */
  onUniverseChange(): void {
    this.refreshAutoName();
  }

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
      // P02k review: switch to an equity universe when the user picks
      // pairs unless the currently-selected universe is already equity-
      // like. ETF-only universes (risk_parity_sleeves, sector_etfs,
      // macro_etfs) don't have enough cointegrated pairs to be useful.
      const ETF_ONLY_UNIVERSES = new Set([
        'risk_parity_sleeves', 'sector_etfs', 'macro_etfs',
      ]);
      const currentUni = this.universe === null ? null
        : this.store.universes().find((u) => u.id === this.universe);
      if (!currentUni || ETF_ONLY_UNIVERSES.has(currentUni.name)) {
        const equityUni = this.store.universes().find(
          (u) => u.name === 'sp500_top_200',
        ) || this.store.universes().find(
          (u) => u.name === 'sp500',
        ) || this.store.universes().find(
          (u) => !ETF_ONLY_UNIVERSES.has(u.name),
        );
        if (equityUni) this.universe = equityUni.id;
      }
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
    this.refreshAutoName();
  }

  label(k: string): string { return SCREENER_WEIGHT_LABELS[k] ?? k; }
  tooltip(k: string): string { return SCREENER_WEIGHT_TOOLTIPS[k] ?? ''; }

  ngOnInit(): void {
    this.refreshAutoName();
    this.store.loadUniverses().subscribe((us) => {
      if (us.length && this.universe === null) this.universe = us[0].id;
      this.refreshAutoName();
    });
    this.store.loadPortfolios().subscribe((ps) => {
      const strategyPs = ps.filter((p) => p.kind !== 'manual');
      if (strategyPs.length && this.portfolio === null) {
        this.portfolio = strategyPs[0].id;
      }
    });
  }

  createPortfolio(): void {
    this.store.createPortfolio({
      name: 'Default paper portfolio',
      cash_balance: 100000,
    }).subscribe(() => this.store.loadPortfolios().subscribe((ps) => {
      const strategyPs = ps.filter((p) => p.kind !== 'manual');
      if (strategyPs.length) this.portfolio = strategyPs[strategyPs.length - 1].id;
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
      auto_run_council: this.autoRunCouncil,
      screener_weights: this.weights,
      // Empty list = no persona filter on the backend (full default council);
      // otherwise pass the explicit picks. Either way, the user owns the choice.
      personas: this.selectedPersonas.length === this.ALL_PERSONAS.length
        ? []
        : [...this.selectedPersonas],
    };
    if (this.kind === 'market_neutral') {
      // P02f review: surface market-neutral parameters in the payload.
      payload['benchmark_ticker'] = this.benchmarkTicker.toUpperCase();
      payload['beta_window_days'] = this.betaWindowDays;
      payload['neutrality_tolerance_dollar_pct'] = String(this.neutralityToleranceDollar);
      payload['neutrality_tolerance_beta'] = String(this.neutralityToleranceBeta);
      payload['drop_on_unreliable_beta'] = this.dropOnUnreliableBeta;
    }
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
      // P02i review: surface inverse-policy + asset-class caps to backend.
      payload['prefer_inverse_etf_over_short'] = this.preferInverseEtfOverShort;
      payload['max_inverse_etf_hold_days'] = this.maxInverseEtfHoldDays;
      payload['asset_class_caps'] = {
        equity: this.assetClassCapEquity,
        rates: this.assetClassCapRates,
        commodity: this.assetClassCapCommodity,
      };
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
