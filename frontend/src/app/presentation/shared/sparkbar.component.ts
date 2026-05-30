import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'hf-sparkbar',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="bars" [style.height.px]="height" aria-hidden="true">
      @for (b of bars; track $index) {
        <span class="bar" [class.up]="tone==='up'" [class.down]="tone==='down'" [style.height.%]="b"></span>
      }
    </div>
  `,
  styles: [
    `
      .bars {
        display: flex;
        align-items: flex-end;
        gap: 2px;
        width: 100%;
      }
      .bar {
        flex: 1;
        background: var(--text-3);
        border-radius: 1px;
        min-height: 1px;
        opacity: 0.75;
      }
      .bar.up { background: var(--acc-long); }
      .bar.down { background: var(--acc-short); }
    `,
  ],
})
export class SparkbarComponent {
  @Input() points: number[] = [];
  @Input() height = 20;
  @Input() tone: 'up' | 'down' | 'neutral' = 'neutral';

  get bars(): number[] {
    if (!this.points.length) return [];
    const max = Math.max(...this.points, 1);
    const min = Math.min(...this.points, 0);
    const range = max - min || 1;
    return this.points.map((p) => Math.max(6, ((p - min) / range) * 100));
  }
}
