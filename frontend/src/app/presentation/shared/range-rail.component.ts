import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

export type RangeRailTone = 'long' | 'short' | 'hold' | 'neutral';

@Component({
  selector: 'hf-range-rail',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="rr" [attr.aria-label]="ariaLabel || defaultAriaLabel">
      <div class="rail" [class.long]="tone === 'long'" [class.short]="tone === 'short'" [class.hold]="tone === 'hold'">
        @if (bandPos; as bp) {
          <span class="band" [style.left.%]="bp.left" [style.width.%]="bp.width"></span>
        }
        @if (markerPos !== null) {
          <span class="marker" [style.left.%]="markerPos"></span>
        }
      </div>
      <div class="labels">
        <span class="mono">{{ format(min) }}</span>
        <span class="mono">{{ format(max) }}</span>
      </div>
    </div>
  `,
  styles: [
    `
      .rr {
        display: flex;
        flex-direction: column;
        gap: 6px;
        width: 100%;
      }
      .rail {
        position: relative;
        height: 12px;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-full, 9999px);
      }
      .band {
        position: absolute;
        top: 0;
        bottom: 0;
        background: color-mix(in oklab, var(--text-3) 22%, transparent);
        border-radius: var(--r-full, 9999px);
      }
      .rail.long .band  { background: color-mix(in oklab, var(--acc-long) 28%, transparent); }
      .rail.short .band { background: color-mix(in oklab, var(--acc-short) 28%, transparent); }
      .rail.hold .band  { background: color-mix(in oklab, var(--acc-hold) 28%, transparent); }
      .marker {
        position: absolute;
        top: -3px;
        width: 4px;
        height: 18px;
        background: var(--text);
        border-radius: 1px;
        transform: translateX(-2px);
      }
      .rail.long .marker  { background: var(--acc-long); }
      .rail.short .marker { background: var(--acc-short); }
      .rail.hold .marker  { background: var(--acc-hold); }
      .labels {
        display: flex;
        justify-content: space-between;
        font-size: 11px;
        color: var(--text-3);
      }
    `,
  ],
})
export class RangeRailComponent {
  @Input({ required: true }) min!: number;
  @Input({ required: true }) max!: number;
  @Input() value: number | null = null;
  @Input() bandLo?: number;
  @Input() bandHi?: number;
  @Input() tone: RangeRailTone = 'neutral';
  @Input() format: (v: number) => string = (v) => v.toFixed(2);
  @Input() ariaLabel?: string;

  get markerPos(): number | null {
    const v = this.value;
    if (v === null || !Number.isFinite(v)) return null;
    return this.posPct(v);
  }

  get bandPos(): { left: number; width: number } | null {
    const lo = this.bandLo;
    const hi = this.bandHi;
    if (lo === undefined || hi === undefined) return null;
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const a = this.posPct(Math.min(lo, hi));
    const b = this.posPct(Math.max(lo, hi));
    return { left: a, width: Math.max(0, b - a) };
  }

  get defaultAriaLabel(): string {
    const valStr = this.value === null ? '—' : this.format(this.value);
    return `Value ${valStr} on range ${this.format(this.min)} to ${this.format(this.max)}`;
  }

  private posPct(v: number): number {
    const min = this.min;
    const max = this.max;
    if (!Number.isFinite(min) || !Number.isFinite(max) || max === min) return 0;
    const raw = ((v - min) / (max - min)) * 100;
    return Math.max(0, Math.min(100, raw));
  }
}
