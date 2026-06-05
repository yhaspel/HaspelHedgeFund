import {
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ModalComponent } from '../shared/modal.component';
import { BrokerStore } from '../../abstraction/broker.store';
import { LiveDisclaimer } from '../../core/models/broker.model';

@Component({
  selector: 'hf-disclaimer-modal',
  standalone: true,
  imports: [CommonModule, ModalComponent],
  template: `
    <hf-modal titleId="disclaimer-title" [dismissOnOverlay]="false" (closed)="closed.emit()">
      <div class="card max-w-[540px]" (click)="$event.stopPropagation()">
        <div class="card-hd">
          <h2 class="title" id="disclaimer-title">
            Live-trading disclaimer · version {{ disclaimer?.version }}
          </h2>
        </div>
        <div class="p-3.5">
          @if (disclaimer) {
            <div class="text-xs text-text-2 whitespace-pre-line">{{ disclaimer.body }}</div>
            @if (error(); as e) {
              <div role="alert" class="pill err h-auto py-1.5 px-2.5 mt-2.5"><span class="dot"></span>{{ e }}</div>
            }
            <div class="text-right mt-3">
              <button class="btn" (click)="closed.emit()">Cancel</button>
              <button class="btn primary ml-2" (click)="onAccept()" [disabled]="busy()"
                      data-test="accept-disclaimer">
                {{ busy() ? 'Saving…' : 'I have read and accept' }}
              </button>
            </div>
          }
        </div>
      </div>
    </hf-modal>
  `,
})
export class DisclaimerModalComponent {
  @Input() disclaimer: LiveDisclaimer | null = null;
  @Output() closed = new EventEmitter<void>();
  @Output() accepted = new EventEmitter<void>();

  private readonly store = inject(BrokerStore);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  onAccept(): void {
    if (!this.disclaimer) return;
    this.busy.set(true);
    this.store.acceptDisclaimer(this.disclaimer.version).subscribe({
      next: () => {
        this.busy.set(false);
        this.accepted.emit();
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.detail ?? 'Could not record acceptance.');
      },
    });
  }
}
