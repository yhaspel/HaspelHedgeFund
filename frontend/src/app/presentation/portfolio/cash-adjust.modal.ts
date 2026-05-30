import { Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import { CashAdjustRequest } from '../../core/models/portfolio.model';
import { ModalComponent } from '../shared/modal.component';

@Component({
  selector: 'hf-cash-adjust-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, ModalComponent],
  template: `
    @if (open) {
      <hf-modal titleId="cash-adjust-title" (closed)="onCancel()">
        <div class="card cash-modal" (click)="$event.stopPropagation()">
          <div class="card-hd">
            <h2 class="title" id="cash-adjust-title">Adjust manual cash</h2>
            <div class="actions">
              <button class="icon-btn" (click)="onCancel()" aria-label="Close">
                <svg width="16" height="16" aria-hidden="true"><use href="/icons.svg#i-x" /></svg>
              </button>
            </div>
          </div>
          <div class="cash-modal__body">
            <div class="field-row">
              <label class="lbl" for="cash-kind">Kind</label>
              <div class="seg" id="cash-kind" role="radiogroup" aria-label="Adjustment kind">
                <button type="button" class="seg-btn" role="radio"
                        [attr.aria-checked]="kind === 'deposit'"
                        [class.active]="kind === 'deposit'"
                        (click)="kind = 'deposit'">Deposit</button>
                <button type="button" class="seg-btn" role="radio"
                        [attr.aria-checked]="kind === 'withdrawal'"
                        [class.active]="kind === 'withdrawal'"
                        (click)="kind = 'withdrawal'">Withdrawal</button>
              </div>
            </div>
            <div class="field-row">
              <label class="lbl" for="cash-amount">Amount</label>
              <input id="cash-amount" class="input mono w-[160px]" type="number" min="0" step="0.01"
                     [(ngModel)]="amount" placeholder="0.00" name="amount" />
              <span class="text-[11.5px] text-text-3">USD</span>
            </div>
            <div class="field-row">
              <label class="lbl" for="cash-note">Note</label>
              <input id="cash-note" class="input" type="text" [(ngModel)]="note" name="note"
                     placeholder="optional context for the ledger" />
            </div>
            @if (error()) {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5">
                <span class="dot"></span>{{ error() }}
              </div>
            }
          </div>
          <div class="cash-modal__foot">
            <button class="btn" (click)="onCancel()">Cancel</button>
            <button class="btn primary" (click)="onConfirm()"
                    [disabled]="!canConfirm()">
              {{ store.busy() ? 'Saving…' : (kind === 'deposit' ? 'Deposit' : 'Withdraw') }}
            </button>
          </div>
        </div>
      </hf-modal>
    }
  `,
  styles: [
    `
      .modal-overlay {
        position: fixed; inset: 0; background: rgba(0,0,0,0.6);
        z-index: var(--z-modal); display: flex; align-items: center;
        justify-content: center; padding: 16px;
      }
      .cash-modal {
        max-width: 480px; width: 100%;
      }
      .cash-modal__body {
        padding: 16px; display: flex; flex-direction: column; gap: 12px;
        border-bottom: 1px solid var(--border);
      }
      .cash-modal__foot {
        display: flex; justify-content: flex-end; gap: 8px;
        padding: 12px 16px;
      }
      .field-row { display: flex; align-items: center; gap: 10px; }
      .field-row .lbl { flex: 0 0 100px; font-size: 12px; color: var(--text-2); }
      .seg {
        display: inline-flex; gap: 0; border: 1px solid var(--border);
        border-radius: var(--r-6); overflow: hidden;
      }
      .seg-btn {
        background: transparent; border: 0; padding: 6px 12px; font-size: 12px;
        color: var(--text-2); cursor: pointer;
      }
      .seg-btn.active { background: var(--acc-info-soft); color: var(--text); }
    `,
  ],
})
export class CashAdjustModalComponent {
  readonly store = inject(PortfolioStore);

  @Input() open = false;
  @Output() closed = new EventEmitter<{ saved: boolean }>();

  kind: 'deposit' | 'withdrawal' = 'deposit';
  amount: number | null = null;
  note = '';
  error = signal<string | null>(null);

  canConfirm(): boolean {
    return !this.store.busy() && !!this.amount && this.amount > 0;
  }

  onConfirm(): void {
    if (!this.canConfirm()) return;
    this.error.set(null);
    const body: CashAdjustRequest = {
      kind: this.kind,
      amount: String(this.amount || 0),
      note: this.note,
    };
    this.store.adjustCash(body).subscribe({
      next: () => {
        this.amount = null;
        this.note = '';
        this.closed.emit({ saved: true });
      },
      error: (err) => this.error.set(err?.error?.detail || 'Failed to save.'),
    });
  }

  onCancel(): void {
    this.amount = null;
    this.note = '';
    this.error.set(null);
    this.closed.emit({ saved: false });
  }
}
