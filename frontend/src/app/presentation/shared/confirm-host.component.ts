import { Component, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ModalComponent } from './modal.component';
import { ConfirmService, PendingRequest } from './confirm.service';

let _seq = 0;

/**
 * hf-confirm — the single global host for {@link ConfirmService}.
 *
 * Mounted once at the app root. Renders the active request inside hf-modal
 * (focus trap, Escape-cancels, focus-return) and settles the awaiting promise.
 * Replaces the native confirm / alert / prompt dialogs (HHF-05 / HHF-10).
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

            <!-- prompt: a single text field (replaces native prompt) -->
            <div class="field confirm-type" *ngIf="c.kind === 'prompt'">
              <label class="lbl" [for]="inputId">{{ c.label || 'Value' }}</label>
              <input
                class="input sans"
                [id]="inputId"
                type="text"
                autocomplete="off"
                [placeholder]="c.placeholder || ''"
                [value]="typed()"
                (input)="typed.set($any($event.target).value)"
                (keydown.enter)="accept()"
              />
            </div>

            <!-- confirm with type-to-confirm gate (high-stakes actions) -->
            <div class="field confirm-type" *ngIf="c.kind === 'confirm' && c.requireText as rt">
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
            <button class="btn" *ngIf="c.kind !== 'notify'" (click)="cancel()">
              {{ c.cancelLabel || 'Cancel' }}
            </button>
            <button
              class="btn"
              [class.danger]="c.danger"
              [class.primary]="!c.danger"
              [disabled]="!ready(c)"
              (click)="accept()"
            >
              {{ c.confirmLabel || (c.kind === 'notify' ? 'OK' : 'Confirm') }}
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
    // Reset the input when a new request opens; prompts seed their initial value.
    effect(() => {
      const c = this.svc.active();
      this.typed.set(c && c.kind === 'prompt' ? (c.initialValue ?? '') : '');
    });
  }

  ready(c: PendingRequest): boolean {
    if (c.kind === 'prompt') return this.typed().trim().length > 0;
    if (c.kind === 'confirm' && c.requireText) return this.typed().trim() === c.requireText;
    return true;
  }

  accept(): void {
    const c = this.svc.active();
    if (!c || !this.ready(c)) return;
    this.svc.accept(c.kind === 'prompt' ? this.typed().trim() : true);
  }

  cancel(): void {
    this.svc.cancel();
  }
}
