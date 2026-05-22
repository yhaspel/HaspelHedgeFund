import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import {
  MarkCadence,
  PortfolioOverview,
  PositionValuation,
} from '../../core/models/portfolio.model';
import { AppShellComponent } from '../shared/app-shell.component';
import { CashAdjustModalComponent } from './cash-adjust.modal';
import { EnterPositionModalComponent } from './enter-position.modal';
import { PositionsTableComponent } from './positions-table.component';

@Component({
  selector: 'hf-portfolio-page',
  standalone: true,
  imports: [
    CommonModule, DatePipe, DecimalPipe,
    AppShellComponent,
    PositionsTableComponent,
    EnterPositionModalComponent,
    CashAdjustModalComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{label:'Portfolio'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Manual Book · Paper</div>
          <h1 style="margin-top:6px">Portfolio</h1>
          <p style="font-size:13px;color:var(--text-2);margin-top:4px">
            A local paper book seeded with $100,000. No broker, no real money.
            <span data-test="cadence-summary">{{ cadenceSummary() }}</span>
          </p>
        </div>
        <div class="head-actions">
          <button
            class="btn"
            [class.primary]="cadence() === 'manual'"
            [class.ghost]="cadence() !== 'manual'"
            (click)="onRefreshMarks()"
            [disabled]="refreshDisabled()"
            data-test="refresh-marks-btn"
            [title]="refreshTitle()">
            <svg width="13" height="13" style="margin-right:4px">
              <use href="/icons.svg#i-rerun" />
            </svg>
            {{ store.refreshing() ? 'Refreshing…' : 'Refresh marks' }}
          </button>
          <button class="btn" (click)="cashOpen.set(true)" data-test="cash-btn">
            Cash deposit / withdraw
          </button>
          <button class="btn primary" (click)="openEntry()" data-test="add-position-btn">
            <svg width="14" height="14" style="margin-right:6px">
              <use href="/icons.svg#i-plus" />
            </svg>
            Add position
          </button>
        </div>
      </div>

      @if (loadError(); as msg) {
        <div class="pill err" style="height:auto;padding:8px 12px;margin-bottom:14px">
          <span class="dot"></span>{{ msg }}
        </div>
      }

      @if (store.overview(); as o) {
        <section class="kpi-row" style="margin-bottom:16px">
          <div class="kpi">
            <div class="eyebrow">Total value</div>
            <div class="v mono">$\{{ +o.total_value | number: '1.2-2' }}</div>
            <div class="sub mono">cash $\{{ +o.cash_balance | number: '1.2-2' }} ·
              free $\{{ +o.free_cash | number: '1.2-2' }}</div>
          </div>
          <div class="kpi">
            <div class="eyebrow">Unrealized P&amp;L</div>
            <div class="v mono"
                 [style.color]="+o.unrealized_pnl > 0 ? 'var(--acc-long-fg)' : (+o.unrealized_pnl < 0 ? 'var(--acc-short-fg)' : null)">
              $\{{ +o.unrealized_pnl | number: '1.2-2' }}
            </div>
            <div class="sub">across {{ o.positions.length }} position{{ o.positions.length === 1 ? '' : 's' }}</div>
          </div>
          <div class="kpi">
            <div class="eyebrow">Net exposure</div>
            <div class="v mono">{{ +o.net_exposure_pct | number: '1.2-2' }}%</div>
            <div class="sub">gross {{ +o.gross_exposure_pct | number: '1.2-2' }}%</div>
          </div>
          <div class="kpi">
            <div class="eyebrow">Realized P&amp;L</div>
            <div class="v mono"
                 [style.color]="+o.realized_pnl > 0 ? 'var(--acc-long-fg)' : (+o.realized_pnl < 0 ? 'var(--acc-short-fg)' : null)">
              $\{{ +o.realized_pnl | number: '1.2-2' }}
            </div>
            <div class="sub">closed lots</div>
          </div>
        </section>

        @if (o.warnings.length > 0) {
          <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:14px">
            @for (w of o.warnings; track w) {
              <div class="pill warn" style="height:auto;padding:6px 12px">
                <span class="dot"></span>{{ w }}
              </div>
            }
          </div>
        }

        <section class="card" style="margin-bottom:18px">
          <div class="card-hd">
            <span class="title">Positions</span>
            <div class="actions">
              @if (+o.reserved_short_proceeds > 0) {
                <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                  reserved short proceeds $\{{ +o.reserved_short_proceeds | number: '1.2-2' }}
                </span>
              }
            </div>
          </div>
          <hf-positions-table
            [positions]="o.positions"
            (closeClick)="onClose($event)"
            (editClick)="onEdit($event)" />
        </section>
      } @else {
        <p style="color:var(--text-2)">Loading manual book…</p>
      }

      <section class="card">
        <div class="card-hd">
          <span class="title">Transaction ledger</span>
          <div class="actions">
            <span class="mono" style="font-size:11.5px;color:var(--text-3)">
              showing latest 50
            </span>
          </div>
        </div>
        @if (store.ledger().length === 0) {
          <p style="font-size:12px;color:var(--text-3);margin:0;padding:16px">
            No transactions yet. The ledger captures every cash movement and
            position event.
          </p>
        } @else {
          <table class="tbl">
            <thead>
              <tr>
                <th>When</th>
                <th>Kind</th>
                <th>Ticker</th>
                <th class="right">Qty</th>
                <th class="right">Price</th>
                <th class="right">Cash Δ</th>
                <th class="right">Realized P&amp;L</th>
                <th class="right">Cash after</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              @for (e of ledgerHead(); track e.id) {
                <tr>
                  <td class="mono" style="font-size:11.5px">{{ e.created_at | date: 'short' }}</td>
                  <td><span class="pill" [class.ok]="isLong(e.kind)" [class.err]="isShortLike(e.kind)" [class.info]="e.kind === 'edit_adjustment'"><span class="dot"></span>{{ humanKind(e.kind) }}</span></td>
                  <td class="mono">{{ e.ticker || '—' }}</td>
                  <td class="num mono">{{ formatQty(e.quantity_delta) }}</td>
                  <td class="num mono">{{ e.price !== null ? (+e.price | number: '1.2-4') : '—' }}</td>
                  <td class="num mono"
                      [style.color]="+e.cash_delta > 0 ? 'var(--acc-long-fg)' : (+e.cash_delta < 0 ? 'var(--acc-short-fg)' : null)">
                    {{ +e.cash_delta | number: '1.2-2' }}
                  </td>
                  <td class="num mono">{{ +e.realized_pnl === 0 ? '—' : (+e.realized_pnl | number: '1.2-2') }}</td>
                  <td class="num mono">{{ +e.cash_balance_after | number: '1.2-2' }}</td>
                  <td style="font-size:11.5px;color:var(--text-3)">{{ truncateNote(e.note) }}</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>

      <hf-enter-position-modal
        [open]="entryOpen()"
        [prefill]="entryPrefill()"
        (closed)="onEntryClosed($event)" />
      <hf-cash-adjust-modal
        [open]="cashOpen()"
        (closed)="onCashClosed($event)" />

      @if (editFor(); as pos) {
        <div class="modal-overlay" (click)="cancelEdit()">
          <div class="card edit-modal" (click)="$event.stopPropagation()">
            <div class="card-hd">
              <span class="title">Edit position · {{ pos.ticker }}</span>
              <div class="actions">
                <button class="icon-btn" (click)="cancelEdit()"><svg width="16" height="16"><use href="/icons.svg#i-x" /></svg></button>
              </div>
            </div>
            <div style="padding:16px;display:flex;flex-direction:column;gap:12px">
              <p style="font-size:11.5px;color:var(--text-3);margin:0">
                Correction-only — adjusting cash should use deposit/withdraw,
                trades should use close/reduce. The edit writes an audit
                <span class="mono">edit_adjustment</span> ledger entry.
              </p>
              <div style="display:flex;align-items:center;gap:10px">
                <label class="lbl" style="flex:0 0 110px;font-size:12px;color:var(--text-2)">Quantity</label>
                <input class="input mono" type="number" step="any"
                       [value]="editQty" (input)="editQty = $any($event.target).value" style="width:160px" />
              </div>
              <div style="display:flex;align-items:center;gap:10px">
                <label class="lbl" style="flex:0 0 110px;font-size:12px;color:var(--text-2)">Avg cost</label>
                <input class="input mono" type="number" step="0.0001"
                       [value]="editAvgCost" (input)="editAvgCost = $any($event.target).value" style="width:160px" />
              </div>
              <div style="display:flex;align-items:center;gap:10px">
                <label class="lbl" style="flex:0 0 110px;font-size:12px;color:var(--text-2)">Note</label>
                <input class="input" type="text"
                       [value]="editNote" (input)="editNote = $any($event.target).value" />
              </div>
              @if (editError(); as msg) {
                <div class="pill err" style="height:auto;padding:6px 10px"><span class="dot"></span>{{ msg }}</div>
              }
            </div>
            <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border)">
              <button class="btn" (click)="cancelEdit()">Cancel</button>
              <button class="btn primary" (click)="confirmEdit()" [disabled]="store.busy()">Save</button>
            </div>
          </div>
        </div>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .modal-overlay {
        position: fixed; inset: 0; background: rgba(0,0,0,0.6);
        z-index: var(--z-modal); display: flex; align-items: center;
        justify-content: center; padding: 16px;
      }
      .edit-modal { max-width: 480px; width: 100%; }
    `,
  ],
})
export class PortfolioPage implements OnInit, OnDestroy {
  readonly store = inject(PortfolioStore);

  loadError = signal<string | null>(null);
  entryOpen = signal(false);
  cashOpen = signal(false);
  entryPrefill = signal<{ runId?: number; decisionId?: number; ticker?: string; side?: 'long' | 'short' } | null>(null);

  editFor = signal<PositionValuation | null>(null);
  editQty: string = '';
  editAvgCost: string = '';
  editNote: string = '';
  editError = signal<string | null>(null);

  // P3.1: cadence-aware auto-refresh polling.
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private currentIntervalMs = 0;
  private readonly onVisibility = () => this.applyPolling();

  cadence = computed<MarkCadence>(
    () => this.store.preferences()?.mark_cadence ?? 'daily',
  );

  ngOnInit(): void {
    this.refresh();
    document.addEventListener('visibilitychange', this.onVisibility);
  }

  ngOnDestroy(): void {
    this.stopPolling();
    document.removeEventListener('visibilitychange', this.onVisibility);
  }

  refresh(): void {
    this.loadError.set(null);
    this.store.loadOverview().subscribe({
      next: () => this.applyPolling(),
      error: (err) => this.loadError.set(err?.error?.detail || 'Failed to load portfolio.'),
    });
    this.store.loadLedger().subscribe({
      error: () => { /* silently ignore — ledger optional */ },
    });
  }

  /**
   * Start / stop / reschedule the polling timer based on the user's
   * preferences and tab visibility. Idempotent — called on every change
   * to cadence/interval and on tab visibility flips.
   */
  private applyPolling(): void {
    const prefs = this.store.preferences();
    const cadence = prefs?.mark_cadence ?? 'daily';
    const minutes = Math.max(5, prefs?.interval_minutes ?? 20);
    const targetMs = cadence === 'delayed' ? minutes * 60 * 1000 : 0;
    const visible = document.visibilityState === 'visible';

    if (!targetMs || !visible) {
      this.stopPolling();
      return;
    }
    if (this.pollHandle && this.currentIntervalMs === targetMs) return;

    this.stopPolling();
    this.currentIntervalMs = targetMs;
    this.pollHandle = setInterval(() => {
      this.store.loadOverview().subscribe({ error: () => { /* ignore */ } });
    }, targetMs);
  }

  private stopPolling(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
      this.currentIntervalMs = 0;
    }
  }

  cadenceSummary(): string {
    const prefs = this.store.preferences();
    if (!prefs) return 'Marks update from the latest daily close.';
    if (prefs.mark_cadence === 'daily') {
      return 'Marks update from the latest daily close.';
    }
    if (prefs.mark_cadence === 'delayed') {
      return `Marks auto-refresh every ${prefs.interval_minutes} minutes.`;
    }
    return 'Marks refresh only when you click Refresh marks.';
  }

  refreshDisabled(): boolean {
    return this.store.refreshing();
  }

  refreshTitle(): string {
    const prefs = this.store.preferences();
    const last = prefs?.last_refreshed_at;
    if (!last) return 'Re-mark all open positions.';
    const ago = this.relativeTime(last);
    return `Marks updated ${ago}. Click to refetch.`;
  }

  private relativeTime(iso: string): string {
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return 'recently';
    const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    return `${hours}h ago`;
  }

  onRefreshMarks(): void {
    this.store.refreshMarks().subscribe({
      next: () => {
        // ledger may not have changed; skip the reload to avoid flicker.
      },
      error: (err) =>
        this.loadError.set(err?.error?.detail || 'Refresh failed.'),
    });
  }

  ledgerHead() {
    return this.store.ledger().slice(0, 50);
  }

  openEntry(): void {
    this.entryPrefill.set(null);
    this.entryOpen.set(true);
  }

  onEntryClosed(e: { saved: boolean }): void {
    this.entryOpen.set(false);
    this.entryPrefill.set(null);
    if (e.saved) this.refresh();
  }

  onCashClosed(e: { saved: boolean }): void {
    this.cashOpen.set(false);
    if (e.saved) this.refresh();
  }

  onClose(pos: PositionValuation): void {
    if (!confirm(`Close ${pos.ticker} at the latest mark?`)) return;
    this.store.closePosition(pos.id, {}).subscribe({
      next: (r) => {
        if (r.realized_pnl) {
          // Lightweight feedback; the page header already reflects the new totals.
        }
        this.refresh();
      },
      error: (err) => alert(err?.error?.detail || 'Failed to close position.'),
    });
  }

  onEdit(pos: PositionValuation): void {
    this.editFor.set(pos);
    this.editQty = String(Math.abs(Number(pos.quantity)));
    this.editAvgCost = String(Number(pos.avg_cost));
    this.editNote = pos.note || '';
    this.editError.set(null);
  }

  cancelEdit(): void {
    this.editFor.set(null);
  }

  confirmEdit(): void {
    const p = this.editFor();
    if (!p) return;
    this.editError.set(null);
    const body: {
      quantity?: string; avg_cost?: string; note?: string;
      quantity_mode?: 'whole' | 'fractional';
    } = {};
    const qty = Number(this.editQty);
    const avg = Number(this.editAvgCost);
    if (Number.isFinite(qty) && qty > 0) body.quantity = String(qty);
    if (Number.isFinite(avg) && avg > 0) body.avg_cost = String(avg);
    if (this.editNote !== p.note) body.note = this.editNote;
    body.quantity_mode = Math.abs(qty - Math.round(qty)) < 1e-9 ? 'whole' : 'fractional';

    this.store.editPosition(p.id, body).subscribe({
      next: () => {
        this.editFor.set(null);
        this.refresh();
      },
      error: (err) => this.editError.set(err?.error?.detail || 'Failed to save edit.'),
    });
  }

  isLong(kind: string): boolean {
    return kind === 'deposit' || kind === 'position_open' || kind === 'position_increase';
  }

  isShortLike(kind: string): boolean {
    return kind === 'withdrawal' || kind === 'position_close' || kind === 'position_reduce';
  }

  humanKind(kind: string): string {
    switch (kind) {
      case 'deposit': return 'Deposit';
      case 'withdrawal': return 'Withdrawal';
      case 'position_open': return 'Open';
      case 'position_increase': return 'Increase';
      case 'position_reduce': return 'Reduce';
      case 'position_close': return 'Close';
      case 'edit_adjustment': return 'Edit';
      default: return kind;
    }
  }

  formatQty(raw: string): string {
    const n = Number(raw);
    if (!Number.isFinite(n) || n === 0) return '—';
    if (Math.abs(n - Math.round(n)) < 1e-9) return n.toFixed(0);
    return n.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }

  truncateNote(note: string): string {
    if (!note) return '';
    return note.length > 60 ? note.slice(0, 57) + '…' : note;
  }

  // External API: caller can prefill from a run/decision.
  prefillFromRun(runId: number, decisionId: number, ticker: string, side: 'long' | 'short'): void {
    this.entryPrefill.set({ runId, decisionId, ticker, side });
    this.entryOpen.set(true);
  }
}
