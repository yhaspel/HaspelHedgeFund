import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';

import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { RunsStore } from '../../abstraction/runs.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { TickerComponent } from '../shared/ticker.component';
import { DecisionAction, RunStatus, RunSummary } from '../../core/models/run.model';

type SourceFilter = 'all' | 'adhoc' | 'strategy';
type StatusFilter = 'all' | RunStatus;

@Component({
  selector: 'hf-runs-list',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent, EmptyStateComponent, TickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'Runs'}]">
      <div class="page-head flex items-end justify-between gap-4 mb-3.5">
        <div>
          <div class="eyebrow">Run archive</div>
          <h1 class="m-0 mt-1.5">Runs</h1>
          <p class="muted m-0 mt-1 text-xs text-text-3">
            Every analysis you have run — ad-hoc and strategy-cycle-sourced.
          </p>
        </div>
        <a class="btn primary h-8 px-3.5 inline-flex items-center gap-1.5" routerLink="/runs/new">
          <svg width="14" height="14" aria-hidden="true"><use href="/icons.svg#i-play" /></svg>
          New run
        </a>
      </div>

      <section class="card">
        <div class="card-hd gap-3 flex-wrap">
          <h2 class="title">
            Runs ({{ filtered().length }}<span class="text-text-3">{{
              runs.runsCount() > filtered().length ? ' of ' + runs.runsCount() : ''
            }}</span>)
          </h2>
          <!-- P10 §D4: server defaults to the last 30 days; widen on demand. -->
          <div role="group" aria-label="Time window" class="seg">
            @for (opt of windowOptions; track opt.id) {
              <button type="button" class="seg-btn"
                [class.active]="windowFilter() === opt.id"
                [attr.aria-pressed]="windowFilter() === opt.id"
                (click)="setWindow(opt.id)">{{ opt.label }}</button>
            }
          </div>
          <div role="group" aria-label="Filter by source" class="seg ml-auto">
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
          <div class="run-search">
            <input #searchInput type="search" class="input sans" placeholder="Search transcripts…"
              aria-label="Search run transcripts"
              [value]="searchTerm()"
              (keyup.enter)="doSearch(searchInput.value)" />
            <button type="button" class="btn sm" (click)="doSearch(searchInput.value)">Search</button>
            @if (searchTerm()) {
              <button type="button" class="btn sm ghost" (click)="clearSearch(searchInput)">Clear</button>
            }
          </div>
        </div>

        @if (loading()) {
          <div class="card-bd" aria-busy="true" aria-label="Loading runs">
            <div class="flex flex-col gap-2.5">
              @for (_ of [1,2,3,4,5]; track $index) {
                <div class="skel h-6 w-full"></div>
              }
            </div>
          </div>
        } @else if (filtered().length === 0) {
          @if (allRuns().length === 0) {
            <hf-empty-state message="You haven't started any runs yet.">
              <a class="btn primary" routerLink="/runs/new">Start your first run</a>
            </hf-empty-state>
          } @else {
            <hf-empty-state message="No runs match the current filters.">
              <button type="button" class="btn ghost" (click)="resetFilters()">Reset filters</button>
            </hf-empty-state>
          }
        } @else {
          <div class="tbl-scroll">
          <table class="tbl runs-tbl">
            <thead>
              <tr>
                <th scope="col" class="w-[78px]">Run</th>
                <th scope="col">Tickers</th>
                <th scope="col" class="w-[120px]">Source</th>
                <th scope="col" class="w-[108px]">Status</th>
                <th scope="col" class="w-[140px]">Decision</th>
                <th scope="col" class="right w-24">Personas</th>
                <th scope="col" class="right w-[108px]">Cost</th>
                <th scope="col" class="w-32">Date</th>
                <th scope="col" class="w-[92px]"><span class="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              @for (r of filtered(); track r.id) {
                <tr class="row-clickable" (click)="open(r)">
                  <td class="mono">#{{ r.id }}</td>
                  <td>
                    <span class="inline-flex gap-2 flex-wrap">
                      @for (t of r.tickers; track t) {
                        <hf-ticker [ticker]="t"></hf-ticker>
                      }
                    </span>
                  </td>
                  <td>
                    @if (r.source === 'strategy' && r.strategy_backlink) {
                      <span class="pill pill-source">
                        via {{ r.strategy_backlink.strategy_name }}
                      </span>
                    } @else {
                      <span class="pill pill-source">manual</span>
                    }
                    @if (r.rerun_of) {
                      <span class="pill pill-source" title="This run was created by rerunning #{{ r.rerun_of }}">
                        ↻ rerun of #{{ r.rerun_of }}
                      </span>
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
                  <td>
                    @if (r.decisions?.length) {
                      <span class="inline-flex gap-1 flex-wrap">
                        @for (d of r.decisions; track d.id) {
                          <span class="pill decision-pill" [class]="decisionToneClass(d.action)">
                            {{ d.action }}
                          </span>
                        }
                      </span>
                    } @else {
                      <span class="text-text-3">—</span>
                    }
                  </td>
                  <td class="num">{{ personaCount(r) }}</td>
                  <td class="num">$ {{ (+r.total_cost_usd).toFixed(4) }}</td>
                  <td class="mono text-text-3 text-[11.5px]">
                    {{ r.as_of_date }}
                  </td>
                  <td class="actions-cell">
                    @if (r.status === 'failed' || r.status === 'cancelled') {
                      <button type="button" class="btn ghost sm"
                              (click)="rerun(r, $event)"
                              [disabled]="rerunningId() === r.id"
                              [attr.data-test]="'rerun-' + r.id"
                              [attr.aria-label]="'Rerun run #' + r.id">
                        ↻ Rerun
                      </button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
          </div>
          <!-- P10 §D4: pager (50/page server-side). -->
          @if (runs.runsCount() > pageSize) {
            <div class="pager">
              <button type="button" class="btn ghost sm" [disabled]="page() <= 1"
                      (click)="setPage(page() - 1)">← Prev</button>
              <span class="mono text-2xs text-text-3">
                page {{ page() }} / {{ totalPages() }}
              </span>
              <button type="button" class="btn ghost sm" [disabled]="page() >= totalPages()"
                      (click)="setPage(page() + 1)">Next →</button>
            </div>
          }
        }
      </section>
    </hf-app-shell>
  `,
  styles: [
    `
      .runs-tbl tr.row-clickable { cursor: pointer; }
      .runs-tbl tr.row-clickable:hover { background: var(--hover); }
      .pill-source {
        background: var(--surface-2);
        color: var(--text-3);
        height: auto;
        padding: 2px 8px;
        font-size: 11px;
      }
      .actions-cell { text-align: right; white-space: nowrap; }
      .decision-pill {
        text-transform: uppercase;
        font-weight: 600;
        letter-spacing: 0.02em;
      }
      .decision-pill.tone-long {
        background: color-mix(in oklab, var(--acc-long) 18%, transparent);
        color: var(--acc-long-fg);
      }
      .decision-pill.tone-short {
        background: color-mix(in oklab, var(--acc-short) 18%, transparent);
        color: var(--acc-short-fg);
      }
      .decision-pill.tone-hold {
        background: color-mix(in oklab, var(--acc-hold) 18%, transparent);
        color: var(--acc-hold-fg);
      }
      .decision-pill.tone-skip {
        background: var(--surface-2);
        color: var(--text-3);
      }
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
      .pager {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        padding: 10px 0 4px;
      }
    `,
  ],
})
export class RunsListPage implements OnInit {
  readonly runs = inject(RunsStore);
  private readonly router = inject(Router);
  private readonly profileStore = inject(TickerProfileStore);

  readonly loading = signal(true);
  readonly rerunningId = signal<number | null>(null);
  readonly sourceFilter = signal<SourceFilter>('all');
  readonly statusFilter = signal<StatusFilter>('all');
  readonly searchTerm = signal('');
  // P10 §D4: server-side time window (default 30 days) + page.
  readonly windowFilter = signal<'30d' | 'all'>('30d');
  readonly page = signal(1);
  readonly pageSize = 50;
  readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.runs.runsCount() / this.pageSize)),
  );

  readonly windowOptions: { id: '30d' | 'all'; label: string }[] = [
    { id: '30d', label: 'Last 30d' },
    { id: 'all', label: 'All time' },
  ];

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
    this.reload();
  }

  private reload(): void {
    this.loading.set(true);
    this.runs.listRuns({
      search: this.searchTerm() || undefined,
      days: this.windowFilter() === 'all' ? 'all' : undefined,
      page: this.page(),
    }).subscribe({
      next: (rows) => {
        this.loading.set(false);
        // Lazy-prefetch names for visible tickers (cheap; deduped batch).
        const tickers = [...new Set(rows.flatMap((r) => r.tickers || []))];
        if (tickers.length) this.profileStore.fetchNames(tickers).subscribe();
      },
      error: () => this.loading.set(false),
    });
  }

  setWindow(w: '30d' | 'all'): void {
    this.windowFilter.set(w);
    this.page.set(1);
    this.reload();
  }

  setPage(p: number): void {
    this.page.set(Math.max(1, Math.min(p, this.totalPages())));
    this.reload();
  }

  personaCount(r: RunSummary): number {
    return r.personas?.length ?? 0;
  }

  decisionToneClass(action: DecisionAction): string {
    switch (action) {
      case 'buy':
      case 'cover_short':
      case 'enter':
        return 'tone-long';
      case 'sell':
      case 'open_short':
        return 'tone-short';
      case 'hold':
        return 'tone-hold';
      case 'skip':
      default:
        return 'tone-skip';
    }
  }

  setSourceFilter(s: SourceFilter): void { this.sourceFilter.set(s); }
  setStatusFilter(s: StatusFilter): void { this.statusFilter.set(s); }
  resetFilters(): void { this.sourceFilter.set('all'); this.statusFilter.set('all'); }

  // P3b: server-side full-text transcript search (Postgres FTS on the backend).
  doSearch(term: string): void {
    const q = term.trim();
    this.searchTerm.set(q);
    this.page.set(1);
    this.reload();
  }
  clearSearch(input: HTMLInputElement): void {
    input.value = '';
    this.doSearch('');
  }

  open(r: RunSummary): void { this.router.navigate(['/runs', r.id]); }

  // P4 WS-A: rerun a failed/cancelled run. Stops row-click navigation, creates
  // a new run from the original's payload, and navigates to the new run.
  rerun(r: RunSummary, ev: Event): void {
    ev.stopPropagation();
    if (this.rerunningId() !== null) return;
    this.rerunningId.set(r.id);
    this.runs.rerunRun(r.id).subscribe({
      next: (res) => {
        this.rerunningId.set(null);
        this.router.navigate(['/runs', res.id]);
      },
      error: () => this.rerunningId.set(null),
    });
  }
}
