import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { ScreenResultRow } from '../../core/models/screener.model';
import { TickerComponent } from '../shared/ticker.component';
import { InfoTooltipComponent } from '../shared/info-tooltip.component';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';

interface ColumnDef {
  id: string;
  label: string;
  align: 'left' | 'right';
  sortable: boolean;
  tip?: string;
}

const COLUMNS: ColumnDef[] = [
  { id: 'ticker', label: 'Ticker', align: 'left', sortable: false },
  { id: 'name', label: 'Name', align: 'left', sortable: false },
  { id: 'price', label: 'Price', align: 'right', sortable: true },
  {
    id: 'change_pct',
    label: 'Change %',
    align: 'right',
    sortable: true,
    tip: "Today's percent move versus yesterday's close — includes the overnight gap plus intraday drift. Green = up, red = down.",
  },
  {
    id: 'gap_pct',
    label: 'Gap %',
    align: 'right',
    sortable: true,
    tip: "Today's open versus yesterday's close, in percent. Positive = gapped up (opened higher), negative = gapped down. Surfaces overnight catalysts.",
  },
  {
    id: 'rvol',
    label: 'RVOL',
    align: 'right',
    sortable: true,
    tip: "Relative Volume — today's volume divided by the 14-day average daily volume. 1.0× = normal, 2.0× = double the usual activity (often a news catalyst or breakout).",
  },
  {
    id: 'volume',
    label: 'Volume',
    align: 'right',
    sortable: true,
    tip: "Today's traded volume in shares (live). Higher volume means tighter spreads and easier fills.",
  },
  {
    id: 'adv_14d',
    label: 'ADV (14d)',
    align: 'right',
    sortable: true,
    tip: 'Average Daily Volume — mean daily share volume over the last 14 completed trading sessions. A baseline measure of liquidity.',
  },
  {
    id: 'market_cap',
    label: 'Market Cap',
    align: 'right',
    sortable: true,
    tip: 'Total market value of all outstanding shares, in USD (shown as M / B / T).',
  },
  {
    id: 'momentum_3m',
    label: '3m %',
    align: 'right',
    sortable: true,
    tip: '3-month price return — the percent change over the last ~63 trading sessions (about three months). A classic momentum signal. Green = up, red = down.',
  },
  { id: 'sector', label: 'Sector', align: 'left', sortable: false },
];

@Component({
  selector: 'hf-screener-results-table',
  standalone: true,
  imports: [CommonModule, TickerComponent, InfoTooltipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="tbl-wrap">
      <table class="tbl">
        <thead>
          <tr>
            @for (col of columns; track col.id) {
              <th
                scope="col"
                [class.right]="col.align === 'right'"
                [attr.aria-sort]="ariaSort(col.id)"
              >
                @if (col.sortable) {
                  <button
                    type="button"
                    class="sort-btn"
                    (click)="toggleSort(col.id)"
                  >
                    {{ col.label }}
                    @if (sortField() === col.id) {
                      <span class="caret" aria-hidden="true">{{ sortDir() === 'asc' ? '▲' : '▼' }}</span>
                    }
                  </button>
                } @else {
                  <span>{{ col.label }}</span>
                }
                @if (col.tip) {
                  <hf-info [text]="col.tip" />
                }
              </th>
            }
            <th scope="col" class="right">
              <span class="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          @for (row of sortedRows(); track row.ticker) {
            <tr>
              <td><hf-ticker [ticker]="row.ticker"></hf-ticker></td>
              <td class="text-text-2">{{ row.name || resolveName(row.ticker) }}</td>
              <td class="num mono">{{ formatDecimal(row.price, 2) }}</td>
              <td class="num mono" [class.long]="(row.change_pct ?? 0) > 0" [class.short]="(row.change_pct ?? 0) < 0">
                {{ formatNumber(row.change_pct, 2) }}%
              </td>
              <td class="num mono" [class.long]="(row.gap_pct ?? 0) > 0" [class.short]="(row.gap_pct ?? 0) < 0">
                {{ formatNumber(row.gap_pct, 2) }}%
              </td>
              <td class="num mono">{{ formatNumber(row.rvol, 2) }}</td>
              <td class="num mono">{{ formatInt(row.volume) }}</td>
              <td class="num mono">{{ formatInt(row.adv_14d) }}</td>
              <td class="num mono">{{ formatBig(row.market_cap) }}</td>
              <td class="num mono" [class.long]="(row.momentum_3m ?? 0) > 0" [class.short]="(row.momentum_3m ?? 0) < 0">
                {{ formatPct(row.momentum_3m) }}
              </td>
              <td>{{ row.sector }}</td>
              <td class="right action-cell">
                <button
                  type="button"
                  class="icon-btn star"
                  [class.starred]="row.in_watchlist"
                  [attr.aria-pressed]="row.in_watchlist"
                  [attr.aria-label]="row.in_watchlist ? 'Remove from watchlist' : 'Add to watchlist'"
                  (click)="toggleWatchlist.emit(row)"
                  title="Toggle watchlist"
                >★</button>
                <button
                  type="button"
                  class="btn ghost sm"
                  (click)="addToPortfolio.emit(row)"
                  aria-label="Add to Portfolio"
                >+ Book</button>
                <button
                  type="button"
                  class="btn ghost sm"
                  (click)="sendToRun.emit(row)"
                  aria-label="Send to New Run"
                >Analyze →</button>
              </td>
            </tr>
          }
        </tbody>
      </table>
    </div>
  `,
  styles: [
    `
      .tbl-wrap {
        overflow-x: auto;
      }
      thead th {
        white-space: nowrap;
      }
      .sort-btn {
        background: transparent;
        border: 0;
        color: var(--text-3);
        font: inherit;
        cursor: pointer;
        padding: 0;
        text-transform: inherit;
        letter-spacing: inherit;
      }
      .sort-btn:hover {
        color: var(--text);
      }
      .caret {
        margin-left: 4px;
        color: var(--text-2);
      }
      .num {
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      .long { color: var(--acc-long-fg); }
      .short { color: var(--acc-short-fg); }
      .action-cell {
        display: flex;
        gap: 4px;
        justify-content: flex-end;
        align-items: center;
      }
      .icon-btn.star {
        font-size: 14px;
        color: var(--text-3);
        background: transparent;
        border: 0;
        cursor: pointer;
        padding: 2px 6px;
        border-radius: var(--r-4);
      }
      .icon-btn.star.starred {
        color: var(--acc-hold-fg);
      }
      .icon-btn.star:hover {
        background: var(--hover);
      }
      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        border: 0;
      }
    `,
  ],
})
export class ResultsTableComponent {
  private readonly profileStore = inject(TickerProfileStore);

  @Input() set rows(value: ScreenResultRow[]) {
    this._rows.set(value || []);
    const missing = (value || [])
      .filter((r) => !r.name)
      .map((r) => r.ticker);
    if (missing.length) {
      this.profileStore.fetchNames(missing).subscribe();
    }
  }
  get rows(): ScreenResultRow[] {
    return this._rows();
  }

  @Input() set initialSort(value: { field: string; dir: 'asc' | 'desc' } | undefined) {
    if (value) {
      this._sortField.set(value.field);
      this._sortDir.set(value.dir);
    }
  }

  @Output() toggleWatchlist = new EventEmitter<ScreenResultRow>();
  @Output() addToPortfolio = new EventEmitter<ScreenResultRow>();
  @Output() sendToRun = new EventEmitter<ScreenResultRow>();

  readonly columns = COLUMNS;

  private readonly _rows = signal<ScreenResultRow[]>([]);
  private readonly _sortField = signal<string>('market_cap');
  private readonly _sortDir = signal<'asc' | 'desc'>('desc');

  readonly sortField = this._sortField.asReadonly();
  readonly sortDir = this._sortDir.asReadonly();

  readonly sortedRows = computed(() => {
    const rows = [...this._rows()];
    const field = this._sortField();
    const dir = this._sortDir() === 'desc' ? -1 : 1;
    rows.sort((a, b) => {
      const va = this.valueFor(a, field);
      const vb = this.valueFor(b, field);
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
    return rows;
  });

  toggleSort(field: string): void {
    if (this._sortField() === field) {
      this._sortDir.update((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      this._sortField.set(field);
      this._sortDir.set('desc');
    }
  }

  ariaSort(id: string): 'ascending' | 'descending' | 'none' {
    if (this._sortField() !== id) return 'none';
    return this._sortDir() === 'asc' ? 'ascending' : 'descending';
  }

  private valueFor(r: ScreenResultRow, field: string): number | string | null {
    const v = (r as any)[field];
    if (v === null || v === undefined) return null;
    if (typeof v === 'string') {
      const n = Number(v);
      return Number.isNaN(n) ? v : n;
    }
    return v as number;
  }

  resolveName(ticker: string): string {
    return this.profileStore.name(ticker) || '—';
  }

  formatDecimal(v: string | null, _digits = 2): string {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  formatNumber(v: number | null, digits = 2): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return v.toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  formatInt(v: number | null): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return Math.round(v).toLocaleString('en-US');
  }

  formatPct(v: number | null): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return (v * 100).toLocaleString('en-US', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }) + '%';
  }

  formatBig(v: string | null): string {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    return n.toLocaleString('en-US');
  }
}
