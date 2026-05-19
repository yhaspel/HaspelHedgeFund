import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';

@Component({
  selector: 'hf-strategies-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, DatePipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Your strategies</div>
          <h1 style="margin-top:6px">Strategies</h1>
          <p style="color:var(--text-2);font-size:13px;margin-top:6px;max-width:560px">
            Long-only, long/short, market-neutral, and short-only books. Click a row to open the book; hover to rerun.
          </p>
        </div>
        <div class="head-actions">
          <button class="btn"><svg width="12" height="12"><use href="/icons.svg#i-filter"/></svg> Filter</button>
          <button class="btn">Import</button>
          <a class="btn primary" routerLink="/strategies/new"><svg width="12" height="12"><use href="/icons.svg#i-plus"/></svg> New strategy</a>
        </div>
      </div>

      <section class="card" style="margin-bottom:14px">
        <div class="card-bd" style="display:flex;align-items:center;gap:14px">
          <input class="input sans" style="flex:1;max-width:320px" placeholder="Search strategies, universes…" [(ngModel)]="search" />
          <div class="seg" style="grid-template-columns:repeat(5,auto)">
            @for(k of kinds; track k){
              <button class="opt" [class.on]="kind()===k" (click)="kind.set(k)" style="padding:0 12px">{{ k }}</button>
            }
          </div>
          <span class="mono" style="margin-left:auto;font-size:11px;color:var(--text-3)">
            {{ store.strategies().length }} strategies
          </span>
        </div>
      </section>

      <section class="card">
        <table class="tbl">
          <thead><tr>
            <th style="width:32px"></th>
            <th>Name</th><th>Kind</th><th>Universe</th><th class="right">Top-k</th>
            <th>Gross · Net</th><th class="right">NAV</th><th class="right">YTD</th>
            <th>Last cycle</th><th>Status</th><th></th>
          </tr></thead>
          <tbody>
            @for(s of store.strategies(); track s.id){
              <tr style="cursor:pointer" (click)="open(s.id)">
                <td><input type="checkbox" style="accent-color:var(--acc-info)" (click)="$event.stopPropagation()" /></td>
                <td><a [routerLink]="['/strategies', s.id]" style="color:var(--text)">{{ s.name }}</a>
                  <div class="mono" style="font-size:10.5px;color:var(--text-3)">id {{ s.id }}</div></td>
                <td><span class="kind" [class.lo]="kindOf(s)==='LO'" [class.ls]="kindOf(s)==='LS'" [class.mn]="kindOf(s)==='MN'" [class.so]="kindOf(s)==='SO'">{{ kindOf(s) }}</span></td>
                <td style="color:var(--text-2)">{{ s.universe_name }}</td>
                <td class="num">{{ s.top_k_longs }} / {{ s.top_k_shorts }}</td>
                <td>
                  <div class="gnet">
                    <div class="track">
                      <div class="lng" [style.width]="grossNetLong(s)+'%'"></div>
                      <div class="sht" [style.width]="grossNetShort(s)+'%'"></div>
                      <div class="div"></div>
                    </div>
                    <span class="txt">{{ s.target_gross_pct }} · {{ s.target_net_pct }}</span>
                  </div>
                </td>
                <td class="num">$ {{ navOf(s) }}</td>
                <td class="num" [style.color]="(ytdOf(s) || 0) >= 0 ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  {{ (ytdOf(s) || 0) >= 0 ? '+' : '' }}{{ ytdOf(s) }}%
                </td>
                <td class="mono" style="font-size:11.5px;color:var(--text-3)">{{ s.last_run_at ? (s.last_run_at | date:'short') : '—' }}</td>
                <td><span class="pill ok"><span class="dot"></span>ACTIVE</span></td>
                <td><a [routerLink]="['/strategies', s.id]" class="btn ghost sm">Open</a></td>
              </tr>
            } @empty {
              <tr><td colspan="11" style="text-align:center;color:var(--text-3);padding:40px 0">
                No strategies yet. <a routerLink="/strategies/new" style="color:var(--acc-info-fg)">Create one →</a>
              </td></tr>
            }
          </tbody>
        </table>
      </section>
    </hf-app-shell>
  `,
})
export class StrategiesListPage implements OnInit {
  readonly store = inject(StrategiesStore);
  search = '';
  kind = signal('All');
  kinds = ['All', 'Long-only', 'L/S', 'Market-neutral', 'Short-only'];

  ngOnInit(): void { this.store.list().subscribe(); }

  open(id: string | number) { /* navigation via RouterLink */ }

  kindOf(s: any): 'LO' | 'LS' | 'MN' | 'SO' {
    const k = (s.kind || s.strategy_kind || '').toLowerCase();
    if (k.includes('short') && !k.includes('long')) return 'SO';
    if (k.includes('neutral')) return 'MN';
    if (k.includes('long') && k.includes('short')) return 'LS';
    return 'LO';
  }
  grossNetLong(s: any): number {
    return Math.min(100, Math.max(0, Number(s.target_gross_pct ?? 100) * 0.6));
  }
  grossNetShort(s: any): number {
    return Math.min(100, Math.max(0, Number(s.target_gross_pct ?? 0) * 0.4));
  }
  navOf(s: any): string { return s.nav_usd ? Number(s.nav_usd).toLocaleString() : '—'; }
  ytdOf(s: any): number { return Number(s.ytd_pct ?? 0); }
}
