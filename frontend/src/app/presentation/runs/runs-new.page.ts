import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { ModelsStore } from '../../abstraction/models.store';
import { RunsStore } from '../../abstraction/runs.store';
import { ALL_PERSONAS, DEFAULT_PERSONA_IDS } from '../../core/models/run.model';
import { ModelPanelComponent } from '../shared/model-panel.component';
import { PersonaCardComponent } from '../shared/persona-card.component';

@Component({
  selector: 'hf-runs-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, ModelPanelComponent, AppShellComponent, PersonaCardComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Runs', link:'/'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Run setup</div>
          <h1 style="margin-top:6px">New analysis</h1>
        </div>
      </div>

      <form (ngSubmit)="submit()" style="max-width:640px;display:flex;flex-direction:column;gap:18px">
        <section class="card">
          <div class="card-bd" style="display:flex;flex-direction:column;gap:14px">
            <div class="field">
              <label class="lbl">Ticker</label>
              <input class="input" name="ticker" [(ngModel)]="ticker" placeholder="AAPL" required
                style="text-transform:uppercase" />
            </div>
            <div class="field">
              <label class="lbl">As-of date</label>
              <input class="input" type="date" name="asOf" [(ngModel)]="asOfDate" required />
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
            <span class="title">Council personas</span>
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
          <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
        }

        <div style="display:flex;gap:8px">
          <a class="btn ghost" routerLink="/">Cancel</a>
          <button type="submit" class="btn primary"
            [disabled]="submitting() || selected().size === 0"
            style="flex:1;height:36px;justify-content:center">
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
