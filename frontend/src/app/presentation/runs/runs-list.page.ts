import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';

import { AppShellComponent } from '../shared/app-shell.component';
import { RunsStore } from '../../abstraction/runs.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { TickerComponent } from '../shared/ticker.component';
import { RunStatus, RunSummary } from '../../core/models/run.model';

type SourceFilter = 'all' | 'adhoc' | 'strategy';
type StatusFilter = 'all' | RunStatus;

@Component({
  selector: 'hf-runs-list',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent, TickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'Runs'}]">
      <div class="page-head" style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:14px">
        <div>
          <div class="eyebrow">Run archive</div>
          <h1 style="margin:6px 0 0">Runs</h1>
          <p class="muted" style="margin:4px 0 0;font-size:13px;color:var(--text-3)">
            Every analysis you have run — ad-hoc and strategy-cycle-sourced.
          </p>
        </div>
        <a class="btn primary" routerLink="/runs/new" style="height:32px;padding:0 14px;display:inline-flex;align-items:center;gap:6px">
          <svg width="14" height="14" aria-hidden="true"><use href="/icons.svg#i-play" /></svg>
          New run
        </a>
      </div>

      <section class="card">
        <div class="card-hd" style="gap:12px;flex-wrap:wrap">
          <span class="title">All runs ({{ filtered().length }})</span>
          <div role="group" aria-label="Filter by source" class="seg" style="margin-left:auto">
            @for (opt of sourceOptions; track opt.id) {
              <button type="button" class="seg-btn"
                [class.active]="sourceFilter() === opt.id"
                [attr.aria-pressed]="sourceFilter() === opt.id"
                (click)="setSourceFilter(opt.id)">{{ opt.label }}</button>
            }
          </div>
          <div role="group" aria-label="Filter by status" class="seg">
            @for (opt of statusOptions; track opt.id) {
              <button type="button" class="seg-btn"
                [class.active]="statusFilter() === opt.id"
                [attr.aria-pressed]="statusFilter() === opt.id"
                (click)="setStatusFilter(opt.id)">{{ opt.label }}</button>
            }
          </div>
        </div>

        @if (loading()) {
          <div class="card-bd"><p class="muted">Loading runs…</p></div>
        } @else if (filtered().length === 0) {
          <div class="empty-state">
            @if (allRuns().length === 0) {
              <p>You haven't started any runs yet.</p>
              <a class="btn primary" routerLink="/runs/new">Start your first run</a>
            } @else {
              <p>No runs match the current filters.</p>
              <button type="button" class="btn ghost" (click)="resetFilters()">Reset filters</button>
            }
          </div>
        } @else {
          <table class="tbl runs-tbl">
            <thead>
              <tr>
                <th scope="col" style="width:78px">Run</th>
                <th scope="col">Tickers</th>
                <th scope="col" style="width:120px">Source</th>
                <th scope="col" style="width:108px">Status</th>
                <th scope="col" class="right" style="width:96px">Personas</th>
                <th scope="col" class="right" style="width:108px">Cost</th>
                <th scope="col" style="width:128px">Date</th>
              </tr>
            </thead>
            <tbody>
              @for (r of filtered(); track r.id) {
                <tr class="row-clickable" (click)="open(r)">
                  <td class="mono">#{{ r.id }}</td>
                  <td>
                    <span style="display:inline-flex;gap:8px;flex-wrap:wrap">
                      @for (t of r.tickers; track t) {
                        <hf-ticker [ticker]="t"></hf-ticker>
                      }
                    </span>
                  </td>
                  <td>
                    @if (r.source === 'strategy' && r.strategy_backlink) {
                      <span class="pill" style="background:var(--surface-2);color:var(--text-3);height:auto;padding:2px 8px;font-size:11px">
                        via {{ r.strategy_backlink.strategy_name }}
                      </span>
                    } @else {
                      <span class="pill" style="background:var(--surface-2);color:var(--text-3);height:auto;padding:2px 8px;font-size:11px">manual</span>
                    }
                  </td>
                  <td>
                    <span class="pill"
                      [class.ok]="r.status==='done'"
                      [class.warn]="r.status==='running' || r.status==='queued'"
                      [class.err]="r.status==='failed' || r.status==='cancelled'">
                      <span class="dot"></span>{{ r.status }}
                    </span>
                  </td>
                  <td class="num">{{ personaCount(r) }}</td>
                  <td class="num">$ {{ (+r.total_cost_usd).toFixed(4) }}</td>
                  <td class="mono" style="color:var(--text-3);font-size:11.5px">
                    {{ r.as_of_date }}
                  </td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </hf-app-shell>
  `,
  styles: [
    `
      .runs-tbl tr.row-clickable { cursor: pointer; }
      .runs-tbl tr.row-clickable:hover { background: var(--hover); }
      .empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 12px;
        padding: 32px 12px;
        text-align: center;
        color: var(--text-3);
      }
      .empty-state p { margin: 0; font-size: 13px; }
      .seg {
        display: inline-flex;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        padding: 2px;
        gap: 2px;
      }
      .seg-btn {
        height: 26px;
        min-width: 56px;
        padding: 0 10px;
        border-radius: var(--r-4);
        font-size: 12px;
        color: var(--text-2);
        background: transparent;
        border: 0;
        cursor: pointer;
      }
      .seg-btn:hover { color: var(--text); }
      .seg-btn.active {
        background: var(--surface);
        color: var(--text);
        box-shadow: var(--shadow-1);
      }
    `,
  ],
})
export class RunsListPage implements OnInit {
  readonly runs = inject(RunsStore);
  private readonly router = inject(Router);
  private readonly profileStore = inject(TickerProfileStore);

  readonly loading = signal(true);
  readonly sourceFilter = signal<SourceFilter>('all');
  readonly statusFilter = signal<StatusFilter>('all');

  readonly sourceOptions: { id: SourceFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'adhoc', label: 'Manual' },
    { id: 'strategy', label: 'Strategy' },
  ];
  readonly statusOptions: { id: StatusFilter; label: string }[] = [
    { id: 'all', label: 'Any' },
    { id: 'done', label: 'Done' },
    { id: 'running', label: 'Running' },
    { id: 'failed', label: 'Failed' },
  ];

  readonly allRuns = computed<RunSummary[]>(() => {
    const items = this.runs.runs();
    return [...items].sort((a, b) => (b.id ?? 0) - (a.id ?? 0));
  });

  readonly filtered = computed<RunSummary[]>(() => {
    const src = this.sourceFilter();
    const status = this.statusFilter();
    return this.allRuns().filter((r) => {
      if (src !== 'all' && (r.source ?? 'adhoc') !== src) return false;
      if (status !== 'all' && r.status !== status) return false;
      return true;
    });
  });

  ngOnInit(): void {
    this.runs.listRuns().subscribe({
      next: (rows) => {
        this.loading.set(false);
        // Lazy-prefetch names for visible tickers (cheap; deduped batch).
        const tickers = [...new Set(rows.flatMap((r) => r.tickers || []))];
        if (tickers.length) this.profileStore.fetchNames(tickers).subscribe();
      },
      error: () => this.loading.set(false),
    });
  }

  personaCount(r: RunSummary): number {
    return r.personas?.length ?? 0;
  }

  setSourceFilter(s: SourceFilter): void { this.sourceFilter.set(s); }
  setStatusFilter(s: StatusFilter): void { this.statusFilter.set(s); }
  resetFilters(): void { this.sourceFilter.set('all'); this.statusFilter.set('all'); }

  open(r: RunSummary): void { this.router.navigate(['/runs', r.id]); }
}
