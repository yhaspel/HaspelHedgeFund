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
import { BrokerAccount, BrokerOrderRow } from '../../core/models/broker.model';

/** Minimal decision shape the ticket needs — normalized by the caller so the
 *  modal doesn't depend on the full run-model DecisionRow. */
export interface BrokerOrderTicketDecision {
  id: number;
  ticker: string;
  side: 'buy' | 'sell';
  targetQuantity: string;
}

/**
 * Step 1 of "Submit as broker order": pick the broker account + quantity +
 * order type, then create the draft. On success it emits `review` with the
 * created order so the parent can chain into the gated `hf-order-confirm-modal`
 * (which re-prices, runs the notional/live gate, and actually transmits to the
 * broker). Mirrors OrderConfirmModalComponent's structure and the hf-modal
 * shell used across the app.
 */
@Component({
  selector: 'hf-broker-order-ticket-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, ModalComponent],
  template: `
    <hf-modal titleId="broker-ticket-title" (closed)="onCancel()">
      <div class="card max-w-[460px]" (click)="$event.stopPropagation()">
        <div class="card-hd">
          <span class="title" id="broker-ticket-title">Submit as broker order</span>
        </div>
        <div class="p-3.5">
          <div class="text-xs text-text-2">
            <span class="font-medium mono">{{ decision.side }} {{ decision.ticker }}</span>
            — choose an account and quantity, then review &amp; submit.
          </div>

          <label class="lbl block mt-2.5">
            Broker account
            <select class="input mt-1" [(ngModel)]="accountId" name="account"
                    data-test="ticket-account">
              @for (a of accounts; track a.id) {
                <option [ngValue]="a.id">{{ a.label }} · {{ a.broker_display }} ({{ a.mode }})</option>
              }
            </select>
          </label>

          <label class="lbl block mt-2.5">
            Quantity (shares)
            <input class="input mt-1 mono" type="number" min="0" step="any"
                   [(ngModel)]="quantity" name="quantity" data-test="ticket-quantity" />
          </label>

          <label class="lbl block mt-2.5">
            Order type
            <select class="input mt-1" [(ngModel)]="orderType" name="orderType"
                    data-test="ticket-order-type">
              <option value="market">Market</option>
              <option value="limit">Limit</option>
            </select>
          </label>

          @if (orderType === 'limit') {
            <label class="lbl block mt-2.5">
              Limit price
              <input class="input mt-1 mono" type="number" min="0" step="any"
                     [(ngModel)]="limitPrice" name="limitPrice" data-test="ticket-limit-price" />
            </label>
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
  protected orderType: 'market' | 'limit' = 'market';
  protected limitPrice: number | null = null;

  ngOnInit(): void {
    this.accountId = this.accounts[0]?.id ?? null;
    const q = Math.abs(parseFloat(this.decision.targetQuantity));
    this.quantity = Number.isFinite(q) && q > 0 ? q : null;
  }

  canReview(): boolean {
    if (this.accountId == null) return false;
    if (this.quantity == null || this.quantity <= 0) return false;
    if (this.orderType === 'limit' && (this.limitPrice == null || this.limitPrice <= 0)) return false;
    return true;
  }

  onCancel(): void {
    this.closed.emit();
  }

  onReview(): void {
    if (!this.canReview()) return;
    const account = this.accounts.find((a) => a.id === this.accountId);
    if (!account) return;
    this.busy.set(true);
    this.error.set(null);
    this.store
      .createDraftOrder({
        broker_account: account.id,
        ticker: this.decision.ticker,
        side: this.decision.side,
        quantity: String(this.quantity),
        order_type: this.orderType,
        limit_price: this.orderType === 'limit' ? String(this.limitPrice) : null,
        decision: this.decision.id,
      })
      .subscribe({
        next: (order) => {
          this.busy.set(false);
          this.review.emit({ order, account });
        },
        error: (err) => {
          this.busy.set(false);
          this.error.set(err?.error?.detail ?? 'Could not create the draft order.');
        },
      });
  }
}
