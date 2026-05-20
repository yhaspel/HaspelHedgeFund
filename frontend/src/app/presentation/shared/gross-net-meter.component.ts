import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'hf-gross-net-meter',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="meter" [attr.aria-label]="'Long ' + longPct + '%, Short ' + shortPct + '%'">
      <span class="seg long" [style.flex]="longPct"></span>
      <span class="divider"></span>
      <span class="seg short" [style.flex]="shortPct"></span>
      <span class="rest" [style.flex]="restFlex()"></span>
    </div>
  `,
  styles: [
    `
      .meter {
        display: flex;
        align-items: stretch;
        height: 8px;
        border-radius: var(--r-4, 4px);
        overflow: hidden;
        background: var(--surface-2);
        border: 1px solid var(--border);
      }
      .seg.long { background: var(--acc-long); }
      .seg.short { background: var(--acc-short); }
      .divider { width: 1px; background: var(--text-3); opacity: 0.4; }
      .rest { background: transparent; }
    `,
  ],
})
export class GrossNetMeterComponent {
  @Input() longPct = 0;
  @Input() shortPct = 0;

  restFlex(): number {
    return Math.max(0, 200 - this.longPct - this.shortPct);
  }
}
