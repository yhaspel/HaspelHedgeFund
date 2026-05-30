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
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerAccount, BrokerOrderRow } from '../../core/models/broker.model';

const NOTIONAL_THRESHOLD = 1000;

@Component({
  selector: 'hf-order-confirm-modal',
  standalone: true,
  imports: [CommonModule, DecimalPipe, FormsModule, ModalComponent],
  template: `
    <hf-modal titleId="order-confirm-title" (closed)="onCancel()">
      <div class="card" style="max-width:480px" (click)="$event.stopPropagation()">
        <div class="card-hd">
          <h2 class="title" id="order-confirm-title">Confirm and submit order</h2>
        </div>
        <div class="p-3.5">
          @if (order) {
            <div class="text-xs text-text-2">
              <span class="font-medium">{{ order.side }} {{ order.quantity }} {{ order.ticker }}</span>
              · {{ order.order_type }}
              @if (order.limit_price) {
                · limit {{ '$' + (+order.limit_price | number:'1.2-4') }}
              }
              · est. notional <span class="mono">{{ '$' + (+order.notional_estimate | number:'1.2-2') }}</span>
            </div>
            @if (account?.mode === 'live') {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5 mt-2.5">
                <span class="dot"></span>
                <strong>LIVE account.</strong> This will submit a real-money
                order. You must type a phrase containing the word LIVE and
                have accepted the current trading disclaimer.
              </div>
            }

            @if (typedRequired()) {
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

  protected readonly typedRequired = computed(() => {
    if (!this.order) return false;
    return parseFloat(this.order.notional_estimate || '0') > NOTIONAL_THRESHOLD;
  });

  canConfirm(): boolean {
    if (!this.order) return false;
    if (this.typedRequired()) {
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
        typed_confirmation: this.typedRequired() ? this.typedTicker : undefined,
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
          this.error.set(err?.error?.detail ?? 'Submission failed.');
        },
      });
  }
}
