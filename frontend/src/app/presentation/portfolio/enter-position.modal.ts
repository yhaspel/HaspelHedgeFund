import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import {
  OpenPositionRequest,
  PositionSide,
  PositionSuggestion,
  QuantityMode,
} from '../../core/models/portfolio.model';
import { ModalComponent } from '../shared/modal.component';

interface PrefillInput {
  runId?: number;
  decisionId?: number;
  ticker?: string;
  side?: PositionSide;
}

@Component({
  selector: 'hf-enter-position-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, DecimalPipe, DatePipe, ModalComponent],
  template: `
    @if (open) {
      <hf-modal [titleId]="'enter-position-title'" (closed)="onCancel()">
        <div class="card entry-modal" (click)="$event.stopPropagation()">

          <div class="entry-modal__head">
            <div class="card-hd">
              <span class="title" id="enter-position-title">{{ titleText() }}</span>
              <div class="actions">
                <button class="icon-btn" (click)="onCancel()" aria-label="Close">
                  <svg width="16" height="16" aria-hidden="true"><use href="/icons.svg#i-x" /></svg>
                </button>
              </div>
            </div>
          </div>

          <div class="entry-modal__body scroll-area">
            @if (store.suggestionLoading()) {
              <p style="margin:0;color:var(--text-3)">Loading suggestion…</p>
            }

            <div class="field-row">
              <label class="lbl">Ticker</label>
              <input class="input mono" type="text" [(ngModel)]="ticker"
                     [disabled]="!!prefill?.ticker" placeholder="AAPL"
                     (input)="ticker = ticker.toUpperCase()" />
            </div>

            <div class="field-row">
              <label class="lbl">Direction</label>
              <div class="seg">
                <button type="button" class="seg-btn" [class.active]="side === 'long'"
                        (click)="side = 'long'">Long</button>
                <button type="button" class="seg-btn" [class.active]="side === 'short'"
                        (click)="side = 'short'">Short</button>
              </div>
            </div>

            <div class="field-row">
              <label class="lbl">Quantity mode</label>
              <div class="seg">
                <button type="button" class="seg-btn" [class.active]="quantityMode === 'whole'"
                        (click)="setQuantityMode('whole')">Whole shares</button>
                <button type="button" class="seg-btn" [class.active]="quantityMode === 'fractional'"
                        (click)="setQuantityMode('fractional')">Fractional</button>
              </div>
            </div>

            <div class="field-row">
              <label class="lbl">Size</label>
              <div style="display:flex;gap:8px;align-items:center;flex:1">
                <input class="input mono" type="number" [min]="0" step="{{ quantityMode === 'whole' ? '1' : '0.000001' }}"
                       [(ngModel)]="quantity" style="width:120px" />
                <span style="font-size:11.5px;color:var(--text-3)">shares</span>
                @if (currentPrice() > 0) {
                  <span style="font-size:11.5px;color:var(--text-3)">
                    ≈ $\{{ notionalEstimate() | number: '1.2-2' }} ·
                    {{ weightEstimate() | number: '1.2-2' }}% of book
                  </span>
                }
              </div>
            </div>

            <div class="field-row">
              <label class="lbl">Entry price</label>
              <input class="input mono" type="number" min="0" step="0.01"
                     [(ngModel)]="entryPrice" style="width:160px" />
              @if (priceAsOf()) {
                <span style="font-size:11.5px;color:var(--text-3);margin-left:8px">
                  latest close {{ priceAsOf() | date: 'mediumDate' }}
                </span>
              }
            </div>

            <div class="field-row">
              <label class="lbl">Note</label>
              <textarea class="input" rows="2" [(ngModel)]="note"
                        placeholder="optional context for the ledger"></textarea>
            </div>

            @if (suggestion(); as s) {
              <details class="why-card" [open]="true">
                <summary>
                  <span class="eyebrow">Why this size?</span>
                  <span style="margin-left:8px;color:var(--text-2);font-size:11.5px">
                    {{ s.action_label }} · suggested {{ +s.suggested_weight_pct | number: '1.2-2' }}%
                    ({{ formatQty(s.suggested_quantity) }} sh @ \${{ +s.current_price | number: '1.2-4' }})
                  </span>
                </summary>
                <ul class="factor-list">
                  @for (f of s.factors; track f.key) {
                    <li>
                      <span class="factor-key">{{ f.label }}</span>
                      <span class="factor-eff mono">{{ f.effect }}</span>
                      <span class="factor-detail">{{ f.detail }}</span>
                    </li>
                  }
                </ul>
                @if (+s.rounding_residual_usd > 0.5) {
                  <p style="font-size:11.5px;color:var(--text-3);margin:6px 0 0">
                    Whole-share rounding residual:
                    <span class="mono">\${{ +s.rounding_residual_usd | number: '1.2-2' }}</span>
                    — switch to fractional shares to capture the full notional.
                  </p>
                }
              </details>
            }

            @for (w of warnings(); track w) {
              <div class="pill warn" style="height:auto;padding:6px 10px;width:fit-content">
                <span class="dot"></span>{{ w }}
              </div>
            }

            @if (error()) {
              <div class="pill err" style="height:auto;padding:6px 10px">
                <span class="dot"></span>{{ error() }}
              </div>
            }
          </div>

          <div class="entry-modal__foot">
            <button class="btn" (click)="onCancel()">Cancel</button>
            <button class="btn primary" (click)="onConfirm()"
                    [disabled]="!canConfirm()">
              {{ store.busy() ? 'Saving…' : confirmLabel() }}
            </button>
          </div>
        </div>
      </hf-modal>
    }
  `,
  styles: [
    `
      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.6);
        z-index: var(--z-modal);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
      }
      .entry-modal {
        max-width: 580px;
        width: 100%;
        max-height: 90vh;
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .entry-modal__head { flex: 0 0 auto; border-bottom: 1px solid var(--border); }
      .entry-modal__body {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .entry-modal__foot {
        flex: 0 0 auto;
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        padding: 12px 16px;
        border-top: 1px solid var(--border);
      }
      .field-row {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .field-row .lbl {
        flex: 0 0 110px;
        font-size: 12px;
        color: var(--text-2);
      }
      .seg {
        display: inline-flex;
        gap: 0;
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        overflow: hidden;
      }
      .seg-btn {
        background: transparent;
        border: 0;
        padding: 6px 12px;
        font-size: 12px;
        color: var(--text-2);
        cursor: pointer;
      }
      .seg-btn.active {
        background: var(--acc-info-soft);
        color: var(--text);
      }
      .why-card {
        border: 1px solid var(--border);
        border-radius: var(--r-8);
        padding: 10px 12px;
        background: var(--surface-2, var(--n-50));
      }
      .why-card summary {
        cursor: pointer;
        list-style: none;
      }
      .why-card summary::-webkit-details-marker { display: none; }
      .factor-list {
        list-style: none;
        margin: 8px 0 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .factor-list li {
        display: grid;
        grid-template-columns: 160px 110px 1fr;
        gap: 8px;
        font-size: 12px;
        line-height: 1.4;
        align-items: baseline;
      }
      .factor-key { color: var(--text); font-weight: 500; }
      .factor-eff { color: var(--acc-info-fg); }
      .factor-detail { color: var(--text-3); }
    `,
  ],
})
export class EnterPositionModalComponent implements OnChanges {
  readonly store = inject(PortfolioStore);

  @Input() open = false;
  @Input() prefill: PrefillInput | null = null;
  @Output() closed = new EventEmitter<{ saved: boolean }>();

  ticker = '';
  side: PositionSide = 'long';
  quantity: number | null = null;
  entryPrice: number | null = null;
  quantityMode: QuantityMode = 'whole';
  note = '';
  error = signal<string | null>(null);

  // Pull a typed view of the suggestion signal for templating.
  suggestion(): PositionSuggestion | null { return this.store.suggestion(); }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['open'] && this.open) {
      this.reset();
      this.prefillFromInput();
    }
  }

  private reset(): void {
    this.ticker = '';
    this.side = 'long';
    this.quantity = null;
    this.entryPrice = null;
    this.note = '';
    this.error.set(null);
    this.quantityMode = 'whole';
    this.store.resetSuggestion();
  }

  private prefillFromInput(): void {
    if (!this.prefill) return;
    if (this.prefill.ticker) this.ticker = this.prefill.ticker;
    if (this.prefill.side) this.side = this.prefill.side;
    if (this.prefill.runId && this.prefill.decisionId) {
      this.loadSuggestion();
    }
  }

  setQuantityMode(mode: QuantityMode): void {
    if (this.quantityMode === mode) return;
    this.quantityMode = mode;
    if (this.prefill?.runId && this.prefill?.decisionId) {
      this.loadSuggestion();
    }
  }

  private loadSuggestion(): void {
    if (!this.prefill?.runId || !this.prefill?.decisionId) return;
    this.error.set(null);
    this.store
      .loadSuggestion(this.prefill.runId, this.prefill.decisionId, this.quantityMode)
      .subscribe({
        next: (s) => {
          this.ticker = s.ticker;
          this.side = s.side;
          if (s.suggested_quantity && Number(s.suggested_quantity) > 0) {
            this.quantity = Number(s.suggested_quantity);
          }
          if (s.current_price && Number(s.current_price) > 0) {
            this.entryPrice = Number(s.current_price);
          }
        },
        error: (err) => {
          this.error.set(err?.error?.detail || 'Failed to load suggestion.');
        },
      });
  }

  titleText(): string {
    const s = this.suggestion();
    if (s && s.action_label) return s.action_label + (this.ticker ? ` · ${this.ticker}` : '');
    return this.ticker ? `Enter position · ${this.ticker}` : 'Enter position';
  }

  currentPrice(): number {
    if (this.entryPrice && this.entryPrice > 0) return this.entryPrice;
    const s = this.suggestion();
    return s ? Number(s.current_price || 0) : 0;
  }

  priceAsOf(): string | null {
    return this.suggestion()?.price_as_of || null;
  }

  notionalEstimate(): number {
    const q = Number(this.quantity || 0);
    const p = this.currentPrice();
    return Math.abs(q) * p;
  }

  weightEstimate(): number {
    const s = this.suggestion();
    const total = s ? Number(s.portfolio_total_value || 0) : Number(this.store.overview()?.total_value || 0);
    if (!total) return 0;
    return (this.notionalEstimate() / total) * 100;
  }

  warnings(): string[] {
    return this.suggestion()?.warnings ?? [];
  }

  canConfirm(): boolean {
    if (this.store.busy()) return false;
    if (!this.ticker) return false;
    if (!this.quantity || this.quantity <= 0) return false;
    if (!this.entryPrice || this.entryPrice <= 0) return false;
    const s = this.suggestion();
    if (s && s.action_label === 'Reduce/close first') return false;
    return true;
  }

  confirmLabel(): string {
    const s = this.suggestion();
    if (s?.action_label === 'Increase position') return 'Increase position';
    if (s?.action_label === 'Reduce/close first') return 'Close first';
    return 'Confirm position';
  }

  formatQty(raw: string): string {
    const n = Number(raw);
    if (!Number.isFinite(n)) return raw;
    if (Math.abs(n - Math.round(n)) < 1e-9) return n.toFixed(0);
    return n.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }

  onConfirm(): void {
    if (!this.canConfirm()) return;
    this.error.set(null);
    const body: OpenPositionRequest = {
      ticker: this.ticker.trim().toUpperCase(),
      side: this.side,
      quantity: String(this.quantity || 0),
      entry_price: String(this.entryPrice || 0),
      quantity_mode: this.quantityMode,
      source_run: this.prefill?.runId ?? null,
      source_decision: this.prefill?.decisionId ?? null,
      note: this.note,
    };
    this.store.openPosition(body).subscribe({
      next: () => this.closed.emit({ saved: true }),
      error: (err) => this.error.set(err?.error?.detail || 'Failed to save.'),
    });
  }

  onCancel(): void {
    this.closed.emit({ saved: false });
  }
}
