import {
  Component,
  EventEmitter,
  Input,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ModalComponent } from '../shared/modal.component';
import { TickerComponent } from '../shared/ticker.component';
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerAccount, BrokerOrderRow } from '../../core/models/broker.model';

const NOTIONAL_THRESHOLD = 1000;

@Component({
  selector: 'hf-order-confirm-modal',
  standalone: true,
  imports: [CommonModule, DecimalPipe, FormsModule, ModalComponent, TickerComponent],
  template: `
    <hf-modal titleId="order-confirm-title" (closed)="onCancel()">
      <div class="card max-w-[480px]" (click)="$event.stopPropagation()">
        <div class="card-hd">
          <h2 class="title" id="order-confirm-title">Confirm and submit order</h2>
        </div>
        <div class="p-3.5">
          @if (order) {
            <div class="text-xs text-text-2">
              <span class="font-medium">{{ order.side }} {{ order.quantity }} <hf-ticker [ticker]="order.ticker"></hf-ticker></span>
              · {{ order.order_type }}
              @if (order.limit_price) {
                · limit {{ '$' + (+order.limit_price | number:'1.2-4') }}
              }
              · est. notional <span class="mono">{{ '$' + (+order.notional_estimate | number:'1.2-2') }}</span>
            </div>

            @if (isGroupEntry()) {
              <div class="mt-2.5 text-xs text-text-2 border-t border-border pt-2.5"
                   data-test="bracket-summary">
                <div class="font-medium mb-1">Protective exits</div>
                @if (stopLeg(); as sl) {
                  <div>
                    Stop-loss {{ sl.order_type === 'stop_limit' ? 'limit' : 'trigger' }}
                    <span class="mono">{{ '$' + (+(sl.stop_price || 0) | number:'1.2-2') }}</span>
                    @if (maxLoss() !== null) {
                      · max loss
                      <span class="mono" data-test="max-loss">{{ '$' + (maxLoss()! | number:'1.2-2') }}</span>
                    }
                  </div>
                }
                @if (takeProfitLeg(); as tp) {
                  <div>
                    Take-profit limit
                    <span class="mono">{{ '$' + (+(tp.limit_price || 0) | number:'1.2-2') }}</span>
                    @if (targetGain() !== null) {
                      · target gain
                      <span class="mono" data-test="target-gain">{{ '$' + (targetGain()! | number:'1.2-2') }}</span>
                    }
                  </div>
                }
                @if (isMarketEntry()) {
                  <p class="text-[11.5px] text-text-3 m-0 mt-1" data-test="market-entry-note">
                    Max loss / target gain are estimated at the live quote on submit
                    (market entry).
                  </p>
                }
                @if (hasStopLimitExit()) {
                  <p class="text-[11.5px] text-warn-fg m-0 mt-1" data-test="stop-limit-caveat">
                    A stop-limit exit can fail to fill if price gaps through the limit —
                    it is not guaranteed protection. Stop-market is the safer default.
                  </p>
                }
              </div>
            }

            @if (account?.mode === 'live') {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5 mt-2.5">
                <span class="dot"></span>
                <strong>LIVE account.</strong> This will submit a real-money
                order. You must type a phrase containing the word LIVE and
                have accepted the current trading disclaimer.
              </div>
            }

            @if (typedNeeded()) {
              <label class="lbl block mt-2.5" data-test="typed-confirmation-row">
                Type the ticker (<strong class="mono">{{ order.ticker }}</strong>)
                to confirm — required for orders over {{ '$' + threshold }}.
                <input class="input mt-1 mono" type="text" [(ngModel)]="typedTicker"
                       (input)="onTypedInput()"
                       data-test="typed-confirmation-input" name="typed" />
              </label>
            }

            @if (account?.mode === 'live') {
              <label class="lbl block mt-2.5">
                Type a phrase containing LIVE
                <input class="input mt-1" type="text" [(ngModel)]="livePhrase"
                       data-test="live-confirmation-input" name="livePhrase" />
              </label>
            }

            @if (error(); as e) {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5 mt-2.5"
                   data-test="confirm-error">
                <span class="dot"></span>{{ e }}
              </div>
            }

            <div class="text-right mt-3">
              <button class="btn" (click)="onCancel()" data-test="confirm-cancel">Cancel</button>
              <button class="btn primary ml-2" (click)="onConfirm()"
                      [disabled]="!canConfirm() || busy()"
                      data-test="confirm-submit">
                {{ busy() ? 'Submitting…' : 'Confirm and submit' }}
              </button>
            </div>
          }
        </div>
      </div>
    </hf-modal>
  `,
})
export class OrderConfirmModalComponent {
  @Input() order: BrokerOrderRow | null = null;
  @Input() account: BrokerAccount | null = null;
  @Output() closed = new EventEmitter<void>();
  @Output() confirmed = new EventEmitter<BrokerOrderRow>();

  private readonly store = inject(BrokerStore);
  protected readonly threshold = NOTIONAL_THRESHOLD;
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected typedTicker = '';
  protected livePhrase = '';

  /** Set when the backend's notional gate demands typed confirmation for an
   *  order whose client-side estimate was under the threshold. Market orders
   *  carry no limit price, so `notional_estimate` is "0.00" until fill while
   *  the gate re-prices them and can cross the $1,000 threshold — without this
   *  the rejection message would show with no input to satisfy it. */
  protected readonly typedRevealed = signal(false);

  protected readonly typedRequired = computed(() => {
    if (!this.order) return false;
    return parseFloat(this.order.notional_estimate || '0') > NOTIONAL_THRESHOLD;
  });

  /** Show and enforce the typed-ticker input when either the client-side
   *  estimate crosses the threshold or the backend gate has demanded it. */
  protected readonly typedNeeded = computed(
    () => this.typedRequired() || this.typedRevealed(),
  );

  // --- Bracket / OTO entry summary (client-side; server is authoritative) ---

  isGroupEntry(): boolean {
    return this.order?.leg_role === 'entry';
  }

  isMarketEntry(): boolean {
    return this.isGroupEntry() && this.order?.order_type === 'market';
  }

  stopLeg(): BrokerOrderRow | null {
    return (this.order?.legs ?? []).find((l) => l.leg_role === 'stop_loss') ?? null;
  }

  takeProfitLeg(): BrokerOrderRow | null {
    return (this.order?.legs ?? []).find((l) => l.leg_role === 'take_profit') ?? null;
  }

  hasStopLimitExit(): boolean {
    return this.stopLeg()?.order_type === 'stop_limit';
  }

  /** The entry reference: a limit entry prices exactly; a market entry has no
   *  client-side price (the server estimates from a live quote on submit). */
  private entryRef(): number | null {
    if (!this.order || this.order.limit_price == null) return null;
    const ref = Number(this.order.limit_price);
    return Number.isFinite(ref) ? ref : null;
  }

  maxLoss(): number | null {
    const sl = this.stopLeg();
    const ref = this.entryRef();
    if (!this.isGroupEntry() || !this.order || ref === null || !sl?.stop_price) return null;
    const qty = Number(this.order.quantity);
    const stop = Number(sl.stop_price);
    const diff = this.order.side === 'buy' ? ref - stop : stop - ref;
    return diff * qty;
  }

  targetGain(): number | null {
    const tp = this.takeProfitLeg();
    const ref = this.entryRef();
    if (!this.isGroupEntry() || !this.order || ref === null || !tp?.limit_price) return null;
    const qty = Number(this.order.quantity);
    const target = Number(tp.limit_price);
    const diff = this.order.side === 'buy' ? target - ref : ref - target;
    return diff * qty;
  }

  canConfirm(): boolean {
    if (!this.order) return false;
    if (this.typedNeeded()) {
      if (this.typedTicker.trim().toUpperCase() !== this.order.ticker.toUpperCase()) return false;
    }
    if (this.account?.mode === 'live') {
      if (!this.livePhrase.toUpperCase().includes('LIVE')) return false;
    }
    return true;
  }

  onTypedInput(): void {
    this.error.set(null);
  }

  onCancel(): void {
    this.closed.emit();
  }

  onConfirm(): void {
    if (!this.order || !this.canConfirm()) return;
    this.busy.set(true);
    this.error.set(null);
    this.store
      .confirmOrder(this.order.id, {
        typed_confirmation: this.typedNeeded() ? this.typedTicker : undefined,
        live_confirmation: this.account?.mode === 'live' ? this.livePhrase : undefined,
        confirmation_method: 'manual_ui',
      })
      .subscribe({
        next: (row) => {
          this.busy.set(false);
          this.confirmed.emit(row);
        },
        error: (err) => {
          this.busy.set(false);
          const detail: string = err?.error?.detail ?? 'Submission failed.';
          // The gate re-prices the order server-side and may require typed
          // confirmation even when our estimate didn't (market orders). Reveal
          // the ticker input so the user can satisfy the gate and resubmit.
          const needsTyped =
            err?.error?.code === 'typed_confirmation_required' ||
            /typ\w*\s+the\s+ticker/i.test(detail);
          if (needsTyped) {
            this.typedRevealed.set(true);
          }
          this.error.set(detail);
        },
      });
  }
}
