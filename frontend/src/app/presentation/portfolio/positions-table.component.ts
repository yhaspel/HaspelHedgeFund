import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, inject } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { PositionValuation } from '../../core/models/portfolio.model';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { TickerComponent } from '../shared/ticker.component';

@Component({
  selector: 'hf-positions-table',
  standalone: true,
  imports: [CommonModule, DatePipe, DecimalPipe, RouterLink, EmptyStateComponent, TickerComponent],
  template: `
    @if (!positions || positions.length === 0) {
      <hf-empty-state
        message="No positions yet."
        detail='Run an analysis and use "Add to portfolio" on the Run detail page, or click "Add position" above.'>
      </hf-empty-state>
    } @else {
      <table class="tbl">
        <thead>
          <tr>
            <th scope="col">Ticker</th>
            <th scope="col">Name</th>
            <th scope="col">Side</th>
            <th scope="col" class="right">Qty</th>
            <th scope="col" class="right">Avg cost</th>
            <th scope="col" class="right">Mark</th>
            <th scope="col" class="right">Market value</th>
            <th scope="col" class="right">Unrealized P&amp;L</th>
            <th scope="col" class="right">Weight</th>
            <th scope="col">Source</th>
            <th scope="col"><span class="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          @for (p of positions; track p.id) {
            <tr>
              <td><hf-ticker [ticker]="p.ticker"></hf-ticker></td>
              <td class="text-text-2 text-[12.5px]">{{ nameFor(p.ticker) }}</td>
              <td>
                <span class="pill" [class.ok]="!p.is_short" [class.err]="p.is_short">
                  <span class="dot"></span>{{ p.is_short ? 'Short' : 'Long' }}
                </span>
              </td>
              <td class="num mono">{{ formatQty(p.quantity) }}</td>
              <td class="num mono">{{ +p.avg_cost | number: '1.2-4' }}</td>
              <td class="num mono">
                @if (p.mark_price !== null) {
                  {{ +p.mark_price | number: '1.2-4' }}
                  @if (p.mark_stale) {
                    <span class="pill warn ml-1.5"><span class="dot"></span>stale</span>
                  }
                } @else {
                  <span class="text-text-3">—</span>
                }
              </td>
              <td class="num mono">{{ +p.market_value | number: '1.2-2' }}</td>
              <td class="num mono"
                  [style.color]="+p.unrealized_pnl > 0 ? 'var(--acc-long-fg)' : (+p.unrealized_pnl < 0 ? 'var(--acc-short-fg)' : null)">
                {{ +p.unrealized_pnl | number: '1.2-2' }}
                @if (p.unrealized_pnl_pct !== null) {
                  <span class="text-[11px] opacity-80 ml-1">({{ +p.unrealized_pnl_pct | number: '1.2-2' }}%)</span>
                }
              </td>
              <td class="num mono">{{ +p.weight_pct | number: '1.2-2' }}%</td>
              <td class="text-[11.5px] text-text-3">
                @if (p.opened_via === 'run' && p.source_run_id !== null) {
                  <a [routerLink]="['/runs', p.source_run_id]" class="mono text-[var(--acc-info-fg)] underline">run #{{ p.source_run_id }}</a>
                } @else if (p.opened_via === 'strategy_cycle') {
                  <span>strategy cycle</span>
                } @else {
                  <span>manual</span>
                }
                <div class="text-[10.5px]">{{ p.opened_at | date: 'short' }}</div>
              </td>
              <td class="whitespace-nowrap">
                <button class="btn ghost sm" (click)="closeClick.emit(p)">Close</button>
                <button class="btn ghost sm ml-1" (click)="editClick.emit(p)">Edit</button>
              </td>
            </tr>
            @if (p.warnings && p.warnings.length > 0) {
              <tr>
                <td colspan="11" class="px-3 pb-1.5 pt-0">
                  @for (w of p.warnings; track w) {
                    <span class="pill warn mr-1.5 text-[11px]">
                      <span class="dot"></span>{{ w }}
                    </span>
                  }
                </td>
              </tr>
            }
          }
        </tbody>
      </table>
    }
  `,
})
export class PositionsTableComponent implements OnChanges {
  @Input() positions: PositionValuation[] = [];
  @Output() closeClick = new EventEmitter<PositionValuation>();
  @Output() editClick = new EventEmitter<PositionValuation>();

  private readonly profiles = inject(TickerProfileStore);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['positions']) {
      const tickers = [...new Set((this.positions ?? []).map((p) => p.ticker))];
      if (tickers.length) this.profiles.fetchNames(tickers).subscribe();
    }
  }

  nameFor(ticker: string): string {
    // _bump is read for reactivity within Angular's CD pass.
    void this.profiles._bump();
    return this.profiles.name(ticker) || '—';
  }

  formatQty(raw: string): string {
    const n = Number(raw);
    if (!Number.isFinite(n)) return raw;
    // Display as integer when the stored value is integral.
    if (Math.abs(n - Math.round(n)) < 1e-9) {
      return n.toFixed(0);
    }
    return n.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }
}
