import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { Preset } from '../../core/models/screener.model';

@Component({
  selector: 'hf-screener-preset-bar',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="presets"
      role="group"
      aria-label="Predefined screens"
    >
      @for (p of presets; track p.id) {
        <button
          type="button"
          class="preset-chip"
          [class.active]="activeId === p.id"
          [class.disabled]="!p.available"
          [disabled]="!p.available"
          [attr.aria-pressed]="activeId === p.id"
          [attr.title]="!p.available ? tooltipFor(p) : null"
          (click)="onPick(p)"
        >
          {{ p.name }}
          @if (!p.available) {
            <span class="lock" aria-hidden="true">·</span>
          }
        </button>
      }
    </div>
  `,
  styles: [
    `
      .presets {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .preset-chip {
        height: 28px;
        padding: 0 12px;
        border-radius: var(--r-6);
        background: var(--surface-2);
        border: 1px solid var(--border);
        color: var(--text-2);
        font-size: var(--fs-12);
        font-weight: 500;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }
      .preset-chip:hover:not(:disabled) {
        background: var(--surface-3);
        color: var(--text);
      }
      .preset-chip.active {
        background: var(--acc-info-soft);
        border-color: var(--acc-info-soft);
        color: var(--acc-info-fg);
      }
      .preset-chip.disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .lock {
        opacity: 0.6;
      }
    `,
  ],
})
export class PresetBarComponent {
  @Input() presets: Preset[] = [];
  @Input() activeId = '';
  @Output() picked = new EventEmitter<Preset>();

  onPick(p: Preset): void {
    if (!p.available) return;
    this.picked.emit(p);
  }

  tooltipFor(p: Preset): string {
    const requires = (p.requires && p.requires[0]) || 'data';
    return `Requires a ${requires} data source — not yet available.`;
  }
}
