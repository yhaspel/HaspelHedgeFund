import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { take } from 'rxjs/operators';
import { AppShellComponent } from '../shared/app-shell.component';
import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { RunsStore } from '../../abstraction/runs.store';
import { ALL_PERSONAS, DEFAULT_PERSONA_IDS } from '../../core/models/run.model';
import { ModelPanelComponent } from '../shared/model-panel.component';
import { PersonaCardComponent } from '../shared/persona-card.component';
import { SparklineComponent } from '../shared/sparkline.component';
import { GlossaryTermComponent } from '../shared/glossary-term.component';
import { TickerHistoryStore } from '../../abstraction/ticker-history.store';
import { apiErrorMessage } from '../../core/api/api-error';
import { normalizeTicker, tickerError } from '../shared/format';

/** Today's date in the USER's timezone as YYYY-MM-DD (toISOString is UTC). */
function localDateString(d: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

@Component({
  selector: 'hf-runs-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, ModelPanelComponent, AppShellComponent, PersonaCardComponent, SparklineComponent, GlossaryTermComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Runs', link:'/runs'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Run setup</div>
          <h1 class="mt-1.5">New analysis</h1>
        </div>
      </div>

      <form (ngSubmit)="submit()" class="max-w-[640px] flex flex-col gap-[18px]">
        <section class="card">
          <div class="card-bd flex flex-col gap-3.5">
            <div class="field">
              <label class="lbl" for="run-ticker"><hf-term key="ticker">Ticker</hf-term></label>
              <div class="flex items-center gap-2.5">
                <input id="run-ticker" class="input uppercase flex-1" name="ticker" [(ngModel)]="ticker"
                  (ngModelChange)="onTickerChange($event)"
                  (blur)="touched.set(true)"
                  [attr.aria-invalid]="touched() && tickerError() ? 'true' : null"
                  [attr.aria-describedby]="touched() && tickerError() ? 'run-ticker-err' : null"
                  placeholder="AAPL" required autocomplete="off" />
                <hf-sparkline [points]="tickerSpark()" [width]="80" [height]="22" [loading]="sparkLoading()" />
              </div>
              @if (touched() && tickerError()) {
                <p id="run-ticker-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   data-test="ticker-error">{{ tickerError() }}</p>
              }
              @if (tickerSpark() && tickerSpark()!.length >= 2) {
                <div class="mono text-[11px] text-text-3 mt-1">
                  {{ ticker.toUpperCase() }} · last {{ tickerSpark()!.length }}d ·
                  <span [style.color]="trendUp() ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                    {{ trendUp() ? '▲' : '▼' }} {{ trendPct() }}%
                  </span>
                </div>
              }
            </div>
            <div class="field">
              <label class="lbl" for="run-asof">As-of date</label>
              <input id="run-asof" class="input" type="date" name="asOf" [(ngModel)]="asOfDate" required />
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd flex flex-col gap-3.5">
            <div class="field">
              <label class="lbl" for="run-graph">Agent graph</label>
              <select id="run-graph" class="input" name="graph"
                [ngModel]="graphVersionId()" (ngModelChange)="onGraphChange($event)"
                data-testid="run-graph-select">
                <option [ngValue]="null">Council classic (default)</option>
                @for (g of graphOptions(); track g.versionId) {
                  <option [ngValue]="g.versionId">{{ g.label }}</option>
                }
              </select>
              @if (graphVersionId()) {
                <p class="mono text-[11px] text-text-3 mt-1">
                  Models &amp; personas come from this saved graph.
                  <a routerLink="/graphs">Manage graphs</a>
                </p>
              }
            </div>
          </div>
        </section>

        @if (!graphVersionId()) {
          <section class="card">
            <div class="card-bd">
              <hf-model-panel [agents]="panelAgents()" [(overrides)]="overrides" />
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <h2 class="title"><hf-term key="council">Council</hf-term> personas</h2>
              <span class="pill"><span class="dot"></span>{{ selected().size }} of {{ allPersonas.length }}</span>
            </div>
            <div class="card-bd persona-grid">
              @for (p of allPersonas; track p.id) {
                <hf-persona-card
                  [persona]="p"
                  [selected]="selected().has(p.id)"
                  (toggled)="toggle(p.id)" />
              }
            </div>
          </section>
        }

        @if (error()) {
          <p role="alert" class="text-[var(--acc-short-fg)] text-2xs">{{ error() }}</p>
        }

        <div class="flex gap-2">
          <a class="btn ghost" routerLink="/runs">Cancel</a>
          <button type="submit" class="btn primary flex-1 h-9 justify-center"
            [disabled]="submitting() || !!tickerError() || !asOfDate
                        || (!graphVersionId() && selected().size === 0)">
            {{ submitting() ? 'Submitting…' : 'Run council' }}
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
    `,
  ],
})
export class RunsNewPage implements OnInit {
  readonly runs = inject(RunsStore);
  readonly modelsStore = inject(ModelsStore);
  readonly graphs = inject(GraphsStore);
  private readonly router = inject(Router);
  private readonly history = inject(TickerHistoryStore);
  private readonly route = inject(ActivatedRoute);

  readonly allPersonas = ALL_PERSONAS;
  ticker = 'AAPL';
  /**
   * The LOCAL calendar date, not the UTC one. `toISOString()` is UTC, so a user
   * in New York at 21:30 on Sep 7 was pre-filled with Sep 8 — a future as_of the
   * backend rejects.
   */
  asOfDate = localDateString();
  submitting = signal(false);
  error = signal<string | null>(null);
  /** Mirror of `ticker` as a signal so the inline field error is reactive. */
  readonly tickerValue = signal('AAPL');
  /** The field error only shows after a blur or a submit attempt. */
  readonly touched = signal(false);
  selected = signal<Set<string>>(new Set(DEFAULT_PERSONA_IDS));
  overrides = signal<Record<string, string>>({});
  graphVersionId = signal<number | null>(null);

  // Own graphs + templates that have a valid latest version.
  graphOptions = computed(() =>
    this.graphs
      .graphs()
      .filter((g) => g.latest_version && g.latest_version.validation_status === 'valid')
      .map((g) => ({
        versionId: g.latest_version!.id,
        label: g.name + (g.is_template ? ' (template)' : ''),
      })),
  );
  tickerSpark = signal<number[] | null>(null);
  sparkLoading = signal(false);
  private sparkDebounce?: ReturnType<typeof setTimeout>;

  panelAgents = () => [
    ...Array.from(this.selected()),
    'fundamentals', 'technicals', 'valuation', 'sentiment',
    'macro', 'news_digest',
    'risk_manager', 'portfolio_manager', 'cio',
  ];

  toggle(id: string): void {
    const next = new Set(this.selected());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.selected.set(next);
  }

  onGraphChange(v: number | null): void {
    this.graphVersionId.set(v ?? null);
  }

  ngOnInit(): void {
    this.modelsStore.loadAll().subscribe();
    this.graphs.loadGraphs().subscribe();
    this.route.queryParamMap.pipe(take(1)).subscribe((params) => {
      const t = normalizeTicker(params.get('ticker'));
      if (t) {
        this.ticker = t;
      }
      this.tickerValue.set(this.ticker);
      if (!tickerError(this.ticker)) this.loadSpark(this.ticker);
    });
  }

  onTickerChange(v: string): void {
    this.tickerValue.set(v ?? '');
    if (this.sparkDebounce) clearTimeout(this.sparkDebounce);
    this.tickerSpark.set(null);
    const t = (v ?? '').trim();
    if (t.length < 1) return;
    // Don't burn a history fetch on a symbol the backend would reject anyway.
    if (tickerError(t)) return;
    this.sparkDebounce = setTimeout(() => this.loadSpark(t), 350);
  }

  private loadSpark(t: string): void {
    const key = t.toUpperCase();
    if (!key) return;
    this.sparkLoading.set(true);
    this.history.fetch(key, 60).subscribe({
      next: (closes) => {
        this.tickerSpark.set(closes.length ? closes : null);
        this.sparkLoading.set(false);
      },
      error: () => {
        this.tickerSpark.set(null);
        this.sparkLoading.set(false);
      },
    });
  }

  trendUp(): boolean {
    const p = this.tickerSpark();
    return !!(p && p.length >= 2 && p[p.length - 1] >= p[0]);
  }
  trendPct(): string {
    const p = this.tickerSpark();
    if (!p || p.length < 2 || p[0] === 0) return '0.00';
    return (((p[p.length - 1] - p[0]) / p[0]) * 100).toFixed(2);
  }

  /** null when the ticker is acceptable to the backend, else the reason. */
  readonly tickerError = computed(() => tickerError(this.tickerValue()));

  submit(): void {
    this.error.set(null);
    // Validate before spending a round trip; the field error is rendered inline.
    this.touched.set(true);
    const bad = this.tickerError();
    if (bad) {
      this.error.set(bad);
      return;
    }
    if (!this.asOfDate) {
      this.error.set('Pick an as-of date.');
      return;
    }
    this.submitting.set(true);
    const gv = this.graphVersionId();
    this.runs
      .submitRun({
        tickers: [normalizeTicker(this.ticker)],
        as_of_date: this.asOfDate,
        // When a graph is chosen, the backend flattens its models + personas;
        // omit the ad-hoc model/persona pickers.
        model_overrides: gv ? {} : this.overrides(),
        personas: gv ? [] : Array.from(this.selected()),
        graph_version_id: gv,
      })
      .subscribe({
        next: (run) => this.router.navigate(['/runs', run.id]),
        error: (e: unknown) => {
          this.submitting.set(false);
          // DRF field errors ({"tickers": [...]}) have no `detail` key; the old
          // `e?.error?.detail ?? 'Failed to submit run'` threw away the one
          // sentence that says what to change.
          this.error.set(apiErrorMessage(e, 'Failed to submit run'));
        },
      });
  }
}
