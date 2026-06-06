import { Component, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ModalComponent } from './modal.component';
import { ConfirmOptions, ConfirmService } from './confirm.service';

let _seq = 0;

/**
 * hf-confirm — the single global host for {@link ConfirmService}.
 *
 * Mounted once at the app root. Renders the active confirm request inside
 * hf-modal (focus trap, Escape-cancels, focus-return) and settles the awaiting
 * promise on confirm/cancel. Replaces native confirm()/alert()/prompt()
 * (HHF-05 / HHF-10).
 */
@Component({
  selector: 'hf-confirm',
  standalone: true,
  imports: [CommonModule, ModalComponent],
  template: `
    @if (svc.active(); as c) {
      <hf-modal [titleId]="titleId" (closed)="cancel()">
        <div class="card confirm-card">
          <div class="card-hd">
            <h2 class="title" [id]="titleId">{{ c.title }}</h2>
          </div>
          <div class="card-bd">
            <p class="confirm-body" *ngIf="c.body">{{ c.body }}</p>
            <div class="field confirm-type" *ngIf="c.requireText as rt">
              <label class="lbl" [for]="inputId">Type “{{ rt }}” to confirm</label>
              <input
                class="input sans"
                [id]="inputId"
                type="text"
                autocomplete="off"
                spellcheck="false"
                [value]="typed()"
                (input)="typed.set($any($event.target).value)"
                (keydown.enter)="accept()"
              />
            </div>
          </div>
          <div class="confirm-ft">
            <button class="btn" (click)="cancel()">{{ c.cancelLabel || 'Cancel' }}</button>
            <button
              class="btn"
              [class.danger]="c.danger"
              [class.primary]="!c.danger"
              [disabled]="!ready(c)"
              (click)="accept()"
            >
              {{ c.confirmLabel || 'Confirm' }}
            </button>
          </div>
        </div>
      </hf-modal>
    }
  `,
  styles: [
    `
      .confirm-card {
        width: min(440px, 100%);
      }
      .confirm-body {
        margin: 0;
        font-size: var(--fs-13);
        line-height: var(--lh-13);
        color: var(--text-2);
      }
      .confirm-type {
        margin-top: 12px;
      }
      .confirm-ft {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        padding: 12px 16px;
        border-top: 1px solid var(--border);
      }
    `,
  ],
})
export class ConfirmHostComponent {
  readonly svc = inject(ConfirmService);
  readonly typed = signal('');
  readonly titleId = `hf-confirm-title-${++_seq}`;
  readonly inputId = `hf-confirm-input-${_seq}`;

  constructor() {
    // Clear the "type to confirm" field whenever a new request opens.
    effect(() => {
      this.svc.active();
      this.typed.set('');
    });
  }

  ready(c: ConfirmOptions): boolean {
    return c.requireText ? this.typed().trim() === c.requireText : true;
  }

  accept(): void {
    const c = this.svc.active();
    if (!c || !this.ready(c)) return;
    this.svc.settle(true);
  }

  cancel(): void {
    this.svc.settle(false);
  }
}
