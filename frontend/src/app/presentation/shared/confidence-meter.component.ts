import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'hf-confidence-meter',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="cm" [attr.aria-label]="'Confidence ' + clamped + ' of 100'">
      <div class="track">
        <div class="fill" [style.width.%]="clamped" [class.buy]="tone==='buy'" [class.sell]="tone==='sell'" [class.hold]="tone==='hold'"></div>
        @for (t of ticks; track $index) {
          <span class="tick" [style.left.%]="t"></span>
        }
        <span class="marker" [style.left.%]="clamped"></span>
      </div>
      <div class="legend">
        <span class="value mono">{{ clamped }} / 100</span>
        @if (counts) {
          <span class="counts">
            <span class="bull">{{ counts.bull }} bull</span>
            <span class="sep">·</span>
            <span class="neut">{{ counts.neutral }} neutral</span>
            <span class="sep">·</span>
            <span class="bear">{{ counts.bear }} bear</span>
          </span>
        }
      </div>
    </div>
  `,
  styles: [
    `
      .cm {
        display: flex;
        flex-direction: column;
        gap: 6px;
        min-width: 200px;
      }
      .track {
        position: relative;
        height: 8px;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-full, 99px);
        overflow: hidden;
      }
      .fill {
        height: 100%;
        background: linear-gradient(90deg, color-mix(in oklab, var(--acc-info) 50%, transparent), var(--acc-info));
        transition: width var(--dur-chart, 600ms) var(--ease-out-ui);
      }
      .fill.buy { background: linear-gradient(90deg, color-mix(in oklab, var(--acc-long) 40%, transparent), var(--acc-long)); }
      .fill.sell { background: linear-gradient(90deg, color-mix(in oklab, var(--acc-short) 40%, transparent), var(--acc-short)); }
      .fill.hold { background: linear-gradient(90deg, color-mix(in oklab, var(--acc-hold) 40%, transparent), var(--acc-hold)); }
      .tick {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 1px;
        background: color-mix(in oklab, var(--text) 14%, transparent);
        pointer-events: none;
      }
      .marker {
        position: absolute;
        top: -2px;
        width: 4px;
        height: 12px;
        background: var(--text);
        border-radius: 1px;
        transform: translateX(-2px);
      }
      .legend {
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 11px;
        color: var(--text-3);
        gap: 8px;
      }
      .value { color: var(--text-2); }
      .counts { display: flex; gap: 4px; align-items: center; }
      .bull { color: var(--acc-long-fg); }
      .bear { color: var(--acc-short-fg); }
      .neut { color: var(--text-3); }
      .sep { color: var(--text-3); opacity: 0.5; }
    `,
  ],
})
export class ConfidenceMeterComponent {
  @Input() value = 0;
  @Input() tone: 'buy' | 'sell' | 'hold' | 'info' = 'info';
  @Input() counts?: { bull: number; bear: number; neutral: number };

  ticks = [12.5, 25, 37.5, 50, 62.5, 75, 87.5];

  get clamped(): number {
    return Math.max(0, Math.min(100, Math.round(this.value)));
  }
}
