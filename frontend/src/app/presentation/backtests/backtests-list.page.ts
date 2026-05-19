import { Component, OnInit, inject } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { BacktestsStore } from '../../abstraction/backtests.store';

@Component({
  selector: 'hf-backtests-list',
  standalone: true,
  imports: [CommonModule, RouterLink, DecimalPipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Walk-forward backtests</div>
          <h1 style="margin-top:6px">Backtests</h1>
        </div>
        <div class="head-actions">
          <a class="btn primary" routerLink="/backtests/new"><svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New walk-forward</a>
        </div>
      </div>

      <section class="card">
        <table class="tbl">
          <thead><tr>
            <th>Name</th><th>Status</th><th>Period</th>
            <th class="right">OOS return</th><th class="right">OOS Sharpe</th><th class="right">Deflation</th>
          </tr></thead>
          <tbody>
            @for(bt of store.list(); track bt.id){
              <tr>
                <td><a [routerLink]="['/backtests', bt.id]" style="color:var(--acc-info-fg)">{{ bt.name }}</a></td>
                <td>
                  <span class="pill"
                    [class.ok]="bt.status==='done'" [class.warn]="bt.status==='running' || bt.status==='queued'"
                    [class.err]="bt.status==='failed'"><span class="dot"></span>{{ bt.status }}
                    @if(bt.status==='running' || bt.status==='queued'){<span class="mono"> · {{ bt.progress_pct }}%</span>}
                  </span>
                </td>
                <td class="mono" style="color:var(--text-2)">{{ bt.start_date }} → {{ bt.end_date }}</td>
                <td class="num">{{ bt.total_return_pct !== null ? ((bt.total_return_pct | number:'1.2-2') + '%') : '—' }}</td>
                <td class="num">{{ bt.oos_sharpe !== null ? (bt.oos_sharpe | number:'1.2-2') : '—' }}</td>
                <td class="num"
                  [style.color]="bt.deflation !== null && bt.deflation < 0.3 ? 'var(--acc-short-fg)' : bt.deflation !== null && bt.deflation < 0.5 ? 'var(--acc-hold-fg)' : 'var(--acc-long-fg)'">
                  {{ bt.deflation !== null ? (bt.deflation | number:'1.2-2') : '—' }}
                </td>
              </tr>
            } @empty {
              <tr><td colspan="6" style="text-align:center;color:var(--text-3);padding:40px 0">No backtests yet — start your first walk-forward.</td></tr>
            }
          </tbody>
        </table>
      </section>
    </hf-app-shell>
  `,
})
export class BacktestsListPage implements OnInit {
  readonly store = inject(BacktestsStore);
  ngOnInit(): void { this.store.listBacktests().subscribe(); }
}
