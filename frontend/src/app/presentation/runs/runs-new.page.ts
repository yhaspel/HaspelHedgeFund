import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { RunsStore } from '../../abstraction/runs.store';

@Component({
  selector: 'hf-runs-new',
  standalone: true,
  imports: [FormsModule, RouterLink],
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

        <label class="block text-sm font-medium">
          Buffett model
          <select
            name="model"
            [(ngModel)]="buffettModel"
            class="mt-1 w-full border rounded px-3 py-2"
          >
            <option value="">Default (Qwen3.6 27B)</option>
            @for (m of runs.models(); track m.id) {
              <option [value]="m.id">{{ m.name }} ({{ m.tier }})</option>
            }
          </select>
        </label>

        @if (error()) {
          <p class="text-red-600 text-sm">{{ error() }}</p>
        }

        <button
          type="submit"
          [disabled]="submitting()"
          class="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50"
        >
          {{ submitting() ? 'Submitting…' : 'Run analysis' }}
        </button>
      </form>
    </div>
  `,
})
export class RunsNewPage implements OnInit {
  readonly runs = inject(RunsStore);
  private readonly router = inject(Router);

  ticker = 'AAPL';
  asOfDate = '2024-12-31';
  buffettModel = '';
  submitting = signal(false);
  error = signal<string | null>(null);

  ngOnInit(): void {
    this.runs.loadModels().subscribe();
  }

  submit(): void {
    this.error.set(null);
    this.submitting.set(true);
    const overrides: Record<string, string> = {};
    if (this.buffettModel) overrides['buffett'] = this.buffettModel;
    this.runs
      .submitRun({
        tickers: [this.ticker.toUpperCase()],
        as_of_date: this.asOfDate,
        model_overrides: overrides,
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
