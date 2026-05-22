import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, inject } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { PositionValuation } from '../../core/models/portfolio.model';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { TickerComponent } from '../shared/ticker.component';

@Component({
  selector: 'hf-positions-table',
  standalone: true,
  imports: [CommonModule, DatePipe, DecimalPipe, RouterLink, TickerComponent],
  template: `
    @if (!positions || positions.length === 0) {
      <p style="font-size:12px;color:var(--text-3);margin:0;padding:16px">
        No positions yet. Run an analysis and use “Add to portfolio” on the
        Run detail page, or click <b>Add position</b> above.
      </p>
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
              <td style="color:var(--text-2);font-size:12.5px">{{ nameFor(p.ticker) }}</td>
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
                    <span class="pill warn" style="margin-left:6px"><span class="dot"></span>stale</span>
                  }
                } @else {
                  <span style="color:var(--text-3)">—</span>
                }
              </td>
              <td class="num mono">{{ +p.market_value | number: '1.2-2' }}</td>
              <td class="num mono"
                  [style.color]="+p.unrealized_pnl > 0 ? 'var(--acc-long-fg)' : (+p.unrealized_pnl < 0 ? 'var(--acc-short-fg)' : null)">
                {{ +p.unrealized_pnl | number: '1.2-2' }}
                @if (p.unrealized_pnl_pct !== null) {
                  <span style="font-size:11px;opacity:0.8;margin-left:4px">({{ +p.unrealized_pnl_pct | number: '1.2-2' }}%)</span>
                }
              </td>
              <td class="num mono">{{ +p.weight_pct | number: '1.2-2' }}%</td>
              <td style="font-size:11.5px;color:var(--text-3)">
                @if (p.opened_via === 'run' && p.source_run_id !== null) {
                  <a [routerLink]="['/runs', p.source_run_id]" class="mono"
                     style="color:var(--acc-info-fg);text-decoration:underline">run #{{ p.source_run_id }}</a>
                } @else if (p.opened_via === 'strategy_cycle') {
                  <span>strategy cycle</span>
                } @else {
                  <span>manual</span>
                }
                <div style="font-size:10.5px">{{ p.opened_at | date: 'short' }}</div>
              </td>
              <td style="white-space:nowrap">
                <button class="btn ghost sm" (click)="closeClick.emit(p)">Close</button>
                <button class="btn ghost sm" (click)="editClick.emit(p)" style="margin-left:4px">Edit</button>
              </td>
            </tr>
            @if (p.warnings && p.warnings.length > 0) {
              <tr>
                <td colspan="11" style="padding:0 12px 6px 12px">
                  @for (w of p.warnings; track w) {
                    <span class="pill warn" style="margin-right:6px;font-size:11px">
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
