import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ModelsStore } from '../../abstraction/models.store';
import { RunsStore } from '../../abstraction/runs.store';
import { ALL_PERSONAS, DEFAULT_PERSONA_IDS } from '../../core/models/run.model';
import { ModelPanelComponent } from '../shared/model-panel.component';

@Component({
  selector: 'hf-runs-new',
  standalone: true,
  imports: [FormsModule, RouterLink, ModelPanelComponent],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-semibold">New analysis</h1>
        <a routerLink="/" class="text-blue-600 hover:underline">Dashboard</a>
      </header>

      <form
        (ngSubmit)="submit()"
        class="bg-white p-6 rounded shadow w-full max-w-xl space-y-4"
      >
        <label class="block text-sm font-medium">
          Ticker
          <input
            name="ticker"
            [(ngModel)]="ticker"
            placeholder="AAPL"
            required
            class="mt-1 w-full border rounded px-3 py-2 uppercase"
          />
        </label>

        <label class="block text-sm font-medium">
          As-of date
          <input
            name="asOf"
            type="date"
            [(ngModel)]="asOfDate"
            required
            class="mt-1 w-full border rounded px-3 py-2"
          />
        </label>

        <hf-model-panel
          [agents]="panelAgents()"
          [(overrides)]="overrides"
        />

        <fieldset class="border rounded p-3">
          <legend class="text-sm font-medium px-1">Council personas</legend>
          <div class="grid grid-cols-2 gap-2 mt-2">
            @for (p of allPersonas; track p.id) {
              <label class="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  [checked]="selected().has(p.id)"
                  (change)="toggle(p.id)"
                />
                {{ p.name }}
                @if (p.optional) {
                  <span class="text-xs text-gray-400">(optional)</span>
                }
              </label>
            }
          </div>
          <p class="text-xs text-gray-500 mt-2">
            {{ selected().size }} of {{ allPersonas.length }} selected.
          </p>
        </fieldset>

        @if (error()) {
          <p class="text-red-600 text-sm">{{ error() }}</p>
        }

        <button
          type="submit"
          [disabled]="submitting() || selected().size === 0"
          class="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50"
        >
          {{ submitting() ? 'Submitting…' : 'Run council' }}
        </button>
      </form>
    </div>
  `,
})
export class RunsNewPage implements OnInit {
  readonly runs = inject(RunsStore);
  readonly modelsStore = inject(ModelsStore);
  private readonly router = inject(Router);

  readonly allPersonas = ALL_PERSONAS;
  ticker = 'AAPL';
  asOfDate = new Date().toISOString().slice(0, 10);
  submitting = signal(false);
  error = signal<string | null>(null);
  selected = signal<Set<string>>(new Set(DEFAULT_PERSONA_IDS));
  overrides = signal<Record<string, string>>({});

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
