import {
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ModalComponent } from '../shared/modal.component';
import { BrokerStore } from '../../abstraction/broker.store';
import {
  BrokerAccount,
  BrokerCapability,
  BrokerOrderRow,
  BrokerOrderType,
  CreateOrderRequest,
} from '../../core/models/broker.model';

/** Minimal decision shape the ticket needs — normalized by the caller so the
 *  modal doesn't depend on the full run-model DecisionRow. */
export interface BrokerOrderTicketDecision {
  id: number;
  ticker: string;
  side: 'buy' | 'sell';
  targetQuantity: string;
  /** Optional protective levels carried from the run's risk output. When a
   *  stop-loss is present and the broker supports brackets, the modal opens
   *  pre-set to a Bracket (or OTO if no target) with these absolute prices.
   *  The user can change everything. */
  stopLossPrice?: number | null;
  takeProfitPrice?: number | null;
}

/** The order shapes the ticket can build. market/limit/stop/stop_limit/
 *  trailing_stop are single (`order_class=simple`); bracket/oto carry the
 *  protective exits and submit as a group. */
type TicketKind =
  | 'market'
  | 'limit'
  | 'stop'
  | 'stop_limit'
  | 'trailing_stop'
  | 'bracket'
  | 'oto';

/**
 * Step 1 of "Submit as broker order": pick the broker account + quantity +
 * order type (incl. stop / stop-limit / trailing and bracket / OTO with
 * protective exits), then create the draft. On success it emits `review` with
 * the created order (the entry anchor for a group) so the parent can chain
 * into the gated `hf-order-confirm-modal`.
 */
@Component({
  selector: 'hf-broker-order-ticket-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, ModalComponent],
  template: `
    <hf-modal titleId="broker-ticket-title" (closed)="onCancel()">
      <div class="card max-w-[460px]" (click)="$event.stopPropagation()">
        <div class="card-hd">
          <h2 class="title" id="broker-ticket-title">Submit as broker order</h2>
        </div>
        <div class="p-3.5">
          <div class="text-xs text-text-2">
            <span class="font-medium mono">{{ decision.side }} {{ decision.ticker }}</span>
            — choose an account and quantity, then review &amp; submit.
          </div>

          <label class="lbl block mt-2.5">
            Broker account
            <select class="input mt-1" [(ngModel)]="accountId" name="account"
                    (ngModelChange)="onAccountChange()" data-test="ticket-account">
              @for (a of accounts; track a.id) {
                <option [ngValue]="a.id">{{ a.label }} · {{ a.broker_display }} ({{ a.mode }})</option>
              }
            </select>
          </label>

          <label class="lbl block mt-2.5">
            Quantity mode
            <select class="input mt-1" [(ngModel)]="quantityMode" name="quantityMode"
                    (ngModelChange)="setQuantityMode(quantityMode)"
                    [disabled]="isAdvanced()" data-test="ticket-quantity-mode">
              <option value="whole">Whole shares</option>
              <option value="fractional" [disabled]="!selectedSupportsFractional() || isAdvanced()">
                Fractional{{ selectedSupportsFractional() ? '' : ' (broker n/a)' }}
              </option>
            </select>
          </label>
          @if (isAdvanced()) {
            <p class="text-[11.5px] text-text-3 m-0 mt-1" data-test="ticket-whole-only">
              Bracket / OTO / trailing orders are whole-share only.
            </p>
          }

          <label class="lbl block mt-2.5">
            Quantity (shares)
            <input class="input mt-1 mono" type="number" min="0"
                   [step]="quantityMode === 'whole' ? '1' : 'any'"
                   [(ngModel)]="quantity" name="quantity" data-test="ticket-quantity" />
          </label>

          @if (quantityMode === 'whole' && showRoundedHint()) {
            <p class="text-[11.5px] text-text-3 m-0 mt-1" data-test="ticket-rounding-note">
              Whole-share order — rounded down from a {{ rawTargetLabel() }}-share target.
              {{ selectedSupportsFractional() && !isAdvanced() ? 'Switch to Fractional to keep the remainder.' : '' }}
            </p>
          }

          <label class="lbl block mt-2.5">
            Order type
            <select class="input mt-1" [(ngModel)]="kind" name="orderType"
                    (ngModelChange)="onKindChange()" data-test="ticket-order-type">
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              @if (supportsType('stop')) { <option value="stop">Stop</option> }
              @if (supportsType('stop_limit')) { <option value="stop_limit">Stop limit</option> }
              @if (supportsType('trailing_stop')) { <option value="trailing_stop">Trailing stop</option> }
              @if (supportsBracket()) {
                <option value="bracket">Bracket (entry + stop &amp; target)</option>
                <option value="oto">OTO (entry + one exit)</option>
              }
            </select>
          </label>

          <!-- Entry price (limit, or a limit entry inside a bracket/OTO) -->
          @if (needsLimitPrice()) {
            <label class="lbl block mt-2.5">
              {{ isGroup() ? 'Entry limit price' : 'Limit price' }}
              <input class="input mt-1 mono" type="number" min="0" step="any"
                     [(ngModel)]="limitPrice" name="limitPrice" data-test="ticket-limit-price" />
            </label>
          }
          @if (isGroup()) {
            <label class="lbl block mt-2.5">
              Entry type
              <select class="input mt-1" [(ngModel)]="entryType" name="entryType"
                      data-test="ticket-entry-type">
                <option value="market">Market</option>
                <option value="limit">Limit</option>
              </select>
            </label>
          }

          <!-- Standalone stop / stop-limit trigger -->
          @if (kind === 'stop' || kind === 'stop_limit') {
            <label class="lbl block mt-2.5">
              Stop (trigger) price
              <input class="input mt-1 mono" type="number" min="0" step="any"
                     [(ngModel)]="stopPrice" name="stopPrice" data-test="ticket-stop-price" />
            </label>
          }

          <!-- Trailing offset -->
          @if (kind === 'trailing_stop') {
            <label class="lbl block mt-2.5">
              Trail by
              <select class="input mt-1" [(ngModel)]="trailMode" name="trailMode"
                      data-test="ticket-trail-mode">
                <option value="price">Amount ($)</option>
                <option value="percent">Percent (%)</option>
              </select>
            </label>
            <label class="lbl block mt-2.5">
              {{ trailMode === 'percent' ? 'Trail percent' : 'Trail amount' }}
              <input class="input mt-1 mono" type="number" min="0" step="any"
                     [(ngModel)]="trailValue" name="trailValue" data-test="ticket-trail-value" />
            </label>
          }

          <!-- Protective exits (bracket / OTO) -->
          @if (isGroup()) {
            <div class="mt-3 pt-3 border-t border-border">
              <div class="lbl">Protective exits</div>
              @if (kind === 'oto') {
                <label class="lbl block mt-2">
                  Exit
                  <select class="input mt-1" [(ngModel)]="otoExit" name="otoExit"
                          data-test="ticket-oto-exit">
                    <option value="stop_loss">Stop-loss only</option>
                    <option value="take_profit">Take-profit only</option>
                  </select>
                </label>
              }
              @if (showTakeProfit()) {
                <label class="lbl block mt-2">
                  Take-profit limit
                  <input class="input mt-1 mono" type="number" min="0" step="any"
                         [(ngModel)]="takeProfit" name="takeProfit" data-test="ticket-take-profit" />
                </label>
              }
              @if (showStopLoss()) {
                <label class="lbl block mt-2">
                  Stop-loss trigger
                  <input class="input mt-1 mono" type="number" min="0" step="any"
                         [(ngModel)]="stopLossStop" name="stopLossStop" data-test="ticket-stop-loss" />
                </label>
                <label class="lbl flex items-center gap-2 mt-2 text-[11.5px]">
                  <input type="checkbox" [(ngModel)]="stopLimitExit" name="stopLimitExit"
                         data-test="ticket-stop-limit-exit" />
                  Use a stop-limit exit (rests at a limit on trigger)
                </label>
                @if (stopLimitExit) {
                  <label class="lbl block mt-2">
                    Stop-loss limit
                    <input class="input mt-1 mono" type="number" min="0" step="any"
                           [(ngModel)]="stopLossLimit" name="stopLossLimit"
                           data-test="ticket-stop-loss-limit" />
                  </label>
                  <p class="text-[11.5px] text-warn-fg m-0 mt-1">
                    A stop-limit can fail to fill if price gaps through the limit —
                    stop-market is the safer protective default.
                  </p>
                }
              }
            </div>
          }

          @if (error(); as e) {
            <div role="alert" class="pill err h-auto py-1.5 px-2.5 mt-2.5" data-test="ticket-error">
              <span class="dot"></span>{{ e }}
            </div>
          }

          <div class="text-right mt-3">
            <button class="btn" (click)="onCancel()" data-test="ticket-cancel">Cancel</button>
            <button class="btn primary ml-2" (click)="onReview()"
                    [disabled]="!canReview() || busy()" data-test="ticket-review">
              {{ busy() ? 'Creating…' : 'Review →' }}
            </button>
          </div>
        </div>
      </div>
    </hf-modal>
  `,
})
export class BrokerOrderTicketModalComponent implements OnInit {
  @Input({ required: true }) decision!: BrokerOrderTicketDecision;
  @Input() accounts: BrokerAccount[] = [];
  @Output() closed = new EventEmitter<void>();
  @Output() review = new EventEmitter<{ order: BrokerOrderRow; account: BrokerAccount }>();

  private readonly store = inject(BrokerStore);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected accountId: number | null = null;
  protected quantity: number | null = null;
  protected quantityMode: 'whole' | 'fractional' = 'whole';
  protected kind: TicketKind = 'market';
  protected entryType: 'market' | 'limit' = 'market';
  protected limitPrice: number | null = null;
  protected stopPrice: number | null = null;
  protected trailMode: 'price' | 'percent' = 'price';
  protected trailValue: number | null = null;
  // Protective exits (bracket / OTO)
  protected otoExit: 'stop_loss' | 'take_profit' = 'stop_loss';
  protected takeProfit: number | null = null;
  protected stopLossStop: number | null = null;
  protected stopLimitExit = false;
  protected stopLossLimit: number | null = null;

  /** Absolute, unrounded target quantity from the decision (for the residual hint). */
  private rawTarget = 0;
  /** Guards the one-shot bracket pre-fill from the run's risk output. */
  private protectiveApplied = false;

  ngOnInit(): void {
    const account = this.accounts[0] ?? null;
    this.accountId = account?.id ?? null;
    this.quantityMode = account?.default_quantity_mode === 'fractional'
      ? 'fractional'
      : 'whole';
    const q = Math.abs(parseFloat(this.decision.targetQuantity));
    this.rawTarget = Number.isFinite(q) && q > 0 ? q : 0;
    this.applyQuantityForMode();
    const ready = () => {
      this.onAccountChange();
      this.applyProtectiveDefault();
    };
    if (!this.store.registry().length) {
      this.store.loadRegistry().subscribe(ready);
    } else {
      ready();
    }
  }

  /** If the originating decision carries a recommended stop-loss (and the
   *  selected broker supports brackets), open pre-set to a Bracket — or an
   *  OTO when there's no target — with the run's levels filled in. Once only,
   *  so it never clobbers the user's edits. */
  private applyProtectiveDefault(): void {
    if (this.protectiveApplied) return;
    const stop = this.decision.stopLossPrice;
    if (stop == null || !(stop > 0) || !this.supportsBracket()) return;
    this.protectiveApplied = true;
    this.entryType = 'market';
    this.stopLossStop = stop;
    const tp = this.decision.takeProfitPrice;
    if (tp != null && tp > 0) {
      this.kind = 'bracket';
      this.takeProfit = tp;
    } else {
      this.kind = 'oto';
      this.otoExit = 'stop_loss';
    }
    this.onKindChange();  // forces whole-share for the advanced type
  }

  private applyQuantityForMode(): void {
    if (this.rawTarget <= 0) {
      this.quantity = null;
      return;
    }
    this.quantity = this.quantityMode === 'whole'
      ? Math.floor(this.rawTarget)
      : this.rawTarget;
  }

  private capForSelected(): BrokerCapability | undefined {
    const account = this.accounts.find((a) => a.id === this.accountId);
    if (!account) return undefined;
    return this.store.registry().find((c) => c.code === account.broker);
  }

  selectedSupportsFractional(): boolean {
    return this.capForSelected()?.supports_fractional ?? false;
  }

  supportsType(t: string): boolean {
    return (this.capForSelected()?.supported_order_types ?? []).includes(t);
  }

  supportsBracket(): boolean {
    return this.capForSelected()?.supports_bracket ?? false;
  }

  isGroup(): boolean {
    return this.kind === 'bracket' || this.kind === 'oto';
  }

  isAdvanced(): boolean {
    return this.isGroup() || this.kind === 'trailing_stop';
  }

  /** A limit price is needed for a standalone limit, or a limit entry in a group. */
  needsLimitPrice(): boolean {
    if (this.isGroup()) return this.entryType === 'limit';
    return this.kind === 'limit';
  }

  showTakeProfit(): boolean {
    if (this.kind === 'bracket') return true;
    return this.kind === 'oto' && this.otoExit === 'take_profit';
  }

  showStopLoss(): boolean {
    if (this.kind === 'bracket') return true;
    return this.kind === 'oto' && this.otoExit === 'stop_loss';
  }

  showRoundedHint(): boolean {
    return this.rawTarget > 0 && this.rawTarget - Math.floor(this.rawTarget) > 1e-9;
  }

  rawTargetLabel(): string {
    return this.rawTarget.toFixed(6).replace(/\.?0+$/, '');
  }

  setQuantityMode(mode: 'whole' | 'fractional'): void {
    this.quantityMode = mode;
    if (mode === 'whole' && this.quantity != null) {
      this.quantity = Math.floor(Math.abs(this.quantity));
    }
  }

  onKindChange(): void {
    // Advanced orders are whole-share only.
    if (this.isAdvanced() && this.quantityMode !== 'whole') {
      this.setQuantityMode('whole');
    }
  }

  onAccountChange(): void {
    if (this.quantityMode === 'fractional' && !this.selectedSupportsFractional()) {
      this.setQuantityMode('whole');
    }
    // A broker that lost bracket support / a removed type resets to market.
    if (this.isGroup() && !this.supportsBracket()) this.kind = 'market';
    if ((this.kind === 'stop' || this.kind === 'stop_limit' || this.kind === 'trailing_stop')
        && !this.supportsType(this.kind)) {
      this.kind = 'market';
    }
  }

  canReview(): boolean {
    if (this.accountId == null) return false;
    if (this.quantity == null || this.quantity <= 0) return false;
    if (this.needsLimitPrice() && !(this.limitPrice && this.limitPrice > 0)) return false;
    if ((this.kind === 'stop' || this.kind === 'stop_limit')
        && !(this.stopPrice && this.stopPrice > 0)) return false;
    if (this.kind === 'stop_limit' && !(this.limitPrice && this.limitPrice > 0)) return false;
    if (this.kind === 'trailing_stop' && !(this.trailValue && this.trailValue > 0)) return false;
    if (this.showTakeProfit() && !(this.takeProfit && this.takeProfit > 0)) return false;
    if (this.showStopLoss() && !(this.stopLossStop && this.stopLossStop > 0)) return false;
    if (this.showStopLoss() && this.stopLimitExit
        && !(this.stopLossLimit && this.stopLossLimit > 0)) return false;
    return true;
  }

  onCancel(): void {
    this.closed.emit();
  }

  private buildRequest(accountId: number): CreateOrderRequest {
    const base: CreateOrderRequest = {
      broker_account: accountId,
      ticker: this.decision.ticker,
      side: this.decision.side,
      quantity: String(this.quantity),
      quantity_mode: this.quantityMode,
      decision: this.decision.id,
    };
    if (this.isGroup()) {
      return {
        ...base,
        order_class: this.kind as 'bracket' | 'oto',
        order_type: this.entryType,
        limit_price: this.entryType === 'limit' ? String(this.limitPrice) : null,
        take_profit_limit_price: this.showTakeProfit() ? String(this.takeProfit) : null,
        stop_loss_stop_price: this.showStopLoss() ? String(this.stopLossStop) : null,
        stop_loss_limit_price:
          this.showStopLoss() && this.stopLimitExit ? String(this.stopLossLimit) : null,
      };
    }
    return {
      ...base,
      order_type: this.kind as BrokerOrderType,
      limit_price:
        this.kind === 'limit' || this.kind === 'stop_limit' ? String(this.limitPrice) : null,
      stop_price:
        this.kind === 'stop' || this.kind === 'stop_limit' ? String(this.stopPrice) : null,
      trail_price:
        this.kind === 'trailing_stop' && this.trailMode === 'price'
          ? String(this.trailValue) : null,
      trail_percent:
        this.kind === 'trailing_stop' && this.trailMode === 'percent'
          ? String(this.trailValue) : null,
    };
  }

  onReview(): void {
    if (!this.canReview()) return;
    const account = this.accounts.find((a) => a.id === this.accountId);
    if (!account) return;
    this.busy.set(true);
    this.error.set(null);
    this.store.createDraftOrder(this.buildRequest(account.id)).subscribe({
      next: (order) => {
        this.busy.set(false);
        this.review.emit({ order, account });
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(
          err?.error?.detail
          ?? Object.values(err?.error ?? {})[0] as string
          ?? 'Could not create the draft order.',
        );
      },
    });
  }
}
