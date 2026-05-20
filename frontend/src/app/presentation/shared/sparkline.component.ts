import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'hf-sparkline',
  standalone: true,
  imports: [CommonModule],
  template: `
    <svg [attr.width]="width" [attr.height]="height" [attr.viewBox]="'0 0 ' + width + ' ' + height" class="sl" [attr.aria-label]="'Sparkline ' + (points?.length || 0) + ' points'">
      @if (loading) {
        <line [attr.x1]="0" [attr.x2]="width" [attr.y1]="height/2" [attr.y2]="height/2"
          stroke="var(--text-3)" stroke-dasharray="2 2" stroke-width="1" />
      } @else if (path) {
        <polyline
          [attr.points]="path"
          fill="none"
          [attr.stroke]="lineColor"
          stroke-width="1.25"
          stroke-linecap="round"
          stroke-linejoin="round" />
      } @else {
        <line [attr.x1]="0" [attr.x2]="width" [attr.y1]="height/2" [attr.y2]="height/2"
          stroke="var(--text-3)" stroke-dasharray="2 2" stroke-width="1" opacity="0.4" />
      }
    </svg>
  `,
  styles: [
    `
      .sl { display: inline-block; vertical-align: middle; }
    `,
  ],
})
export class SparklineComponent {
  @Input() points: number[] | null | undefined;
  @Input() width = 64;
  @Input() height = 18;
  @Input() tone: 'auto' | 'up' | 'down' | 'neutral' = 'auto';
  @Input() loading = false;

  get path(): string | null {
    const pts = this.points;
    if (!pts || pts.length < 2) return null;
    const max = Math.max(...pts);
    const min = Math.min(...pts);
    const range = max - min || 1;
    const stepX = this.width / (pts.length - 1);
    const padY = 2;
    const usableH = this.height - padY * 2;
    return pts
      .map((p, i) => {
        const x = i * stepX;
        const y = padY + (1 - (p - min) / range) * usableH;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
  }

  get lineColor(): string {
    const pts = this.points;
    let tone = this.tone;
    if (tone === 'auto') {
      if (!pts || pts.length < 2) tone = 'neutral';
      else tone = pts[pts.length - 1] >= pts[0] ? 'up' : 'down';
    }
    if (tone === 'up') return 'var(--acc-long)';
    if (tone === 'down') return 'var(--acc-short)';
    return 'var(--text-3)';
  }
}
