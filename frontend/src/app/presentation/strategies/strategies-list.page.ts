import { Component, OnInit, inject } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';

@Component({
  selector: 'hf-strategies-list',
  standalone: true,
  imports: [CommonModule, RouterLink, DatePipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Your strategies</div>
          <h1 style="margin-top:6px">Strategies</h1>
        </div>
        <div class="head-actions">
          <a class="btn primary" routerLink="/strategies/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New strategy
          </a>
        </div>
      </div>

      <section class="card">
        @if (store.strategies().length === 0) {
          <div class="card-bd">
            <p style="font-size:13px;color:var(--text-3);margin:0">
              No strategies yet. Create one to start the autonomous long-short engine.
            </p>
          </div>
        } @else {
          <table class="tbl">
            <thead><tr>
              <th>Name</th>
              <th>Universe</th>
              <th>Gross / Net</th>
              <th>K longs/shorts</th>
              <th>Preset</th>
              <th>Last run</th>
              <th></th>
            </tr></thead>
            <tbody>
              @for (s of store.strategies(); track s.id) {
                <tr>
                  <td style="color:var(--text);font-weight:500">{{ s.name }}</td>
                  <td style="color:var(--text-2)">{{ s.universe_name }}</td>
                  <td class="mono">{{ s.target_gross_pct }} / {{ s.target_net_pct }}</td>
                  <td class="mono">{{ s.top_k_longs }} / {{ s.top_k_shorts }}</td>
                  <td>{{ s.model_preset }}</td>
                  <td class="mono" style="font-size:11.5px;color:var(--text-3)">
                    {{ s.last_run_at ? (s.last_run_at | date: 'short') : '—' }}
                  </td>
                  <td class="right">
                    <a [routerLink]="['/strategies', s.id]" style="color:var(--acc-info-fg);font-size:12px">Open</a>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </hf-app-shell>
  `,
})
export class StrategiesListPage implements OnInit {
  readonly store = inject(StrategiesStore);
  ngOnInit(): void { this.store.list().subscribe(); }
}
