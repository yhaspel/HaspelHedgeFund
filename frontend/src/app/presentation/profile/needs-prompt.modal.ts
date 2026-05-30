/**
 * Welcome modal for new users — invites them to take the 2-minute
 * investor questionnaire. Shows once (form === "modal"); thereafter the
 * monthly banner takes over.
 */
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';

import { ModalComponent } from '../shared/modal.component';

@Component({
  selector: 'hf-needs-prompt-modal',
  standalone: true,
  imports: [CommonModule, RouterLink, ModalComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (open) {
      <hf-modal titleId="needs-prompt-title" (closed)="onDismiss()">
        <div class="card welcome" (click)="$event.stopPropagation()">
          <div class="card-hd">
            <h2 class="title" id="needs-prompt-title">
              Personalize your analyses
            </h2>
            <button class="icon-btn" type="button"
                    aria-label="Close"
                    (click)="onDismiss()">
              <svg width="16" height="16" aria-hidden="true">
                <use href="/icons.svg#i-x" />
              </svg>
            </button>
          </div>
          <div class="card-bd">
            <p>
              A 2-minute questionnaire about your goals, risk tolerance and
              temperament. The council uses your answers to calibrate
              confidence, sizing posture and the Risk Manager's wording for
              every analysis you run from now on.
            </p>
            <p class="text-text-3 text-[12px] mt-3">
              It's <strong>optional</strong> — without it, analyses behave
              exactly as today. You can retake or fine-tune any time.
            </p>
            <div class="actions">
              <button type="button" class="btn ghost" (click)="onDismiss()">
                Maybe later
              </button>
              <a class="btn primary"
                 routerLink="/profile/questionnaire"
                 (click)="onTake()">
                Take the questionnaire
              </a>
            </div>
          </div>
        </div>
      </hf-modal>
    }
  `,
  styles: [
    `
      .welcome {
        max-width: 480px;
        background: var(--surface);
      }
      .actions {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        margin-top: 18px;
      }
      .icon-btn {
        background: transparent;
        border: 0;
        cursor: pointer;
      }
    `,
  ],
})
export class NeedsPromptModalComponent {
  @Input() open = false;
  @Output() dismissed = new EventEmitter<void>();
  @Output() take = new EventEmitter<void>();

  onDismiss(): void {
    this.dismissed.emit();
  }

  onTake(): void {
    this.take.emit();
  }
}
