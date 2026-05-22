import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { PositionValuation } from '../../core/models/portfolio.model';

@Component({
  selector: 'hf-positions-table',
  standalone: true,
  imports: [CommonModule, DatePipe, DecimalPipe],
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
            <th>Ticker</th>
            <th>Side</th>
            <th class="right">Qty</th>
            <th class="right">Avg cost</th>
            <th class="right">Mark</th>
            <th class="right">Market value</th>
            <th class="right">Unrealized P&amp;L</th>
            <th class="right">Weight</th>
            <th>Source</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (p of positions; track p.id) {
            <tr>
              <td class="mono"><b>{{ p.ticker }}</b></td>
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
                  <a [href]="'/runs/' + p.source_run_id" class="mono"
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
                <td colspan="10" style="padding:0 12px 6px 12px">
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
export class PositionsTableComponent {
  @Input() positions: PositionValuation[] = [];
  @Output() closeClick = new EventEmitter<PositionValuation>();
  @Output() editClick = new EventEmitter<PositionValuation>();

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
