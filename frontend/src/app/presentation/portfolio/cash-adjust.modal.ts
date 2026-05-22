import { Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import { CashAdjustRequest } from '../../core/models/portfolio.model';

@Component({
  selector: 'hf-cash-adjust-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    @if (open) {
      <div class="modal-overlay" (click)="onCancel()">
        <div class="card cash-modal" (click)="$event.stopPropagation()">
          <div class="card-hd">
            <span class="title">Adjust manual cash</span>
            <div class="actions">
              <button class="icon-btn" (click)="onCancel()" aria-label="Close">
                <svg width="16" height="16"><use href="/icons.svg#i-x" /></svg>
              </button>
            </div>
          </div>
          <div class="cash-modal__body">
            <div class="field-row">
              <label class="lbl">Kind</label>
              <div class="seg">
                <button type="button" class="seg-btn"
                        [class.active]="kind === 'deposit'"
                        (click)="kind = 'deposit'">Deposit</button>
                <button type="button" class="seg-btn"
                        [class.active]="kind === 'withdrawal'"
                        (click)="kind = 'withdrawal'">Withdrawal</button>
              </div>
            </div>
            <div class="field-row">
              <label class="lbl">Amount</label>
              <input class="input mono" type="number" min="0" step="0.01"
                     [(ngModel)]="amount" style="width:160px" placeholder="0.00" />
              <span style="font-size:11.5px;color:var(--text-3)">USD</span>
            </div>
            <div class="field-row">
              <label class="lbl">Note</label>
              <input class="input" type="text" [(ngModel)]="note"
                     placeholder="optional context for the ledger" />
            </div>
            @if (error()) {
              <div class="pill err" style="height:auto;padding:6px 10px">
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
      </div>
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
