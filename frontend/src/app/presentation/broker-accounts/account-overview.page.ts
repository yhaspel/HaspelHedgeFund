import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { BrokerStore } from '../../abstraction/broker.store';
import { OrderConfirmModalComponent } from './order-confirm.modal';
import { BrokerOrderRow } from '../../core/models/broker.model';

@Component({
  selector: 'hf-broker-account-overview-page',
  standalone: true,
  imports: [
    CommonModule, DatePipe, DecimalPipe, RouterLink,
    AppShellComponent, KpiTileComponent, EmptyStateComponent,
    OrderConfirmModalComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[
      {label:'Broker accounts', link:'/broker-accounts'},
      {label: account()?.label || 'Account'}]">
      @if (loading()) {
        <div class="card p-4 text-xs text-text-3">Loading account…</div>
      } @else if (overview(); as o) {
        <div class="page-head">
          <div>
            <div class="eyebrow">{{ o.account.broker_display }}</div>
            <h1 class="mt-1.5">{{ o.account.label }}
              <span class="pill ml-2" [class.warn]="o.account.mode === 'live'"
                    [class.ok]="o.account.mode === 'paper'">
                <span class="dot"></span>{{ o.account.mode }}
              </span>
            </h1>
            <p class="text-[11.5px] text-text-3 mt-1 mono">
              account_id={{ o.account.account_id }} · status={{ o.account.connection_status }}
            </p>
          </div>
          <div class="head-actions">
            <button class="btn" (click)="onSync()" data-test="sync-btn"
                    [disabled]="syncing()">
              {{ syncing() ? 'Syncing…' : 'Sync now' }}
            </button>
            <button class="btn primary" (click)="openCreateDraft()" data-test="new-order-btn">
              <svg width="14" height="14" class="mr-1.5" aria-hidden="true">
                <use href="/icons.svg#i-plus" /></svg>
              New draft order
            </button>
          </div>
        </div>

        @if (o.drift.pending) {
          <div role="alert" class="pill warn h-auto py-2 px-3 mb-3.5 flex items-center"
               data-test="drift-banner">
            <span class="dot"></span>
            <span class="flex-1">
              <strong>Reconciliation drift detected.</strong>
              {{ o.drift.last_notes }}
            </span>
            <button class="btn btn-sm ml-2" (click)="ackDrift()" data-test="ack-drift-btn">
              Acknowledge
            </button>
          </div>
        }

        @if (lastError(); as e) {
          <div role="alert" class="pill err h-auto py-2 px-3 mb-3.5"><span class="dot"></span>{{ e }}</div>
        }

        <section class="kpi-row mb-4">
          <hf-kpi-tile eyebrow="Broker cash"
                       [value]="'$' + (+o.broker.cash | number: '1.2-2')"
                       [sub]="'buying power $' + (+o.broker.buying_power | number: '1.2-2')" />
          <hf-kpi-tile eyebrow="Portfolio cash"
                       [value]="'$' + (+o.portfolio.cash_balance | number: '1.2-2')"
                       [sub]="'broker-backed (kind=broker)'" />
          <hf-kpi-tile eyebrow="Positions"
                       [value]="o.portfolio.positions.length.toString()"
                       [sub]="o.portfolio.positions.length === 1 ? 'open ticker' : 'open tickers'" />
          <hf-kpi-tile eyebrow="Recent fills"
                       [value]="o.recent_fills.length.toString()"
                       sub="last 25 events" />
        </section>

        <section class="card p-0 overflow-hidden mb-4">
          <div class="card-hd"><span class="title">Positions</span></div>
          @if (o.portfolio.positions.length === 0) {
            <div class="p-4 text-xs text-text-3">No positions yet. Submit a draft order to begin.</div>
          } @else {
            <table class="tbl w-full" data-test="positions-table">
              <thead>
                <tr>
                  <th class="text-left">Ticker</th>
                  <th class="text-right">Quantity</th>
                  <th class="text-right">Avg cost</th>
                  <th class="text-left">Side</th>
                  <th class="text-right">Realized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                @for (p of o.portfolio.positions; track p.ticker) {
                  <tr [attr.data-test]="'position-' + p.ticker">
                    <td class="font-medium">{{ p.ticker }}</td>
                    <td class="text-right mono">{{ +p.quantity | number: '1.0-6' }}</td>
                    <td class="text-right mono">{{ +p.avg_cost | number: '1.2-4' }}</td>
                    <td>
                      <span class="pill" [class.ok]="!p.is_short" [class.warn]="p.is_short">
                        <span class="dot"></span>{{ p.is_short ? 'short' : 'long' }}
                      </span>
                    </td>
                    <td class="text-right mono">{{ '$' + (+p.realized_pnl | number: '1.2-2') }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section class="card p-0 overflow-hidden mb-4">
          <div class="card-hd"><span class="title">Pending orders</span></div>
          @if (pending().length === 0) {
            <div class="p-4 text-xs text-text-3">No drafts. Use “New draft order”.</div>
          } @else {
            <table class="tbl w-full" data-test="pending-orders-table">
              <thead>
                <tr>
                  <th class="text-left">Ticker</th>
                  <th class="text-left">Side</th>
                  <th class="text-right">Qty</th>
                  <th class="text-right">Notional</th>
                  <th class="text-left">Status</th>
                  <th class="text-right"></th>
                </tr>
              </thead>
              <tbody>
                @for (ord of pending(); track ord.id) {
                  <tr [attr.data-test]="'order-row-' + ord.id">
                    <td class="font-medium">{{ ord.ticker }}</td>
                    <td>
                      <span class="pill" [class.ok]="ord.side === 'buy'" [class.warn]="ord.side === 'sell'">
                        <span class="dot"></span>{{ ord.side }}
                      </span>
                    </td>
                    <td class="text-right mono">{{ +ord.quantity | number: '1.0-4' }}</td>
                    <td class="text-right mono">{{ '$' + (+ord.notional_estimate | number: '1.2-2') }}</td>
                    <td><span class="pill"><span class="dot"></span>{{ ord.status }}</span></td>
                    <td class="text-right">
                      <button class="btn primary btn-sm" (click)="openConfirm(ord)"
                              [attr.data-test]="'confirm-order-' + ord.id">
                        Confirm &amp; submit
                      </button>
                      <button class="btn ghost btn-sm ml-1.5" (click)="onCancel(ord)"
                              [attr.data-test]="'cancel-order-' + ord.id">Cancel</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section class="card p-0 overflow-hidden">
          <div class="card-hd"><span class="title">Recent fills</span></div>
          @if (o.recent_fills.length === 0) {
            <div class="p-4 text-xs text-text-3">No fills yet.</div>
          } @else {
            <table class="tbl w-full" data-test="fills-table">
              <thead>
                <tr>
                  <th>When</th><th>Ticker</th><th>Side</th>
                  <th class="text-right">Qty</th><th class="text-right">Price</th>
                </tr>
              </thead>
              <tbody>
                @for (f of o.recent_fills; track f.id) {
                  <tr [attr.data-test]="'fill-row-' + f.id">
                    <td class="text-[11.5px] text-text-3">{{ f.filled_at | date:'short' }}</td>
                    <td class="font-medium">{{ f.ticker }}</td>
                    <td>{{ f.side }}</td>
                    <td class="text-right mono">{{ +f.quantity | number: '1.0-6' }}</td>
                    <td class="text-right mono">{{ +f.price | number: '1.2-4' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>
      } @else {
        <hf-empty-state message="Account not found"
          detail="The account could not be loaded. It may have been disconnected.">
          <a class="btn mt-2" routerLink="/broker-accounts">Back to accounts</a>
        </hf-empty-state>
      }
    </hf-app-shell>

    @if (newDraftOpen()) {
      <div class="modal-overlay" (click)="newDraftOpen.set(false)">
        <div class="card" style="max-width:420px" (click)="$event.stopPropagation()">
          <div class="card-hd"><span class="title">New draft order</span></div>
          <div class="p-3.5 space-y-2.5">
            <label class="lbl block">Ticker
              <input class="input mono" type="text" [value]="draftTicker()"
                     (input)="draftTicker.set($any($event.target).value.toUpperCase())"
                     data-test="new-order-ticker" />
            </label>
            <div class="seg" role="radiogroup">
              <button class="seg-btn" type="button" [class.active]="draftSide() === 'buy'"
                      (click)="draftSide.set('buy')" data-test="new-order-side-buy">Buy</button>
              <button class="seg-btn" type="button" [class.active]="draftSide() === 'sell'"
                      (click)="draftSide.set('sell')" data-test="new-order-side-sell">Sell</button>
            </div>
            <label class="lbl block">Quantity
              <input class="input mono" type="number" step="0.0001" min="0"
                     [value]="draftQty()"
                     (input)="draftQty.set($any($event.target).value)"
                     data-test="new-order-qty" />
            </label>
            <label class="lbl block">Limit price (optional)
              <input class="input mono" type="number" step="0.01"
                     [value]="draftLimit()"
                     (input)="draftLimit.set($any($event.target).value)"
                     data-test="new-order-limit" />
            </label>
            @if (lastError(); as e) {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5"><span class="dot"></span>{{ e }}</div>
            }
            <div class="text-right">
              <button class="btn" (click)="newDraftOpen.set(false)">Cancel</button>
              <button class="btn primary ml-2" (click)="submitDraft()"
                      [disabled]="!canSubmitDraft()"
                      data-test="new-order-submit">Create</button>
            </div>
          </div>
        </div>
      </div>
    }

    @if (confirmingOrder(); as ord) {
      <hf-order-confirm-modal [order]="ord" [account]="overview()?.account || null"
                              (closed)="confirmingOrder.set(null)"
                              (confirmed)="onConfirmed($event)" />
    }
  `,
  styles: [`
    .modal-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6);
      z-index: var(--z-modal); display:flex; align-items:center; justify-content:center; padding:16px;
    }
  `],
})
export class BrokerAccountOverviewPage implements OnInit {
  private readonly store = inject(BrokerStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly overview = this.store.overview;
  protected readonly busy = this.store.busy;
  protected readonly loading = signal(true);
  protected readonly syncing = signal(false);
  protected readonly account = computed(() => this.overview()?.account ?? null);
  protected readonly newDraftOpen = signal(false);
  protected readonly draftTicker = signal('AAPL');
  protected readonly draftSide = signal<'buy' | 'sell'>('buy');
  protected readonly draftQty = signal('5');
  protected readonly draftLimit = signal('');
  protected readonly confirmingOrder = signal<BrokerOrderRow | null>(null);
  protected readonly lastError = signal<string | null>(null);
  protected readonly pending = computed(() =>
    this.store.orders().filter(
      (o) => o.status === 'draft' || o.status === 'confirmed',
    ),
  );

  ngOnInit(): void {
    const accountId = Number(this.route.snapshot.paramMap.get('id'));
    if (!accountId) return;
    this.refresh(accountId);
  }

  refresh(accountId: number | undefined = undefined): void {
    const id = accountId ?? this.overview()?.account.id;
    if (!id) return;
    this.loading.set(true);
    this.store.loadOverview(id).subscribe({
      next: () => {
        this.loading.set(false);
        this.store.loadOrders(id).subscribe();
      },
      error: (err) => {
        this.lastError.set(err?.error?.detail ?? 'Failed to load.');
        this.loading.set(false);
      },
    });
  }

  onSync(): void {
    const id = this.account()?.id;
    if (!id) return;
    this.syncing.set(true);
    this.store.syncAccount(id).subscribe({
      next: () => { this.syncing.set(false); this.refresh(id); },
      error: () => { this.syncing.set(false); },
    });
  }

  ackDrift(): void {
    const id = this.account()?.id;
    if (!id) return;
    this.store.acknowledgeDrift(id).subscribe({
      next: () => this.refresh(id),
    });
  }

  openCreateDraft(): void {
    this.lastError.set(null);
    this.newDraftOpen.set(true);
  }

  canSubmitDraft(): boolean {
    const qty = parseFloat(this.draftQty() || '0');
    return Boolean(this.draftTicker()) && qty > 0;
  }

  submitDraft(): void {
    const id = this.account()?.id;
    if (!id) return;
    const limit = this.draftLimit() ? parseFloat(this.draftLimit()) : null;
    this.store.createDraftOrder({
      broker_account: id,
      ticker: this.draftTicker(),
      side: this.draftSide(),
      quantity: this.draftQty(),
      order_type: limit ? 'limit' : 'market',
      limit_price: limit,
    }).subscribe({
      next: () => {
        this.newDraftOpen.set(false);
        this.refresh(id);
      },
      error: (err) => this.lastError.set(
        err?.error?.detail ?? Object.values(err?.error ?? {}).join('; ') ?? 'Failed.',
      ),
    });
  }

  openConfirm(ord: BrokerOrderRow): void {
    this.lastError.set(null);
    this.confirmingOrder.set(ord);
  }

  onConfirmed(_row: BrokerOrderRow): void {
    this.confirmingOrder.set(null);
    const id = this.account()?.id;
    if (id) this.refresh(id);
  }

  onCancel(ord: BrokerOrderRow): void {
    if (!confirm(`Cancel order ${ord.ticker} ${ord.side} ${ord.quantity}?`)) return;
    this.store.cancelOrder(ord.id).subscribe({
      next: () => this.refresh(),
    });
  }
}
