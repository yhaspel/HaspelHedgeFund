import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { BrokerStore } from '../../abstraction/broker.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import {
  BrokerOrderRow,
  BrokerOrderType,
  BrokerPortfolioSnapshot,
} from '../../core/models/broker.model';

type BrokerPosition = BrokerPortfolioSnapshot['positions'][number];

type OrderType = BrokerOrderType;

/**
 * Broker account overview — the demo book's main surface.
 *
 * The demo flow is streamlined: placing an order submits it straight to
 * the broker (no draft -> confirm gate). Market orders fill immediately at
 * the live price; limit/stop orders rest as "working" until the market
 * crosses their trigger.
 */
@Component({
  selector: 'hf-broker-account-overview-page',
  standalone: true,
  imports: [
    CommonModule, DatePipe, DecimalPipe, RouterLink,
    AppShellComponent, KpiTileComponent, EmptyStateComponent,
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
            <p class="text-[11.5px] text-text-3 mt-1 flex items-center gap-1.5">
              <span>Order quantity default:</span>
              <select class="input mono w-auto py-0.5 px-1.5"
                      [value]="o.account.default_quantity_mode"
                      (change)="setDefaultQuantityMode($any($event.target).value)"
                      data-test="account-qty-mode">
                <option value="whole">Whole shares</option>
                <option value="fractional">Fractional</option>
              </select>
            </p>
          </div>
          <div class="head-actions">
            <button class="btn" (click)="onSync()" data-test="sync-btn"
                    [disabled]="syncing()">
              {{ syncing() ? 'Syncing…' : 'Sync now' }}
            </button>
            <button class="btn primary" (click)="openNewOrder()" data-test="new-order-btn">
              <svg width="14" height="14" class="mr-1.5" aria-hidden="true">
                <use href="/icons.svg#i-plus" /></svg>
              New order
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
          <hf-kpi-tile eyebrow="Cash"
                       [value]="'$' + (+o.broker.cash | number: '1.2-2')"
                       sub="available to trade" />
          <hf-kpi-tile eyebrow="Equity"
                       [value]="'$' + (+o.broker.equity | number: '1.2-2')"
                       sub="cash + positions at cost" />
          <hf-kpi-tile eyebrow="Positions"
                       [value]="o.portfolio.positions.length.toString()"
                       [sub]="o.portfolio.positions.length === 1 ? 'open ticker' : 'open tickers'" />
          <hf-kpi-tile eyebrow="Working orders"
                       [value]="working().length.toString()"
                       sub="awaiting a fill" />
        </section>

        <section class="card p-0 overflow-hidden mb-4">
          <div class="card-hd"><h2 class="title">Positions</h2></div>
          @if (o.portfolio.positions.length === 0) {
            <div class="p-4 text-xs text-text-3">No positions yet. Place an order to begin.</div>
          } @else {
            <table class="tbl w-full" data-test="positions-table">
              <thead>
                <tr>
                  <th class="text-left">Ticker</th>
                  <th class="text-right">Quantity</th>
                  <th class="text-right">Avg cost</th>
                  <th class="text-left">Side</th>
                  <th class="text-right">Realized P&amp;L</th>
                  <th class="text-right"></th>
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
                    <td class="text-right whitespace-nowrap">
                      @if (supportsBracket()) {
                        <button class="btn ghost btn-sm" (click)="openProtect(p)"
                                [attr.data-test]="'protect-position-' + p.ticker">
                          Protect
                        </button>
                      }
                      <button class="btn ghost btn-sm ml-1" (click)="closePosition(p)"
                              [attr.data-test]="'close-position-' + p.ticker">
                        Close
                      </button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section class="card p-0 overflow-hidden mb-4">
          <div class="card-hd"><h2 class="title">Working orders</h2>
            <span class="hint">live orders waiting to fill — cancel any time</span>
          </div>
          @if (working().length === 0) {
            <div class="p-4 text-xs text-text-3">
              No working orders. Market orders fill instantly; limit &amp; stop
              orders appear here until the market reaches their trigger.
            </div>
          } @else {
            <table class="tbl w-full" data-test="working-orders-table">
              <thead>
                <tr>
                  <th class="text-left">Ticker</th>
                  <th class="text-left">Side</th>
                  <th class="text-left">Type</th>
                  <th class="text-right">Qty</th>
                  <th class="text-right">Trigger</th>
                  <th class="text-left">Status</th>
                  <th class="text-right"></th>
                </tr>
              </thead>
              <tbody>
                @for (ord of working(); track ord.id) {
                  <tr [attr.data-test]="'order-row-' + ord.id">
                    <td class="font-medium">{{ ord.ticker }}</td>
                    <td>
                      <span class="pill" [class.ok]="ord.side === 'buy'"
                            [class.err]="ord.side === 'sell'">
                        <span class="dot"></span>{{ ord.side }}
                      </span>
                    </td>
                    <td class="uppercase text-[11px] tracking-wide text-text-2">{{ ord.order_type }}</td>
                    <td class="text-right mono">{{ +ord.quantity | number: '1.0-4' }}</td>
                    <td class="text-right mono">{{ triggerLabel(ord) }}</td>
                    <td>
                      <span class="pill info live" data-test="order-status">
                        <span class="dot"></span>working
                      </span>
                    </td>
                    <td class="text-right">
                      <button class="btn ghost btn-sm danger" (click)="onCancel(ord)"
                              [attr.data-test]="'cancel-order-' + ord.id">Cancel</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section class="card p-0 overflow-hidden">
          <div class="card-hd"><h2 class="title">Recent fills</h2>
            <span class="hint">completed trades — last 25 events</span>
          </div>
          @if (o.recent_fills.length === 0) {
            <div class="p-4 text-xs text-text-3">No fills yet.</div>
          } @else {
            <table class="tbl w-full" data-test="fills-table">
              <thead>
                <tr>
                  <th>When</th><th>Ticker</th><th>Side</th>
                  <th class="text-right">Qty</th><th class="text-right">Fill price</th>
                  <th class="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                @for (f of o.recent_fills; track f.id) {
                  <tr [attr.data-test]="'fill-row-' + f.id">
                    <td class="text-[11.5px] text-text-3">{{ f.filled_at | date:'short' }}</td>
                    <td class="font-medium">{{ f.ticker }}</td>
                    <td>
                      <span class="pill" [class.ok]="f.side === 'buy'"
                            [class.err]="f.side === 'sell'">
                        <span class="dot"></span>{{ f.side }}
                      </span>
                    </td>
                    <td class="text-right mono">{{ +f.quantity | number: '1.0-6' }}</td>
                    <td class="text-right mono">{{ '$' + (+f.price | number: '1.2-4') }}</td>
                    <td><span class="pill ok"><span class="dot"></span>filled</span></td>
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

    @if (newOrderOpen()) {
      <div class="modal-overlay" (click)="newOrderOpen.set(false)">
        <div class="card order-modal" (click)="$event.stopPropagation()"
             role="dialog" aria-label="New order">
          <div class="card-hd"><h2 class="title">New order</h2></div>
          <div class="p-3.5 space-y-3">
            <label class="lbl block">Ticker
              <input class="input mono" type="text" [value]="ticker()"
                     (input)="onTickerInput($any($event.target).value)"
                     (change)="fetchPrice()"
                     data-test="new-order-ticker" autocomplete="off" />
            </label>

            <div class="field">
              <span class="lbl">Side</span>
              <div class="seg" role="radiogroup" aria-label="Side">
                <button type="button" class="opt buy" role="radio"
                        [attr.aria-checked]="side() === 'buy'"
                        [class.on]="side() === 'buy'"
                        (click)="side.set('buy')" data-test="new-order-side-buy">Buy</button>
                <button type="button" class="opt sell" role="radio"
                        [attr.aria-checked]="side() === 'sell'"
                        [class.on]="side() === 'sell'"
                        (click)="side.set('sell')" data-test="new-order-side-sell">Sell</button>
              </div>
            </div>

            <div class="field">
              <span class="lbl">Order type</span>
              <div class="seg" role="radiogroup" aria-label="Order type">
                @for (t of availableOrderTypes(); track t) {
                  <button type="button" class="opt" role="radio"
                          [attr.aria-checked]="orderType() === t"
                          [class.on]="orderType() === t"
                          (click)="setOrderType(t)"
                          [attr.data-test]="'new-order-type-' + t">{{ typeLabel(t) }}</button>
                }
              </div>
              <span class="help">{{ typeHelp() }}</span>
            </div>

            <label class="lbl block">Quantity
              <input class="input mono" type="number" step="0.0001" min="0"
                     [value]="qty()"
                     (input)="qty.set($any($event.target).value)"
                     data-test="new-order-qty" />
            </label>

            @if (orderType() === 'limit' || orderType() === 'stop_limit') {
              <label class="lbl block">Limit price
                <input class="input mono" type="number" step="0.01" min="0"
                       [value]="limitPrice()"
                       (input)="limitPrice.set($any($event.target).value)"
                       data-test="new-order-limit" />
              </label>
            }
            @if (orderType() === 'stop' || orderType() === 'stop_limit') {
              <label class="lbl block">Stop (trigger) price
                <input class="input mono" type="number" step="0.01" min="0"
                       [value]="stopPrice()"
                       (input)="stopPrice.set($any($event.target).value)"
                       data-test="new-order-stop" />
              </label>
            }
            @if (orderType() === 'trailing_stop') {
              <label class="lbl block">Trail by
                <select class="input mono" [value]="trailMode()"
                        (change)="trailMode.set($any($event.target).value)"
                        data-test="new-order-trail-mode">
                  <option value="price">Amount ($)</option>
                  <option value="percent">Percent (%)</option>
                </select>
              </label>
              <label class="lbl block">{{ trailMode() === 'percent' ? 'Trail percent' : 'Trail amount' }}
                <input class="input mono" type="number" step="0.01" min="0"
                       [value]="trailValue()"
                       (input)="trailValue.set($any($event.target).value)"
                       data-test="new-order-trail-value" />
              </label>
            }

            <div class="quote-row">
              @if (priceLoading()) {
                <span class="text-text-3">Fetching last price…</span>
              } @else if (livePrice() !== null) {
                <span>Last price
                  <strong class="mono">\${{ livePrice() | number: '1.2-2' }}</strong>
                </span>
                <span>Est. cost
                  <strong class="mono" [class.long]="side() === 'buy'"
                          [class.short]="side() === 'sell'">
                    {{ estCost() }}
                  </strong>
                </span>
              } @else {
                <span class="text-text-3">No live price for this ticker.</span>
              }
            </div>

            @if (lastError(); as e) {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5"><span class="dot"></span>{{ e }}</div>
            }
            <div class="text-right pt-1">
              <button class="btn" (click)="newOrderOpen.set(false)">Cancel</button>
              <button class="btn primary ml-2" (click)="submitOrder()"
                      [disabled]="!canSubmit() || placing()"
                      data-test="new-order-submit">
                {{ placing() ? 'Placing…' : 'Place ' + side() + ' order' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    }

    @if (protectOpen(); as pos) {
      <div class="modal-overlay" (click)="protectOpen.set(null)">
        <div class="card order-modal" (click)="$event.stopPropagation()"
             role="dialog" aria-label="Attach protection" data-test="protect-modal">
          <div class="card-hd"><h2 class="title">Attach protection · {{ pos.ticker }}</h2></div>
          <div class="p-3.5 space-y-3">
            <p class="text-[11.5px] text-text-3 m-0">
              Sized to the current position ({{ +pos.quantity | number: '1.0-6' }} shares).
              A stop-loss and a take-profit submit as a linked OCO pair — when one
              fills, the other is cancelled. Either alone submits as a single
              protective order.
            </p>
            <label class="lbl block">Stop-loss trigger
              <input class="input mono" type="number" step="0.01" min="0"
                     [value]="protectStop()"
                     (input)="protectStop.set($any($event.target).value)"
                     data-test="protect-stop" />
            </label>
            <label class="lbl flex items-center gap-2 text-[11.5px]">
              <input type="checkbox" [checked]="protectStopLimit()"
                     (change)="protectStopLimit.set($any($event.target).checked)"
                     data-test="protect-stop-limit-toggle" />
              Use a stop-limit (rests at a limit on trigger — may not fill if price gaps)
            </label>
            @if (protectStopLimit()) {
              <label class="lbl block">Stop-loss limit
                <input class="input mono" type="number" step="0.01" min="0"
                       [value]="protectStopLimitPrice()"
                       (input)="protectStopLimitPrice.set($any($event.target).value)"
                       data-test="protect-stop-limit-price" />
              </label>
            }
            <label class="lbl block">Take-profit limit
              <input class="input mono" type="number" step="0.01" min="0"
                     [value]="protectTakeProfit()"
                     (input)="protectTakeProfit.set($any($event.target).value)"
                     data-test="protect-take-profit" />
            </label>
            @if (lastError(); as e) {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5"><span class="dot"></span>{{ e }}</div>
            }
            <div class="text-right pt-1">
              <button class="btn" (click)="protectOpen.set(null)">Cancel</button>
              <button class="btn primary ml-2" (click)="submitProtect()"
                      [disabled]="!canProtect() || placing()"
                      data-test="protect-submit">
                {{ placing() ? 'Placing…' : 'Attach protection' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    .modal-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6);
      z-index: var(--z-modal); display:flex; align-items:center;
      justify-content:center; padding:16px;
    }
    .order-modal { width: 100%; max-width: 440px; }
    .card-hd .hint {
      margin-left: auto; font-size: var(--fs-11); color: var(--text-3);
      font-weight: 400; text-transform: none; letter-spacing: normal;
    }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .field .lbl { margin: 0; }
    .help { font-size: var(--fs-11); color: var(--text-3); }
    .quote-row {
      display: flex; justify-content: space-between; gap: 12px;
      font-size: var(--fs-12); color: var(--text-2);
      padding: 8px 10px; background: var(--surface-2);
      border-radius: var(--r-6); border: 1px solid var(--border);
    }
    .long { color: var(--acc-long-fg); }
    .short { color: var(--acc-short-fg); }
  `],
})
export class BrokerAccountOverviewPage implements OnInit {
  private readonly store = inject(BrokerStore);
  private readonly profiles = inject(TickerProfileStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly overview = this.store.overview;
  protected readonly loading = signal(true);
  protected readonly syncing = signal(false);
  protected readonly placing = signal(false);
  protected readonly account = computed(() => this.overview()?.account ?? null);

  // --- new-order modal state ---
  protected readonly newOrderOpen = signal(false);
  protected readonly ticker = signal('AAPL');
  protected readonly side = signal<'buy' | 'sell'>('buy');
  protected readonly orderType = signal<OrderType>('market');
  protected readonly qty = signal('5');
  protected readonly limitPrice = signal('');
  protected readonly stopPrice = signal('');
  protected readonly trailMode = signal<'price' | 'percent'>('price');
  protected readonly trailValue = signal('');
  protected readonly livePrice = signal<number | null>(null);
  protected readonly priceLoading = signal(false);

  // --- attach-protection modal state ---
  protected readonly protectOpen = signal<BrokerPosition | null>(null);
  protected readonly protectStop = signal('');
  protected readonly protectStopLimit = signal(false);
  protected readonly protectStopLimitPrice = signal('');
  protected readonly protectTakeProfit = signal('');

  protected readonly lastError = signal<string | null>(null);

  protected readonly working = computed(() =>
    this.store.orders().filter(
      (o) => o.status === 'submitted' || o.status === 'partial',
    ),
  );

  /** Persist the per-account default order quantity mode (whole|fractional). */
  setDefaultQuantityMode(mode: string): void {
    const id = this.account()?.id;
    if (!id || (mode !== 'whole' && mode !== 'fractional')) return;
    this.store.updateAccountSettings(id, { default_quantity_mode: mode }).subscribe({
      next: () => this.refresh(id),
      error: (err) =>
        this.lastError.set(err?.error?.detail ?? 'Could not update order defaults.'),
    });
  }

  ngOnInit(): void {
    const accountId = Number(this.route.snapshot.paramMap.get('id'));
    if (!accountId) return;
    // The capability registry gates the advanced order types + the Protect
    // action; load it once if it isn't already populated.
    if (!this.store.registry().length) {
      this.store.loadRegistry().subscribe();
    }
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
    this.store.acknowledgeDrift(id).subscribe({ next: () => this.refresh(id) });
  }

  // --- new-order modal ---

  openNewOrder(): void {
    this.lastError.set(null);
    this.livePrice.set(null);
    this.limitPrice.set('');
    this.stopPrice.set('');
    this.trailValue.set('');
    this.newOrderOpen.set(true);
    this.fetchPrice();
  }

  closePosition(pos: BrokerPosition): void {
    // Closing = opposite-side market order for the full position size. We
    // open the New Order modal pre-filled so the user can still adjust qty
    // or swap to a limit before sending — no separate /close endpoint, the
    // demo broker fills it like any other market order.
    const absQty = Math.abs(parseFloat(pos.quantity));
    if (!(absQty > 0)) return;
    this.lastError.set(null);
    this.ticker.set(pos.ticker);
    this.side.set(pos.is_short ? 'buy' : 'sell');
    this.orderType.set('market');
    this.qty.set(String(absQty));
    this.limitPrice.set('');
    this.stopPrice.set('');
    this.trailValue.set('');
    this.livePrice.set(null);
    this.newOrderOpen.set(true);
    this.fetchPrice();
  }

  onTickerInput(value: string): void {
    this.ticker.set((value || '').toUpperCase());
  }

  setOrderType(t: OrderType): void {
    this.orderType.set(t);
    // Pre-fill the trigger price from the live price the first time a
    // price-bearing type is chosen (item 3).
    const price = this.livePrice();
    if (price === null) return;
    if ((t === 'limit' || t === 'stop_limit') && !this.limitPrice()) {
      this.limitPrice.set(price.toFixed(2));
    }
    if ((t === 'stop' || t === 'stop_limit') && !this.stopPrice()) {
      this.stopPrice.set(price.toFixed(2));
    }
  }

  fetchPrice(): void {
    const sym = this.ticker().trim().toUpperCase();
    if (!sym) return;
    this.priceLoading.set(true);
    this.profiles.fetchProfile(sym).subscribe({
      next: (p) => {
        this.priceLoading.set(false);
        const price = p && p.price ? Number(p.price) : NaN;
        if (Number.isFinite(price) && price > 0) {
          this.livePrice.set(price);
          // Prefill the active trigger field if the user hasn't typed one.
          if (this.orderType() === 'limit' && !this.limitPrice()) {
            this.limitPrice.set(price.toFixed(2));
          }
          if (this.orderType() === 'stop' && !this.stopPrice()) {
            this.stopPrice.set(price.toFixed(2));
          }
        } else {
          this.livePrice.set(null);
        }
      },
      error: () => { this.priceLoading.set(false); this.livePrice.set(null); },
    });
  }

  typeLabel(t: OrderType): string {
    return {
      market: 'market', limit: 'limit', stop: 'stop',
      stop_limit: 'stop limit', trailing_stop: 'trailing',
    }[t];
  }

  availableOrderTypes(): OrderType[] {
    const acc = this.account();
    const cap = acc
      ? this.store.registry().find((c) => c.code === acc.broker)
      : undefined;
    const all: OrderType[] = ['market', 'limit', 'stop', 'stop_limit', 'trailing_stop'];
    if (!cap) return ['market', 'limit', 'stop'];
    return all.filter((t) => cap.supported_order_types.includes(t));
  }

  supportsBracket(): boolean {
    const acc = this.account();
    const cap = acc
      ? this.store.registry().find((c) => c.code === acc.broker)
      : undefined;
    return cap?.supports_bracket ?? false;
  }

  typeHelp(): string {
    switch (this.orderType()) {
      case 'market': return 'Fills immediately at the current market price.';
      case 'limit': return 'Rests until the price reaches your limit, then fills.';
      case 'stop': return 'Rests until the price crosses your stop, then fills at market.';
      case 'stop_limit':
        return 'Rests until the stop triggers, then becomes a limit at your limit price.';
      case 'trailing_stop':
        return 'Trails the high-water mark by your amount/percent, then fills at market.';
    }
  }

  // --- attach protection ---

  openProtect(pos: BrokerPosition): void {
    this.lastError.set(null);
    this.protectStop.set('');
    this.protectStopLimit.set(false);
    this.protectStopLimitPrice.set('');
    this.protectTakeProfit.set('');
    this.protectOpen.set(pos);
  }

  canProtect(): boolean {
    const hasStop = parseFloat(this.protectStop()) > 0;
    const hasTp = parseFloat(this.protectTakeProfit()) > 0;
    if (!hasStop && !hasTp) return false;
    if (this.protectStopLimit() && hasStop
        && !(parseFloat(this.protectStopLimitPrice()) > 0)) return false;
    return true;
  }

  submitProtect(): void {
    const pos = this.protectOpen();
    const id = this.account()?.id;
    if (!pos || !id || !this.canProtect()) return;
    const qty = Math.abs(parseFloat(pos.quantity));
    // Exits are the opposite side of the held position.
    const exitSide: 'buy' | 'sell' = pos.is_short ? 'buy' : 'sell';
    const hasStop = parseFloat(this.protectStop()) > 0;
    const hasTp = parseFloat(this.protectTakeProfit()) > 0;
    const stopLossLimit =
      this.protectStopLimit() && hasStop ? this.protectStopLimitPrice() : null;

    let body: Parameters<BrokerStore['createProtective']>[0];
    if (hasStop && hasTp) {
      // OCO pair.
      body = {
        broker_account: id, ticker: pos.ticker, side: exitSide,
        quantity: String(qty), order_class: 'oco', order_type: 'limit',
        take_profit_limit_price: this.protectTakeProfit(),
        stop_loss_stop_price: this.protectStop(),
        stop_loss_limit_price: stopLossLimit,
      };
    } else if (hasStop) {
      body = {
        broker_account: id, ticker: pos.ticker, side: exitSide,
        quantity: String(qty),
        order_type: stopLossLimit ? 'stop_limit' : 'stop',
        stop_price: this.protectStop(),
        limit_price: stopLossLimit,
      };
    } else {
      body = {
        broker_account: id, ticker: pos.ticker, side: exitSide,
        quantity: String(qty), order_type: 'limit',
        limit_price: this.protectTakeProfit(),
      };
    }

    this.lastError.set(null);
    this.placing.set(true);
    this.store.createProtective(body).subscribe({
      next: () => {
        this.placing.set(false);
        this.protectOpen.set(null);
        this.refresh(id);
      },
      error: (err) => {
        this.placing.set(false);
        this.lastError.set(
          err?.error?.detail
          ?? Object.values(err?.error ?? {})[0] as string
          ?? 'Could not attach protection.',
        );
      },
    });
  }

  private refPrice(): number | null {
    if (this.orderType() === 'limit' || this.orderType() === 'stop_limit') {
      const v = parseFloat(this.limitPrice());
      return Number.isFinite(v) ? v : this.livePrice();
    }
    if (this.orderType() === 'stop') {
      const v = parseFloat(this.stopPrice());
      return Number.isFinite(v) ? v : this.livePrice();
    }
    return this.livePrice();
  }

  estCost(): string {
    const q = parseFloat(this.qty() || '0');
    const p = this.refPrice();
    if (!Number.isFinite(q) || q <= 0 || p === null) return '—';
    const total = q * p;
    return '$' + total.toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

  canSubmit(): boolean {
    const q = parseFloat(this.qty() || '0');
    const type = this.orderType();
    if (!this.ticker().trim() || !(q > 0)) return false;
    if ((type === 'limit' || type === 'stop_limit')
        && !(parseFloat(this.limitPrice()) > 0)) return false;
    if ((type === 'stop' || type === 'stop_limit')
        && !(parseFloat(this.stopPrice()) > 0)) return false;
    if (type === 'trailing_stop' && !(parseFloat(this.trailValue()) > 0)) return false;
    return true;
  }

  submitOrder(): void {
    const id = this.account()?.id;
    if (!id || !this.canSubmit()) return;
    this.lastError.set(null);
    this.placing.set(true);
    const type = this.orderType();
    const isTrail = type === 'trailing_stop';
    this.store.createDraftOrder({
      broker_account: id,
      ticker: this.ticker().trim().toUpperCase(),
      side: this.side(),
      quantity: this.qty(),
      order_type: type,
      limit_price: type === 'limit' || type === 'stop_limit' ? this.limitPrice() : null,
      stop_price: type === 'stop' || type === 'stop_limit' ? this.stopPrice() : null,
      trail_price: isTrail && this.trailMode() === 'price' ? this.trailValue() : null,
      trail_percent: isTrail && this.trailMode() === 'percent' ? this.trailValue() : null,
    }).subscribe({
      next: () => {
        this.placing.set(false);
        this.newOrderOpen.set(false);
        this.refresh(id);
      },
      error: (err) => {
        this.placing.set(false);
        this.lastError.set(
          err?.error?.detail
          ?? Object.values(err?.error ?? {}).join('; ')
          ?? 'Failed to place order.',
        );
      },
    });
  }

  triggerLabel(ord: BrokerOrderRow): string {
    if (ord.order_type === 'limit' && ord.limit_price) {
      return '$' + Number(ord.limit_price).toFixed(2);
    }
    if (ord.order_type === 'stop' && ord.stop_price) {
      return '$' + Number(ord.stop_price).toFixed(2);
    }
    if (ord.order_type === 'stop_limit' && ord.stop_price) {
      const lim = ord.limit_price ? ' / $' + Number(ord.limit_price).toFixed(2) : '';
      return '$' + Number(ord.stop_price).toFixed(2) + lim;
    }
    if (ord.order_type === 'trailing_stop') {
      if (ord.trail_price) return '−$' + Number(ord.trail_price).toFixed(2);
      if (ord.trail_percent) return '−' + Number(ord.trail_percent).toFixed(2) + '%';
    }
    return '—';
  }

  onCancel(ord: BrokerOrderRow): void {
    // Cancelling a working order is low-stakes and reversible (just place
    // again), so it's a single click — no blocking confirm dialog.
    this.store.cancelOrder(ord.id).subscribe({ next: () => this.refresh() });
  }
}
