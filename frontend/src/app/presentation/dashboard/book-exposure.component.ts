import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { GrossNetMeterComponent } from '../shared/gross-net-meter.component';
import { TickerComponent } from '../shared/ticker.component';
import { EmptyStateComponent } from '../shared/empty-state.component';

/**
 * View-model for the strategy "book" (latest cycle target weights) that the
 * dashboard already derives in DashboardPage.ngOnInit. Distinct from the live
 * Manual Book shown in the KPI strip / NAV hero.
 */
export interface BookExposureVm {
  strategy_name: string;
  strategy_id: number;
  as_of_date: string;
  gross_pct: string;
  net_pct: string;
  target_gross_pct: string;
  target_net_pct: string;
  longPct: number;
  shortPct: number;
  netSigned: number;
  longCount: number;
  shortCount: number;
  positionCount: number;
  longs: { ticker: string; weight: number }[];
  shorts: { ticker: string; weight: number }[];
}

@Component({
  selector: 'hf-book-exposure',
  standalone: true,
  imports: [CommonModule, RouterLink, GrossNetMeterComponent, TickerComponent, EmptyStateComponent],
  template: `
    <section class="card book-exposure">
      <div class="card-hd">
        <h2 class="title">Book exposure</h2>
        @if (book; as b) {
          <span class="pill"><span class="dot"></span>{{ b.strategy_name }} · latest cycle</span>
        }
      </div>
      <div class="card-bd">
        @if (book; as b) {
          <hf-gross-net-meter [longPct]="b.longPct" [shortPct]="b.shortPct" />
          <div class="gn-labels">
            <span class="ll">Long {{ b.longPct.toFixed(1) }}%</span>
            <span class="net">net {{ b.netSigned >= 0 ? '+' : '' }}{{ b.netSigned.toFixed(1) }}%</span>
            <span class="sl">Short {{ b.shortPct.toFixed(1) }}%</span>
          </div>
          <div class="book-stats">
            <div class="bs">
              <span class="k">Gross</span><span class="v mono">{{ grossPct(b) }}%</span>
            </div>
            <div class="bs">
              <span class="k">Net</span><span class="v mono">{{ netPct(b) }}%</span>
            </div>
            <div class="bs">
              <span class="k">Leverage</span><span class="v mono">{{ leverage(b) }}×</span>
            </div>
          </div>
          <div class="holdings-hd">Top weights · as of {{ b.as_of_date }}</div>
          <ul class="holdings">
            @for (h of b.longs; track h.ticker) {
              <li class="holding">
                <span class="side l" aria-label="Long">L</span>
                <hf-ticker [ticker]="h.ticker"></hf-ticker>
                <span class="wbar"><span class="fill l" [style.width.%]="barW(h.weight)"></span></span>
                <span class="w long">+{{ h.weight.toFixed(2) }}%</span>
              </li>
            }
            @for (h of b.shorts; track h.ticker) {
              <li class="holding">
                <span class="side s" aria-label="Short">S</span>
                <hf-ticker [ticker]="h.ticker"></hf-ticker>
                <span class="wbar"><span class="fill s" [style.width.%]="barW(h.weight)"></span></span>
                <span class="w short">{{ h.weight.toFixed(2) }}%</span>
              </li>
            }
          </ul>
          <a class="btn ghost sm open-book" [routerLink]="['/strategies', b.strategy_id]">
            Open strategy book →
          </a>
        } @else if (!loaded) {
          <div aria-busy="true" aria-label="Loading book exposure" class="flex flex-col gap-2">
            <div class="skel h-2 w-full"></div>
            <div class="skel h-3.5 w-[70%] mt-2"></div>
            <div class="skel h-[26px] w-full mt-2"></div>
            <div class="skel h-[26px] w-full"></div>
          </div>
        } @else {
          <hf-empty-state message="No strategy cycles yet.">
            <a class="btn primary" routerLink="/strategies/new">Create a strategy</a>
          </hf-empty-state>
        }
      </div>
    </section>
  `,
  styles: [
    `
      .book-exposure { margin: 0; height: 100%; }
      .gn-labels {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        font-family: var(--font-mono);
        font-size: 11px;
        margin-top: 6px;
      }
      .gn-labels .ll { color: var(--acc-long-fg); }
      .gn-labels .sl { color: var(--acc-short-fg); }
      .gn-labels .net { color: var(--text-3); }
      .book-stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 8px;
        margin: 14px 0;
        padding: 12px 0;
        border-top: 1px solid var(--border);
        border-bottom: 1px solid var(--border);
      }
      .bs { display: flex; flex-direction: column; gap: 3px; }
      .bs .k {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-3);
      }
      .bs .v { font-size: 13px; color: var(--text); }
      .holdings-hd {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-3);
        margin-bottom: 8px;
      }
      .holdings { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
      .holding {
        display: grid;
        grid-template-columns: 18px auto 1fr auto;
        align-items: center;
        gap: 10px;
      }
      .side {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 16px;
        height: 16px;
        border-radius: var(--r-4);
        font-family: var(--font-mono);
        font-size: 10px;
        font-weight: 600;
      }
      .side.l { background: var(--acc-long-soft); color: var(--acc-long-fg); }
      .side.s { background: var(--acc-short-soft); color: var(--acc-short-fg); }
      .wbar {
        height: 6px;
        border-radius: var(--r-full);
        background: var(--surface-2);
        overflow: hidden;
      }
      .wbar .fill { display: block; height: 100%; border-radius: var(--r-full); }
      .wbar .fill.l { background: var(--acc-long); }
      .wbar .fill.s { background: var(--acc-short); }
      .w { font-family: var(--font-mono); font-size: 12px; }
      .w.long { color: var(--acc-long-fg); }
      .w.short { color: var(--acc-short-fg); }
      .open-book { margin-top: 14px; }
    `,
  ],
})
export class BookExposureComponent {
  @Input() book: BookExposureVm | null = null;
  @Input() loaded = false;

  // P10 §C1: PortfolioTarget.gross_pct / net_pct are FRACTIONS of NAV
  // (1.0 = 100%), not percents. The old template printed the raw fraction with
  // a "%" suffix ("GROSS 1.0000%") and divided by 100 again for leverage
  // ("0.01×"). Convert exactly once here.
  grossPct(b: BookExposureVm): string {
    const g = Number(b.gross_pct);
    return (Number.isFinite(g) ? g * 100 : 0).toFixed(1);
  }

  netPct(b: BookExposureVm): string {
    const n = Number(b.net_pct);
    return (Number.isFinite(n) ? n * 100 : 0).toFixed(1);
  }

  leverage(b: BookExposureVm): string {
    const g = Number(b.gross_pct);
    return (Number.isFinite(g) ? g : 0).toFixed(2);
  }

  /** Bar width as a % of the largest absolute weight currently shown. */
  barW(weight: number): number {
    const all = [...(this.book?.longs ?? []), ...(this.book?.shorts ?? [])];
    const max = Math.max(1, ...all.map((h) => Math.abs(h.weight)));
    return Math.max(4, (Math.abs(weight) / max) * 100);
  }
}
