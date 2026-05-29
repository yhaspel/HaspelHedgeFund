import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { take } from 'rxjs/operators';
import { AppShellComponent } from '../shared/app-shell.component';
import { ModelsStore } from '../../abstraction/models.store';
import { RunsStore } from '../../abstraction/runs.store';
import { ALL_PERSONAS, DEFAULT_PERSONA_IDS } from '../../core/models/run.model';
import { ModelPanelComponent } from '../shared/model-panel.component';
import { PersonaCardComponent } from '../shared/persona-card.component';
import { SparklineComponent } from '../shared/sparkline.component';
import { GlossaryTermComponent } from '../shared/glossary-term.component';
import { TickerHistoryStore } from '../../abstraction/ticker-history.store';

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
                  placeholder="AAPL" required autocomplete="off" />
                <hf-sparkline [points]="tickerSpark()" [width]="80" [height]="22" [loading]="sparkLoading()" />
              </div>
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

        @if (error()) {
          <p role="alert" class="text-[var(--acc-short-fg)] text-2xs">{{ error() }}</p>
        }

        <div class="flex gap-2">
          <a class="btn ghost" routerLink="/runs">Cancel</a>
          <button type="submit" class="btn primary flex-1 h-9 justify-center"
            [disabled]="submitting() || selected().size === 0">
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
  private readonly router = inject(Router);
  private readonly history = inject(TickerHistoryStore);
  private readonly route = inject(ActivatedRoute);

  readonly allPersonas = ALL_PERSONAS;
  ticker = 'AAPL';
  asOfDate = new Date().toISOString().slice(0, 10);
  submitting = signal(false);
  error = signal<string | null>(null);
  selected = signal<Set<string>>(new Set(DEFAULT_PERSONA_IDS));
  overrides = signal<Record<string, string>>({});
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

  ngOnInit(): void {
    this.modelsStore.loadAll().subscribe();
    this.route.queryParamMap.pipe(take(1)).subscribe((params) => {
      const t = (params.get('ticker') || '').trim().toUpperCase();
      if (t) {
        this.ticker = t;
      }
      this.loadSpark(this.ticker);
    });
  }

  onTickerChange(v: string): void {
    if (this.sparkDebounce) clearTimeout(this.sparkDebounce);
    this.tickerSpark.set(null);
    const t = (v ?? '').trim();
    if (t.length < 1) return;
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

  submit(): void {
    this.error.set(null);
    this.submitting.set(true);
    this.runs
      .submitRun({
        tickers: [this.ticker.toUpperCase()],
        as_of_date: this.asOfDate,
        model_overrides: this.overrides(),
        personas: Array.from(this.selected()),
      })
      .subscribe({
        next: (run) => this.router.navigate(['/runs', run.id]),
        error: (e) => {
          this.submitting.set(false);
          this.error.set(e?.error?.detail ?? 'Failed to submit run');
        },
      });
  }
}
