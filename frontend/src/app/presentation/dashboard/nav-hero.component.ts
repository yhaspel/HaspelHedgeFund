import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PortfolioOverview } from '../../core/models/portfolio.model';

/**
 * Compact NAV hero for the dashboard. The live Manual Book has no NAV time
 * series in the backend (only point-in-time `/portfolio/`), so this card is
 * deliberately value-only — NAV headline + real P&L / cash / long-short market
 * value breakdown. No fabricated equity curve. See plan 03b §0.
 */
@Component({
  selector: 'hf-nav-hero',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="card nav-hero">
      <div class="card-hd">
        <h2 class="title">Net asset value</h2>
        <span class="pill"><span class="dot"></span>Manual Book</span>
      </div>
      <div class="card-bd">
        @if (overview; as p) {
          <div class="nav-val mono">{{ compact(p.total_value) }}</div>
          <div class="nav-pnl">
            <span class="pnl mono" [class.up]="num(p.unrealized_pnl) > 0" [class.down]="num(p.unrealized_pnl) < 0">
              <span class="dir" aria-hidden="true">{{ dir(p.unrealized_pnl) }}</span>{{ signed(p.unrealized_pnl) }}
            </span>
            <span class="lbl">unrealized</span>
            <span class="sep" aria-hidden="true">·</span>
            <span class="pnl mono" [class.up]="num(p.realized_pnl) > 0" [class.down]="num(p.realized_pnl) < 0">
              <span class="dir" aria-hidden="true">{{ dir(p.realized_pnl) }}</span>{{ signed(p.realized_pnl) }}
            </span>
            <span class="lbl">realized</span>
          </div>
          <div class="nav-stats">
            <div class="ns">
              <span class="k">Long MV</span>
              <span class="v mono">{{ money(p.long_market_value) }}</span>
            </div>
            <div class="ns">
              <span class="k">Short MV</span>
              <span class="v mono">{{ money(p.short_market_value) }}</span>
            </div>
            <div class="ns">
              <span class="k">Cash</span>
              <span class="v mono">{{ money(p.cash_balance) }}</span>
            </div>
          </div>
        } @else {
          <div aria-busy="true" aria-label="Loading net asset value">
            <div class="skel h-9 w-[55%]"></div>
            <div class="skel h-3.5 w-[80%] mt-3"></div>
            <div class="skel h-[42px] w-full mt-3"></div>
          </div>
        }
      </div>
    </section>
  `,
  styles: [
    `
      .nav-hero { margin: 0; height: 100%; }
      .nav-val {
        font-size: 32px;
        font-weight: 500;
        line-height: 1.05;
        color: var(--text);
        letter-spacing: -0.01em;
      }
      .nav-pnl {
        display: flex;
        align-items: baseline;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 8px;
        font-size: 12px;
      }
      .nav-pnl .pnl { font-size: 13px; color: var(--text-2); }
      .nav-pnl .pnl .dir { font-size: 9px; margin-right: 3px; }
      .nav-pnl .pnl.up { color: var(--acc-long-fg); }
      .nav-pnl .pnl.down { color: var(--acc-short-fg); }
      .nav-pnl .lbl { color: var(--text-3); font-size: 11px; }
      .nav-pnl .sep { color: var(--text-3); }
      .nav-stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;
        margin-top: 16px;
        padding-top: 14px;
        border-top: 1px solid var(--border);
      }
      .ns { display: flex; flex-direction: column; gap: 3px; }
      .ns .k {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-3);
      }
      .ns .v { font-size: 13px; color: var(--text); }
    `,
  ],
})
export class NavHeroComponent {
  @Input() overview: PortfolioOverview | null = null;

  num(v: string | null | undefined): number {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  /** Compact NAV headline: $1.23M / $12.3K / $123.45. */
  compact(v: string | null | undefined): string {
    const n = this.num(v);
    const a = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
    return `${sign}$${a.toFixed(2)}`;
  }

  money(v: string | null | undefined): string {
    return `$${this.num(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  }

  signed(v: string | null | undefined): string {
    const n = this.num(v);
    const s = n >= 0 ? '+' : '-';
    return `${s}$${Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  }

  /** Colour-independent direction cue (decorative; the signed value carries the data). */
  dir(v: string | null | undefined): string {
    const n = this.num(v);
    return n > 0 ? '▲' : n < 0 ? '▼' : '—';
  }
}
