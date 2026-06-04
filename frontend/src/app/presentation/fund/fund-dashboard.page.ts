import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FundStore } from '../../abstraction/fund.store';
import { FundOverview } from '../../core/models/autopilot.model';
import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';

// P7 §14 — the headline fund view: 3 account cards + aggregate panel +
// realized correlation matrix + the fund-level kill switch. Paper-only.
@Component({
  selector: 'hf-fund-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent, EmptyStateComponent],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Fund' }]">
      <div class="page-head">
        <div><h1>Autonomous Fund</h1></div>
        <div class="head-actions" *ngIf="fund() as f">
          <span class="pill" [class.ok]="f.state === 'active'" [class.err]="f.state === 'halted'">
            <span class="dot"></span>{{ f.state }}
          </span>
          <button
            *ngIf="f.state === 'active'"
            class="btn btn-danger"
            (click)="halt()"
            [disabled]="busy()"
          >Halt all 3 accounts</button>
          <button
            *ngIf="f.state === 'halted'"
            class="btn"
            (click)="resumeFund()"
            [disabled]="busy()"
          >Clear fund halt</button>
        </div>
      </div>

      <p class="disclaimer">Educational use only — not investment advice. Paper trading only.</p>

      <ng-container *ngIf="fund() as f; else noFund">
        <!-- Aggregate panel -->
        <section class="card agg">
          <div class="kpi">
            <div class="kpi-label">Aggregate NAV</div>
            <div class="kpi-value">{{ +f.aggregate_nav | currency: 'USD' : 'symbol' : '1.0-0' }}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Fund DD halt</div>
            <div class="kpi-value">{{ f.fund_dd_halt_pct }}%</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Peak</div>
            <div class="kpi-value">{{ f.peak_equity ? (+f.peak_equity | currency: 'USD' : 'symbol' : '1.0-0') : '—' }}</div>
          </div>
        </section>

        <!-- 3 account cards -->
        <section class="cards">
          <div class="card acct" *ngFor="let a of f.per_account">
            <div class="acct-head">
              <a [routerLink]="['/strategies', a.strategy_id, 'autopilot']" class="acct-name">{{ a.name }}</a>
              <span
                class="pill"
                [class.ok]="a.state === 'active'"
                [class.warn]="a.state === 'soft_cut'"
                [class.err]="a.state === 'halted'"
                [class.info]="!a.is_enabled"
              ><span class="dot"></span>{{ a.is_enabled ? a.state : 'disabled' }}</span>
            </div>
            <div class="acct-kind">{{ a.kind }}</div>
            <div class="acct-row"><span>NAV</span><b>{{ a.nav ? (+a.nav | currency: 'USD' : 'symbol' : '1.0-0') : '—' }}</b></div>
            <div class="acct-row"><span>Rolling Sharpe</span><b>{{ a.rolling_sharpe !== null ? (a.rolling_sharpe | number: '1.2-2') : '—' }}</b></div>
            <div class="acct-row"><span>Next run</span><b>{{ a.next_run_at ? (a.next_run_at | date: 'EEE HH:mm') : '—' }}</b></div>
          </div>
        </section>

        <!-- Correlation matrix -->
        <section class="card">
          <h2>Realized cross-strategy correlation</h2>
          <p class="muted" *ngIf="!f.correlation.available">
            Insufficient data — need ≥ {{ f.correlation.min_sample }} weekly returns per account
            (measure, don't assume). Accounts 1 &amp; 2 are both equity, so expect their pairwise
            number to run high once available.
          </p>
          <table class="corr" *ngIf="f.correlation.available && f.correlation.matrix as m">
            <thead>
              <tr><th></th><th *ngFor="let col of cols()">{{ col }}</th></tr>
            </thead>
            <tbody>
              <tr *ngFor="let row of cols()">
                <th>{{ row }}</th>
                <td
                  *ngFor="let col of cols()"
                  [class.lo]="m[row][col] < 0.3"
                  [class.hi]="m[row][col] > 0.7 && row !== col"
                >{{ m[row][col] | number: '1.2-2' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="card" *ngIf="f.recommendations.length">
          <h2>Recommendations</h2>
          <ul><li *ngFor="let r of f.recommendations">{{ r }}</li></ul>
        </section>
      </ng-container>

      <ng-template #noFund>
        <hf-empty-state
          *ngIf="!loading()"
          message="No autonomous fund yet"
          detail="Run bootstrap_autonomous_fund to provision the 3-account fund."
        />
      </ng-template>
    </hf-app-shell>
  `,
  styles: [`
    .disclaimer { color: var(--text-3); font-size: 12px; margin: 4px 0 14px; }
    .agg { display: flex; gap: 32px; }
    .kpi-label { color: var(--text-3); font-size: 12px; }
    .kpi-value { font-size: 22px; font-weight: 600; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 12px 0; }
    .acct-head { display: flex; justify-content: space-between; align-items: center; }
    .acct-name { font-weight: 600; }
    .acct-kind { color: var(--text-3); font-size: 12px; margin: 2px 0 8px; }
    .acct-row { display: flex; justify-content: space-between; padding: 3px 0; border-top: 1px solid var(--border); }
    table.corr { border-collapse: collapse; }
    table.corr th, table.corr td { padding: 6px 12px; text-align: center; border: 1px solid var(--border); }
    table.corr td.lo { color: var(--acc-long-fg); }
    table.corr td.hi { color: var(--acc-short-fg); font-weight: 600; }
    .btn-danger { color: var(--acc-short-fg); border-color: var(--acc-short-fg); }
    .muted { color: var(--text-3); }
  `],
})
export class FundDashboardPage implements OnInit {
  private readonly store = inject(FundStore);
  readonly fund = this.store.fund;
  readonly loading = signal(true);
  readonly busy = signal(false);

  readonly cols = computed(() => {
    const f: FundOverview | null = this.fund();
    const m = f?.correlation?.matrix;
    return m ? Object.keys(m) : [];
  });

  ngOnInit(): void {
    this.store.loadFund().subscribe({ next: () => this.loading.set(false), error: () => this.loading.set(false) });
  }

  halt(): void {
    if (!confirm('Halt all 3 accounts? This stops every autopilot at once.')) return;
    this.busy.set(true);
    this.store.haltFund().subscribe({ next: () => this.busy.set(false), error: () => this.busy.set(false) });
  }

  resumeFund(): void {
    this.busy.set(true);
    this.store.resumeFund().subscribe({ next: () => this.busy.set(false), error: () => this.busy.set(false) });
  }
}
