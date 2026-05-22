import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PersonaMeta } from '../../core/models/run.model';
import { InfoTooltipComponent } from './info-tooltip.component';

@Component({
  selector: 'hf-persona-card',
  standalone: true,
  imports: [CommonModule, InfoTooltipComponent],
  template: `
    <div class="pp" [class.selected]="selected">
      <label class="pp-label">
        <input
          type="checkbox"
          class="pp-input"
          [checked]="selected"
          (change)="toggled.emit()"
          [attr.aria-label]="persona.name + ' — ' + persona.tagline" />
        <span class="mono-tile sm mt-c{{ persona.monogramVariant }}" aria-hidden="true">{{ persona.initials }}</span>
        <span class="meta">
          <span class="name">{{ persona.name }}</span>
          <span class="tag">{{ persona.tagline }}</span>
        </span>
        <span class="check" aria-hidden="true">
          @if (selected) {
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2.5 6.5L5 9L9.5 3.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          }
        </span>
      </label>
      <div class="info">
        <hf-info [text]="persona.description" />
      </div>
    </div>
  `,
  styles: [
    `
      .pp {
        display: grid;
        grid-template-columns: 1fr auto;
        align-items: stretch;
        border: 1px solid var(--border);
        border-radius: var(--r-8, 8px);
        background: var(--surface);
        transition: background var(--dur-fast, 120ms) var(--ease-out-ui), border-color var(--dur-fast, 120ms) var(--ease-out-ui), transform var(--dur-fast, 120ms) var(--ease-out-ui);
      }
      .pp:hover {
        background: var(--surface-2);
        transform: translateY(-1px);
      }
      .pp.selected {
        background: color-mix(in oklab, var(--acc-info) 8%, var(--surface));
        border-color: color-mix(in oklab, var(--acc-info) 50%, var(--border));
      }
      .pp-label {
        display: grid;
        grid-template-columns: 32px 1fr auto;
        align-items: center;
        gap: 10px;
        padding: 10px 12px;
        cursor: pointer;
      }
      .pp-input {
        /* Visually hidden but focusable & still toggles via the label. */
        position: absolute;
        opacity: 0;
        pointer-events: none;
        width: 1px;
        height: 1px;
      }
      .pp-input:focus-visible + .mono-tile {
        box-shadow: var(--focus-ring);
        border-radius: 999px;
      }
      .meta {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .name {
        color: var(--text);
        font-size: 13px;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .tag {
        color: var(--text-3);
        font-size: 11px;
      }
      .info {
        display: flex;
        align-items: center;
        padding: 0 10px;
        opacity: 0.7;
      }
      .info:hover {
        opacity: 1;
      }
      .check {
        width: 18px;
        height: 18px;
        border-radius: var(--r-4, 4px);
        border: 1px solid var(--border-2);
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--acc-info);
        background: var(--surface);
      }
      .pp.selected .check {
        background: var(--acc-info);
        border-color: var(--acc-info);
        color: white;
      }
    `,
  ],
})
export class PersonaCardComponent {
  @Input({ required: true }) persona!: PersonaMeta;
  @Input() selected = false;
  @Output() toggled = new EventEmitter<void>();
}
