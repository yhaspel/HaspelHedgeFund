import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'hf-empty-state',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="es" [class.compact]="compact" role="status">
      <p class="msg">{{ message }}</p>
      @if (detail) {
        <p class="detail">{{ detail }}</p>
      }
      <ng-content></ng-content>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
        width: 100%;
      }
      .es {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 12px;
        padding: 32px 12px;
        text-align: center;
        color: var(--text-3);
      }
      .es.compact {
        padding: 16px 8px;
        gap: 8px;
      }
      .msg {
        margin: 0;
        font-size: 13px;
        color: var(--text-2);
      }
      .es.compact .msg {
        font-size: 12px;
      }
      .detail {
        margin: 0;
        font-size: 11.5px;
        color: var(--text-3);
        max-width: 44ch;
      }
    `,
  ],
})
export class EmptyStateComponent {
  @Input({ required: true }) message!: string;
  @Input() detail?: string;
  @Input() compact = false;
}
