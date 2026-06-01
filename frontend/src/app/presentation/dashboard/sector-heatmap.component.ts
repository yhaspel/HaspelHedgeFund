import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

type Stance = 'overweight' | 'neutral' | 'underweight';

/**
 * Sector implications heatmap, driven entirely by the real
 * `MacroSnapshot.sector_implications` map (overweight / neutral / underweight).
 * Colour is always paired with a text stance label (a11y: not colour-only).
 */
@Component({
  selector: 'hf-sector-heatmap',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="card">
      <div class="card-hd">
        <h2 class="title">Sector implications</h2>
        <span class="pill"><span class="dot"></span>from macro regime</span>
        <div class="legend" aria-hidden="true">
          <span class="lg over">overweight</span>
          <span class="lg neut">neutral</span>
          <span class="lg under">underweight</span>
        </div>
      </div>
      <div class="card-bd">
        @if (cells.length) {
          <ul class="sector-grid">
            @for (c of cells; track c.key) {
              <li
                class="scell"
                [class.over]="c.stance === 'overweight'"
                [class.neut]="c.stance === 'neutral'"
                [class.under]="c.stance === 'underweight'"
                [attr.aria-label]="c.label + ': ' + c.stanceLabel"
              >
                <span class="snm">{{ c.label }}</span>
                <span class="stance">{{ c.stanceLabel }}</span>
              </li>
            }
          </ul>
        } @else {
          <p class="muted">No sector implications in the current snapshot.</p>
        }
      </div>
    </section>
  `,
  styles: [
    `
      .legend { display: flex; gap: 6px; margin-left: auto; }
      .legend .lg {
        font-size: 10px;
        padding: 2px 8px;
        border-radius: var(--r-full);
        border: 1px solid var(--border);
        color: var(--text-3);
      }
      .legend .lg.over { color: var(--acc-long-fg); border-color: var(--acc-long-soft); }
      .legend .lg.neut { color: var(--text-2); }
      .legend .lg.under { color: var(--acc-short-fg); border-color: var(--acc-short-soft); }
      .sector-grid {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
      }
      @media (max-width: 1180px) {
        .sector-grid { grid-template-columns: repeat(3, 1fr); }
      }
      @media (max-width: 720px) {
        .sector-grid { grid-template-columns: repeat(2, 1fr); }
      }
      .scell {
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 10px 12px;
        border-radius: var(--r-6);
        border: 1px solid var(--border);
        background: var(--surface-2);
      }
      .scell.over { background: var(--acc-long-soft); border-color: var(--acc-long-soft); }
      .scell.under { background: var(--acc-short-soft); border-color: var(--acc-short-soft); }
      .snm { font-size: 12.5px; color: var(--text); font-weight: 500; }
      .stance {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-3);
      }
      .scell.over .stance { color: var(--acc-long-fg); }
      .scell.under .stance { color: var(--acc-short-fg); }
      .muted { font-size: 13px; color: var(--text-3); margin: 0; }
    `,
  ],
})
export class SectorHeatmapComponent {
  @Input() implications: Record<string, Stance> | null | undefined;

  private readonly order: Record<Stance, number> = {
    overweight: 0,
    neutral: 1,
    underweight: 2,
  };

  get cells(): { key: string; label: string; stance: Stance; stanceLabel: string }[] {
    const src = this.implications;
    if (!src) return [];
    return Object.entries(src)
      .map(([key, stance]) => ({
        key,
        label: this.prettify(key),
        stance,
        stanceLabel: stance.charAt(0).toUpperCase() + stance.slice(1),
      }))
      .sort(
        (a, b) =>
          (this.order[a.stance] ?? 9) - (this.order[b.stance] ?? 9) ||
          a.label.localeCompare(b.label),
      );
  }

  /** Turn a snapshot key into a display label without inventing data. */
  private prettify(key: string): string {
    if (/[a-z]/.test(key) && (key.includes('_') || key === key.toLowerCase())) {
      return key
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (m) => m.toUpperCase());
    }
    return key;
  }
}
