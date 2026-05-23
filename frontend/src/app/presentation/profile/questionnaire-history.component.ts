import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';

import { QuestionnaireHistoryItem } from '../../core/models/investor-profile.model';

@Component({
  selector: 'hf-questionnaire-history',
  standalone: true,
  imports: [CommonModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (items.length === 0) {
      <p class="text-text-3 text-[12px]">No history yet.</p>
    } @else {
      <ul class="history">
        @for (it of items; track it.id) {
          <li>
            <button type="button" class="row" (click)="view.emit(it)">
              <span class="when">{{ it.created_at | date: 'mediumDate' }}</span>
              <span class="pill" [class.tuned]="it.source === 'tuned'">
                {{ it.source }}
              </span>
              <span class="model mono text-[11px] text-text-3">
                {{ it.model_id }}
              </span>
              <span class="type">
                {{ it.investor_type || (it.analysis_status === 'failed' ? '(failed)' : '…') }}
              </span>
            </button>
          </li>
        }
      </ul>
    }
  `,
  styles: [
    `
      .history {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .row {
        display: grid;
        grid-template-columns: 110px 80px 1fr 200px;
        gap: 8px;
        align-items: center;
        width: 100%;
        background: transparent;
        border: 0;
        border-radius: var(--r-4);
        padding: 6px 8px;
        text-align: left;
        color: inherit;
        cursor: pointer;
      }
      .row:hover { background: var(--surface-2); }
      .pill {
        font-size: 10px;
        text-transform: uppercase;
        padding: 1px 6px;
        background: var(--surface-2);
        color: var(--text-2);
        border-radius: var(--r-3);
        text-align: center;
      }
      .pill.tuned { background: var(--acc-info-soft); color: var(--text); }
      .type { font-size: 12px; color: var(--text); }
    `,
  ],
})
export class QuestionnaireHistoryComponent {
  @Input() items: QuestionnaireHistoryItem[] = [];
  @Output() view = new EventEmitter<QuestionnaireHistoryItem>();
}
