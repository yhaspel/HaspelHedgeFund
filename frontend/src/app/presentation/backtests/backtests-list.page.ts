import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { ConfirmService } from '../shared/confirm.service';

const DELETABLE_BACKTEST_STATUSES = new Set([
  'cancelled', 'aborted_budget', 'aborted_partial', 'synthetic',
]);

@Component({
  selector: 'hf-backtests-list',
  standalone: true,
  imports: [CommonModule, RouterLink, DecimalPipe, AppShellComponent, EmptyStateComponent],
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
        @if (store.list().length === 0) {
          <hf-empty-state message="No backtests yet.">
            <a class="btn primary" routerLink="/backtests/new">Start your first walk-forward</a>
          </hf-empty-state>
        } @else {
          <div class="tbl-scroll">
          <table class="tbl">
            <thead><tr>
              <th>Name</th><th>Status</th><th>Period</th>
              <th class="right">OOS return</th><th class="right">OOS Sharpe</th><th class="right">Deflation</th>
              <th><span class="visually-hidden">Actions</span></th>
            </tr></thead>
            <tbody>
              @for(bt of store.list(); track bt.id){
                <tr>
                  <td><a [routerLink]="['/backtests', bt.id]" class="text-[var(--acc-info-fg)]">{{ bt.name }}</a></td>
                  <td>
                    <span class="pill"
                      [class.ok]="bt.status==='done'" [class.warn]="bt.status==='running' || bt.status==='queued'"
                      [class.err]="bt.status==='failed'"><span class="dot"></span>{{ bt.status }}
                      @if(bt.status==='running' || bt.status==='queued'){<span class="mono"> · {{ bt.progress_pct }}%</span>}
                    </span>
                  </td>
                  <td class="mono text-text-2">{{ bt.start_date }} → {{ bt.end_date }}</td>
                  <td class="num">{{ bt.total_return_pct !== null ? ((bt.total_return_pct | number:'1.2-2') + '%') : '—' }}</td>
                  <td class="num">{{ bt.oos_sharpe !== null ? (bt.oos_sharpe | number:'1.2-2') : '—' }}</td>
                  <td class="num"
                    [style.color]="bt.deflation !== null && bt.deflation < 0.3 ? 'var(--acc-short-fg)' : bt.deflation !== null && bt.deflation < 0.5 ? 'var(--acc-hold-fg)' : 'var(--acc-long-fg)'">
                    {{ bt.deflation !== null ? (bt.deflation | number:'1.2-2') : '—' }}
                  </td>
                  <td class="right whitespace-nowrap">
                    @if (canDelete(bt.status)) {
                      <button type="button" class="btn danger sm"
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
        }
      </section>
    </hf-app-shell>
  `,
})
export class BacktestsListPage implements OnInit {
  readonly store = inject(BacktestsStore);
  private readonly confirm = inject(ConfirmService);
  readonly deletingId = signal<number | null>(null);
  ngOnInit(): void { this.store.listBacktests().subscribe(); }

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
