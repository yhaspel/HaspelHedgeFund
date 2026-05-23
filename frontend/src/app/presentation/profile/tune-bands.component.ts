import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import {
  HORIZON_BAND_LABEL,
  HorizonBand,
  PATIENCE_BAND_LABEL,
  PatienceBand,
  RISK_BAND_LABEL,
  RiskBand,
} from '../../core/models/investor-profile.model';

export interface TuneBandsChange {
  risk_band?: RiskBand;
  horizon_band?: HorizonBand;
  patience_band?: PatienceBand;
}

@Component({
  selector: 'hf-tune-bands',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form class="tune-form" (ngSubmit)="onSave($event)">
      <div class="field">
        <label for="tune-risk">Risk band</label>
        <select id="tune-risk" [(ngModel)]="risk" name="risk">
          @for (k of riskKeys; track k) {
            <option [value]="k">{{ riskLabel[k] }}</option>
          }
        </select>
      </div>
      <div class="field">
        <label for="tune-horizon">Time horizon</label>
        <select id="tune-horizon" [(ngModel)]="horizon" name="horizon">
          @for (k of horizonKeys; track k) {
            <option [value]="k">{{ horizonLabel[k] }}</option>
          }
        </select>
      </div>
      <div class="field">
        <label for="tune-patience">Patience</label>
        <select id="tune-patience" [(ngModel)]="patience" name="patience">
          @for (k of patienceKeys; track k) {
            <option [value]="k">{{ patienceLabel[k] }}</option>
          }
        </select>
      </div>
      <div class="actions">
        <button type="button" class="btn ghost sm" (click)="onCancel()">
          Cancel
        </button>
        <button type="submit" class="btn primary sm" [disabled]="busy">
          {{ busy ? 'Saving…' : 'Save changes' }}
        </button>
      </div>
      <p class="hint">
        Saved as a new "tuned" history row. No new LLM call —
        recommendations and the agent brief are re-derived deterministically.
      </p>
    </form>
  `,
  styles: [
    `
      .tune-form {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 12px;
        align-items: end;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .field label {
        font-size: 11px;
        color: var(--text-3);
      }
      .field select {
        padding: 6px 8px;
        background: var(--surface-2);
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: var(--r-4);
      }
      .actions {
        grid-column: 1 / -1;
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      .hint {
        grid-column: 1 / -1;
        font-size: 11px;
        color: var(--text-3);
        margin: 0;
      }
    `,
  ],
})
export class TuneBandsComponent implements OnChanges {
  @Input() initialRisk: RiskBand = 'moderate';
  @Input() initialHorizon: HorizonBand = 'medium';
  @Input() initialPatience: PatienceBand = 'medium';
  @Input() busy = false;

  @Output() save = new EventEmitter<TuneBandsChange>();
  @Output() cancel = new EventEmitter<void>();

  readonly riskLabel = RISK_BAND_LABEL;
  readonly horizonLabel = HORIZON_BAND_LABEL;
  readonly patienceLabel = PATIENCE_BAND_LABEL;
  readonly riskKeys = Object.keys(RISK_BAND_LABEL) as RiskBand[];
  readonly horizonKeys = Object.keys(HORIZON_BAND_LABEL) as HorizonBand[];
  readonly patienceKeys = Object.keys(PATIENCE_BAND_LABEL) as PatienceBand[];

  risk: RiskBand = this.initialRisk;
  horizon: HorizonBand = this.initialHorizon;
  patience: PatienceBand = this.initialPatience;

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['initialRisk']) this.risk = this.initialRisk;
    if (changes['initialHorizon']) this.horizon = this.initialHorizon;
    if (changes['initialPatience']) this.patience = this.initialPatience;
  }

  onSave(event: Event): void {
    event.preventDefault();
    const change: TuneBandsChange = {};
    if (this.risk !== this.initialRisk) change.risk_band = this.risk;
    if (this.horizon !== this.initialHorizon)
      change.horizon_band = this.horizon;
    if (this.patience !== this.initialPatience)
      change.patience_band = this.patience;
    if (Object.keys(change).length === 0) {
      this.cancel.emit();
      return;
    }
    this.save.emit(change);
  }

  onCancel(): void {
    this.cancel.emit();
  }
}
