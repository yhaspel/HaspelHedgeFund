import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { ErrorStateComponent } from '../shared/error-state.component';
import { apiErrorMessage } from '../../core/api/api-error';
import { engineVersionBadge, engineVersionLabel } from '../../core/models/backtest.model';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { ConfirmService } from '../shared/confirm.service';

const DELETABLE_BACKTEST_STATUSES = new Set([
  'cancelled', 'aborted_budget', 'aborted_partial', 'synthetic',
]);

@Component({
  selector: 'hf-backtests-list',
  standalone: true,
  imports: [CommonModule, RouterLink, DecimalPipe, AppShellComponent, EmptyStateComponent, ErrorStateComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Walk-forward backtests</div>
          <h1 class="mt-1.5">Backtests</h1>
        </div>
        <div class="head-actions">
          <a class="btn primary" routerLink="/backtests/new"><svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New walk-forward</a>
        </div>
      </div>

      <section class="card">
        <!-- P10 §D4: done/failed rows can never be deleted (audit trail), so
             archive is the declutter verb; archived rows hide behind the chip. -->
        <div class="card-hd">
          <h2 class="title">
            {{ showArchived() ? 'All backtests' : 'Backtests' }}
            ({{ visible().length }}<span class="text-text-2">{{
              store.listCount() > visible().length ? ' of ' + store.listCount() : ''
            }}</span>)
          </h2>
          @if (archivedCount() > 0 || showArchived()) {
            <button type="button" class="chip" [class.chip-on]="showArchived()"
                    (click)="toggleShowArchived()" data-test="bt-archived-toggle">
              Archived{{ showArchived() ? ' (' + archivedCount() + ')' : '' }}
            </button>
          }
        </div>
        @if (loadError()) {
          <div class="card-bd">
            <hf-error-state
              title="Couldn't load your backtests"
              [detail]="loadError()"
              (retry)="reload()"
            ></hf-error-state>
          </div>
        } @else if (visible().length === 0) {
          <hf-empty-state message="No backtests yet.">
            <a class="btn primary" routerLink="/backtests/new">Start your first walk-forward</a>
          </hf-empty-state>
        } @else {
          <div class="tbl-scroll">
          <table class="tbl">
            <thead><tr>
              <th>Name</th><th>Status</th><th>Period</th>
              <th class="right">OOS return</th>
              <th class="right" title="The stitched long-run Sharpe of the whole OOS curve. Averaging 63-day fold Sharpes biases ~0.3–0.4 upward, so that number is only in the tooltip.">OOS Sharpe (stitched)</th>
              <th class="right">Deflation</th>
              <th><span class="visually-hidden">Actions</span></th>
            </tr></thead>
            <tbody>
              @for(bt of visible(); track bt.id){
                <tr>
                  <td>
                    <a [routerLink]="['/backtests', bt.id]" class="text-[var(--acc-info-fg)]">{{ bt.name }}</a>
                    @if (bt.data_era === 'price_only') {
                      <span class="pill warn" title="Pre-PR#50 price-only bars — understates returns; excluded as §9-gate evidence">P</span>
                    }
                    <!-- Rows in this table are read against each other, and v1
                         economics are not comparable with v2's. -->
                    <span class="pill" [title]="engineLabel(bt.engine_version)"
                          [attr.data-test]="'engine-version-' + bt.id">{{ engineBadge(bt.engine_version) }}</span>
                  </td>
                  <td>
                    <span class="pill"
                      [class.ok]="bt.status==='done'" [class.warn]="bt.status==='running' || bt.status==='queued'"
                      [class.err]="bt.status==='failed'"><span class="dot"></span>{{ bt.status }}
                      @if(bt.status==='running' || bt.status==='queued'){<span class="mono"> · {{ bt.progress_pct }}%</span>}
                    </span>
                  </td>
                  <td class="mono text-text-2">{{ bt.start_date }} → {{ bt.end_date }}</td>
                  <td class="num">{{ bt.total_return_pct !== null ? ((bt.total_return_pct | number:'1.2-2') + '%') : '—' }}</td>
                  <!-- P10 §B2: the stitched number is the honest headline;
                       mean-of-folds stays available on hover. -->
                  <td class="num" [attr.data-test]="'sharpe-' + bt.id"
                      [title]="bt.oos_sharpe !== null ? 'mean of folds ' + (bt.oos_sharpe | number:'1.2-2') : 'no per-fold Sharpe recorded'">
                    {{ bt.stitched_sharpe !== null && bt.stitched_sharpe !== undefined
                        ? (bt.stitched_sharpe | number:'1.2-2') : '—' }}
                  </td>
                  <!-- P10 §B4: the OOS/IS ratio only means something when an IS
                       candidate search ran — deterministic runs show n/a. -->
                  @if (bt.deflation_meaningful) {
                    <td class="num"
                      [style.color]="bt.deflation !== null && bt.deflation < 0.3 ? 'var(--acc-short-fg)' : bt.deflation !== null && bt.deflation < 0.5 ? 'var(--acc-hold-fg)' : 'var(--acc-long-fg)'">
                      {{ bt.deflation !== null ? (bt.deflation | number:'1.2-2') : '—' }}
                      @if(bt.deflation !== null){<span aria-hidden="true"> {{ bt.deflation < 0.3 ? '⚠' : bt.deflation < 0.5 ? '•' : '✓' }}</span>}
                    </td>
                  } @else {
                    <td class="num text-text-2" title="No candidate search (deterministic run) — the OOS/IS ratio is not an overfit guard here.">n/a</td>
                  }
                  <td class="right whitespace-nowrap">
                    @if (bt.status !== 'running' && bt.status !== 'queued') {
                      <button type="button" class="btn ghost sm"
                              (click)="toggleArchive(bt)"
                              [disabled]="archivingId() === bt.id"
                              [attr.data-test]="'archive-backtest-' + bt.id">
                        {{ bt.archived_at ? 'Unarchive' : 'Archive' }}
                      </button>
                    }
                    @if (canDelete(bt.status)) {
                      <button type="button" class="btn danger sm ml-2"
                              (click)="confirmDelete(bt.id, bt.name)"
                              [disabled]="deletingId() === bt.id"
                              [attr.data-test]="'delete-backtest-' + bt.id">
                        Delete
                      </button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
          </div>
          <!-- P10 §D4: pager (50/page server-side). -->
          @if (store.listCount() > pageSize) {
            <div class="pager">
              <button type="button" class="btn ghost sm" [disabled]="page() <= 1"
                      (click)="setPage(page() - 1)">← Prev</button>
              <span class="mono text-2xs text-text-3">page {{ page() }} / {{ totalPages() }}</span>
              <button type="button" class="btn ghost sm" [disabled]="page() >= totalPages()"
                      (click)="setPage(page() + 1)">Next →</button>
            </div>
          }
        }
      </section>
    </hf-app-shell>
  `,
  styles: [`
    .chip {
      background: transparent; border: 1px solid var(--border); color: var(--text-2);
      font-size: 11px; line-height: 1; padding: 0 10px; min-height: 24px;
      display: inline-flex; align-items: center; border-radius: var(--r-full); cursor: pointer;
    }
    .chip:hover { background: var(--surface-2); }
    .chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }
    .chip-on { background: var(--surface-2); color: var(--text); border-color: var(--text-3); }
    .pager {
      display: flex; align-items: center; justify-content: center;
      gap: 12px; padding: 10px 0 4px;
    }
  `],
})
export class BacktestsListPage implements OnInit {
  readonly store = inject(BacktestsStore);
  private readonly confirm = inject(ConfirmService);
  readonly deletingId = signal<number | null>(null);
  readonly loading = signal(true);
  /** GET /backtests/ failed — distinct from "this account has no backtests". */
  readonly loadError = signal<string | null>(null);
  readonly engineLabel = engineVersionLabel;
  readonly engineBadge = engineVersionBadge;
  readonly archivingId = signal<number | null>(null);
  readonly showArchived = signal(false);
  readonly page = signal(1);
  readonly pageSize = 50;
  readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.store.listCount() / this.pageSize)),
  );
  // The chip needs a count of archived rows; when the default (active-only)
  // list is shown we only know the visible rows, so count archived among the
  // current page when showing all, else show a plain "Archived" affordance.
  readonly archivedCount = computed(
    () => this.store.list().filter((b) => !!b.archived_at).length,
  );
  readonly visible = computed(() => {
    const rows = this.store.list();
    return this.showArchived() ? rows : rows.filter((b) => !b.archived_at);
  });

  ngOnInit(): void { this.reload(); }

  /**
   * A failed GET /backtests/ used to render "No backtests yet." — a 5xx looked
   * exactly like an empty account. Track the failure and offer a retry.
   */
  reload(): void {
    this.loading.set(true);
    this.loadError.set(null);
    this.store.listBacktests({
      includeArchived: this.showArchived(),
      page: this.page(),
    }).subscribe({
      next: () => this.loading.set(false),
      error: (e: unknown) => {
        this.loading.set(false);
        this.loadError.set(apiErrorMessage(e, 'Could not load your backtests.'));
      },
    });
  }

  toggleShowArchived(): void {
    this.showArchived.set(!this.showArchived());
    this.page.set(1);
    this.reload();
  }

  setPage(p: number): void {
    this.page.set(Math.max(1, Math.min(p, this.totalPages())));
    this.reload();
  }

  // P10 §D4: soft archive/unarchive — the declutter verb for protected rows.
  toggleArchive(bt: { id: number; archived_at: string | null }): void {
    if (this.archivingId() !== null) return;
    this.archivingId.set(bt.id);
    this.store.archiveBacktest(bt.id, !bt.archived_at).subscribe({
      next: () => { this.archivingId.set(null); this.reload(); },
      error: () => {
        this.archivingId.set(null);
        void this.confirm.notify({ title: 'Could not archive this backtest.' });
      },
    });
  }

  canDelete(status: string): boolean {
    return DELETABLE_BACKTEST_STATUSES.has(status);
  }

  // P4 WS-D: delete a cancelled / aborted / synthetic backtest. done + failed
  // never show the button (protected audit history).
  async confirmDelete(id: number, name: string): Promise<void> {
    if (this.deletingId() !== null) return;
    const ok = await this.confirm.ask({
      title: `Delete backtest "${name}"?`,
      body: 'This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    this.deletingId.set(id);
    this.store.deleteBacktest(id).subscribe({
      next: () => { this.deletingId.set(null); this.store.listBacktests().subscribe(); },
      error: () => { this.deletingId.set(null); void this.confirm.notify({ title: 'Could not delete this backtest.' }); },
    });
  }
}
