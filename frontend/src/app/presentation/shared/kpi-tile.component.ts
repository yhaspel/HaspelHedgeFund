import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { SparkbarComponent } from './sparkbar.component';

@Component({
  selector: 'hf-kpi-tile',
  standalone: true,
  imports: [CommonModule, SparkbarComponent],
  template: `
    <div class="kpi-tile">
      <div class="k">{{ eyebrow }}</div>
      <div class="v mono" [class.up]="!delta && tone==='up'" [class.down]="!delta && tone==='down'">{{ value }}</div>
      @if (delta) {
        <div class="d" [class.up]="tone==='up'" [class.down]="tone==='down'">{{ delta }}</div>
      }
      @if (sub) {
        <div class="sub">{{ sub }}</div>
      }
      @if (spark && spark.length) {
        <hf-sparkbar [points]="spark" [tone]="tone" [height]="22" />
      }
    </div>
  `,
  styles: [
    `
      .kpi-tile {
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 14px 16px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--r-8, 8px);
        min-height: 96px;
      }
      .k {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-3);
      }
      .v {
        font-size: 24px;
        font-weight: 500;
        color: var(--text);
        line-height: 1.1;
        margin-top: 2px;
      }
      .v.up { color: var(--acc-long-fg); }
      .v.down { color: var(--acc-short-fg); }
      .d {
        font-family: var(--font-mono);
        font-size: 12px;
        color: var(--text-2);
      }
      .d.up { color: var(--acc-long-fg); }
      .d.down { color: var(--acc-short-fg); }
      .sub {
        font-size: 11px;
        color: var(--text-3);
      }
    `,
  ],
})
export class KpiTileComponent {
  @Input({ required: true }) eyebrow!: string;
  @Input({ required: true }) value!: string;
  @Input() delta?: string;
  @Input() sub?: string;
  @Input() spark?: number[];
  @Input() tone: 'up' | 'down' | 'neutral' = 'neutral';
}
