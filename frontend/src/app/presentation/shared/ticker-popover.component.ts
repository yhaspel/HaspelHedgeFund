import { Component, Input, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

import { TickerHistoryStore } from '../../abstraction/ticker-history.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { SparklineComponent } from './sparkline.component';

function fmtCompact(value: string | null): string {
  if (value === null || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000_000) return (n / 1_000_000_000_000).toFixed(2) + 'T';
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + 'B';
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (abs >= 1_000) return (n / 1_000).toFixed(2) + 'K';
  return n.toFixed(2);
}

function fmtDec(value: string | null, digits = 2): string {
  if (value === null || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(digits);
}

@Component({
  selector: 'hf-ticker-popover',
  standalone: true,
  imports: [CommonModule, SparklineComponent],
  template: `
    <div class="hf-tpop" role="tooltip" [id]="id">
      <div class="hf-tpop-hd">
        <span class="hf-tpop-sym mono">{{ ticker }}</span>
        @if (profileStore.profile(ticker); as p) {
          @if (p.name) {
            <span class="hf-tpop-name">{{ p.name }}</span>
          } @else if (p.detail) {
            <span class="hf-tpop-detail">{{ p.detail }}</span>
          } @else {
            <span class="hf-tpop-name muted">—</span>
          }
        } @else {
          <span class="hf-tpop-name muted">Loading…</span>
        }
      </div>
      <div class="hf-tpop-bd">
        <div class="hf-tpop-spark">
          <hf-sparkline
            [points]="historySpark()"
            [width]="180"
            [height]="36"
            [loading]="!historySpark() && !historyLoadedOnce()"
          />
        </div>
        <dl class="hf-tpop-stats">
          <div>
            <dt>Market Cap</dt>
            <dd class="mono">{{ marketCap() }}</dd>
          </div>
          <div>
            <dt>P/E Ratio</dt>
            <dd class="mono">{{ peRatio() }}</dd>
          </div>
          <div>
            <dt>EPS</dt>
            <dd class="mono">{{ eps() }}</dd>
          </div>
        </dl>
      </div>
      @if (asOfLabel(); as asOf) {
        <div class="hf-tpop-ft">as of {{ asOf }}</div>
      }
    </div>
  `,
  styles: [
    `
      .hf-tpop {
        min-width: 240px;
        max-width: 280px;
        background: var(--surface);
        border: 1px solid var(--border-2);
        border-radius: var(--r-6);
        box-shadow: var(--shadow-3);
        padding: 10px 12px;
        font-size: var(--fs-12);
        line-height: var(--lh-12);
        color: var(--text);
      }
      .hf-tpop-hd {
        display: flex;
        align-items: baseline;
        gap: 8px;
        margin-bottom: 8px;
      }
      .hf-tpop-sym {
        font-size: var(--fs-13);
        font-weight: 600;
        color: var(--text);
      }
      .hf-tpop-name {
        color: var(--text-2);
        font-size: var(--fs-12);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .hf-tpop-name.muted {
        color: var(--text-3);
        font-style: italic;
      }
      .hf-tpop-detail {
        color: var(--acc-hold-fg);
        font-size: 11.5px;
      }
      .hf-tpop-spark {
        margin: 4px 0 8px;
      }
      .hf-tpop-stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 4px 8px;
        margin: 0;
      }
      .hf-tpop-stats div {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .hf-tpop-stats dt {
        font-size: var(--fs-11);
        line-height: 14px;
        letter-spacing: var(--tracking-wide);
        text-transform: uppercase;
        color: var(--text-3);
        margin: 0;
      }
      .hf-tpop-stats dd {
        margin: 0;
        font-size: var(--fs-12);
        color: var(--text);
      }
      .hf-tpop-ft {
        margin-top: 8px;
        font-size: var(--fs-11);
        color: var(--text-3);
        letter-spacing: var(--tracking-wide);
        text-transform: uppercase;
      }
    `,
  ],
})
export class TickerPopoverComponent {
  @Input({ required: true }) ticker = '';
  @Input({ required: true }) id = '';

  readonly profileStore = inject(TickerProfileStore);
  private readonly history = inject(TickerHistoryStore);

  readonly historyLoadedOnce = signal(false);
  readonly historySpark = computed<number[] | null>(() => {
    // re-evaluate when either store bumps.
    this.history._bump();
    this.profileStore._bump();
    return this.history.closes(this.ticker);
  });

  readonly marketCap = computed(() => {
    this.profileStore._bump();
    const p = this.profileStore.profile(this.ticker);
    return fmtCompact(p?.market_cap ?? null);
  });

  readonly peRatio = computed(() => {
    this.profileStore._bump();
    const p = this.profileStore.profile(this.ticker);
    return fmtDec(p?.pe_ratio ?? null);
  });

  readonly eps = computed(() => {
    this.profileStore._bump();
    const p = this.profileStore.profile(this.ticker);
    return fmtDec(p?.eps ?? null);
  });

  readonly asOfLabel = computed(() => {
    this.profileStore._bump();
    return this.profileStore.profile(this.ticker)?.as_of ?? null;
  });

  load(): void {
    if (!this.ticker) return;
    this.profileStore.fetchProfile(this.ticker).subscribe();
    this.history.fetch(this.ticker, 60).subscribe(() => {
      this.historyLoadedOnce.set(true);
    });
  }
}
