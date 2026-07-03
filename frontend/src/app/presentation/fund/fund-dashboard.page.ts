import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FundStore } from '../../abstraction/fund.store';
import { MacroStore } from '../../abstraction/macro.store';
import { FundAccountCard, FundOverview } from '../../core/models/autopilot.model';
import { AppShellComponent } from '../shared/app-shell.component';
import { ConfirmService } from '../shared/confirm.service';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { PopoverComponent } from '../shared/popover.component';
import { RegimeStripComponent } from '../dashboard/regime-strip.component';
import { FundCompositeComponent } from './fund-composite.component';
import { FundHistoryComponent } from './fund-history.component';

// P7 §14 — the headline fund view: 3 account cards + aggregate panel +
// realized correlation matrix + the fund-level kill switch. Paper-only.
@Component({
  selector: 'hf-fund-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    AppShellComponent,
    EmptyStateComponent,
    PopoverComponent,
    RegimeStripComponent,
    FundCompositeComponent,
    FundHistoryComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Fund' }]">
      <div class="page-head">
        <div><h1>Autonomous Fund</h1></div>
        @if (fund(); as f) {
          <div class="head-actions">
            <span class="pill" [class.ok]="f.state === 'active'" [class.err]="f.state === 'halted'">
              <span class="dot"></span>{{ f.state }}
            </span>
            @if (f.state === 'active') {
              <button class="btn btn-danger" (click)="halt()" [disabled]="busy()">
                Halt all 3 accounts
              </button>
            }
            @if (f.state === 'halted') {
              <button class="btn" (click)="resumeFund()" [disabled]="busy()">
                Clear fund halt
              </button>
            }
          </div>
        }
      </div>

      <p class="disclaimer">Educational use only — not investment advice. Paper trading only.</p>

      @if (notLive()) {
        <div class="banner-warn">
          ⚠ This fund is <b>active</b> but no strategies are enabled — nothing will trade yet. Use
          <b>Set up</b> on a card below, or
          <a [routerLink]="setupLink()" class="banner-link">open a strategy’s Autopilot page</a>, to
          validate and enable it.
        </div>
      }

      @if (fund(); as f) {
        <!-- Aggregate panel -->
        <section class="card agg">
          <div class="kpi">
            <div class="kpi-label">Aggregate NAV</div>
            <div class="kpi-value">
              {{ +f.aggregate_nav | currency: 'USD' : 'symbol' : '1.0-0' }}
            </div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Fund DD halt</div>
            <div class="kpi-value">{{ f.fund_dd_halt_pct }}%</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Peak</div>
            <div class="kpi-value">
              {{ f.peak_equity ? (+f.peak_equity | currency: 'USD' : 'symbol' : '1.0-0') : '—' }}
            </div>
          </div>
        </section>

        <!-- Macro regime strip (HMM/Markov consensus + investment clock).
             Lived on the old manual dashboard; restored to the landing page
             when P10 §C1 made / the fund — the regime gate (P10 Part F) makes
             it fund-level context, not manual-book detail. -->
        <hf-regime-strip [snapshot]="macro.snapshot()" />

        <!-- 3 account cards -->
        <section class="cards">
          @for (a of f.per_account; track a) {
            <div class="card acct">
              <div class="acct-head">
                <a [routerLink]="['/strategies', a.strategy_id, 'autopilot']" class="acct-name">{{
                  a.name
                }}</a>
                <!-- Enabled: a plain status pill. -->
                @if (a.is_enabled) {
                  <span
                    class="pill"
                    [class.ok]="a.state === 'active'"
                    [class.warn]="a.state === 'soft_cut'"
                    [class.err]="a.state === 'halted'"
                    ><span class="dot"></span>{{ a.state }}</span
                  >
                }
                <!-- Disabled: the badge itself explains *why* it's off + the one next
                   step, on hover / focus / tap — so 'disabled' is never a dead end. -->
                @if (!a.is_enabled) {
                  <span class="pill-wrap">
                    <button
                      type="button"
                      class="pill info pill-btn"
                      (mouseenter)="pop.show()"
                      (mouseleave)="pop.maybeHide()"
                      (focus)="pop.show()"
                      (blur)="pop.maybeHide()"
                      (click)="pop.toggle()"
                      [attr.aria-label]="'Disabled — ' + hintFor(a)"
                      [attr.aria-describedby]="pop.open() ? pop.popoverId : null"
                    >
                      <span class="dot"></span>disabled<span class="pill-q" aria-hidden="true"
                        >?</span
                      >
                    </button>
                    <hf-popover
                      #pop
                      role="tooltip"
                      placement="bottom"
                      align="end"
                      [dismissOnOutsideClick]="true"
                    >
                      <span class="disabled-pop">
                        <b>Why “disabled”?</b>
                        {{ hintFor(a) }}
                      </span>
                    </hf-popover>
                  </span>
                }
              </div>
              <div class="acct-kind">{{ a.kind }}</div>
              <div class="acct-row">
                <span>NAV</span
                ><b>{{ a.nav ? (+a.nav | currency: 'USD' : 'symbol' : '1.0-0') : '—' }}</b>
              </div>
              <div class="acct-row">
                <span>Rolling Sharpe</span
                ><b>{{ a.rolling_sharpe !== null ? (a.rolling_sharpe | number: '1.2-2') : '—' }}</b>
              </div>
              <!-- Always a cadence; label it inactive while disabled so it doesn't imply an imminent fire. -->
              @if (a.cron_description) {
                <div class="acct-row">
                  <span>{{ a.is_enabled ? 'Schedule' : 'Cadence' }}</span>
                  <b class="sched" [title]="a.cron_description">{{ a.cron_description }}</b>
                </div>
              }
              <!-- Next run is meaningful only when enabled; otherwise say so plainly. -->
              @if (a.is_enabled) {
                <div class="acct-row">
                  <span>Next run</span
                  ><b>{{ a.next_run_at ? (a.next_run_at | date: 'EEE HH:mm') : '—' }}</b>
                </div>
              }
              @if (!a.is_enabled) {
                <div class="acct-row"><span>Next run</span><b class="muted">Not scheduled</b></div>
              }
              @if (a.is_enabled) {
                <div class="acct-actions">
                  <button
                    class="btn btn-sm"
                    (click)="runNow(a.strategy_id)"
                    [disabled]="runningId() === a.strategy_id"
                  >
                    {{ runningId() === a.strategy_id ? 'Queuing…' : 'Run now' }}
                  </button>
                  @if (queuedId() === a.strategy_id) {
                    <span class="queued">Cycle queued ✓</span>
                  }
                </div>
              }
              <!-- Disabled: the reason + the one next step, so the path is never a dead end. -->
              @if (!a.is_enabled) {
                <div class="acct-setup">
                  @if (a.setup_hint) {
                    <p class="setup-hint">{{ a.setup_hint }}</p>
                  }
                  <a
                    class="btn btn-sm btn-primary"
                    [routerLink]="['/strategies', a.strategy_id, 'autopilot']"
                  >
                    {{ a.can_enable ? 'Review & enable →' : 'Set up →' }}
                  </a>
                </div>
              }
              <!-- phase-09a — always-visible validation-backtest launcher (verb from
                 has_backtest); routes to the prefilled New Backtest page. -->
              <div class="acct-bt">
                <a
                  class="btn btn-sm"
                  [routerLink]="['/backtests/new']"
                  [queryParams]="{ strategy: a.strategy_id }"
                >
                  {{ a.has_backtest ? 'Re-run backtest →' : 'Run backtest →' }}
                </a>
              </div>
            </div>
          }
        </section>

        <!-- P10 §C2/§C4: live NAV history (TWR) vs SPY/QQQ. -->
        <hf-fund-history />

        <!-- P10 §B5: the validated composite — what the pods do TOGETHER. -->
        <hf-fund-composite />

        <!-- P10 §C1: the manual book, demoted to a secondary card. -->
        <section class="card manual-row">
          <div>
            <b>Manual book &amp; research desk</b>
            <span class="muted">
              — the hand-managed paper book and council research surfaces now live off the landing
              page.</span
            >
          </div>
          <div class="manual-actions">
            <a class="btn btn-sm" routerLink="/portfolio">Manual book →</a>
            <a class="btn btn-sm" routerLink="/dashboard">Manual dashboard →</a>
          </div>
        </section>

        <!-- Correlation matrix -->
        <section class="card">
          <h2>Realized cross-strategy correlation</h2>
          @if (!f.correlation.available) {
            <p class="muted">
              Insufficient data — need ≥ {{ f.correlation.min_sample }} weekly returns per account
              (measure, don't assume). Accounts 1 &amp; 2 are both equity, so expect their pairwise
              number to run high once available.
            </p>
          }
          @if (f.correlation.available && f.correlation.matrix; as m) {
            <table class="corr">
              <thead>
                <tr>
                  <th></th>
                  @for (col of cols(); track col) {
                    <th>{{ col }}</th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (row of cols(); track row) {
                  <tr>
                    <th>{{ row }}</th>
                    @for (col of cols(); track col) {
                      <td
                        [class.lo]="m[row][col] < 0.3"
                        [class.hi]="m[row][col] > 0.7 && row !== col"
                      >
                        {{ m[row][col] | number: '1.2-2' }}
                      </td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        @if (f.recommendations.length) {
          <section class="card">
            <h2>Recommendations</h2>
            <ul>
              @for (r of f.recommendations; track r) {
                <li>{{ r }}</li>
              }
            </ul>
          </section>
        }
      } @else {
        @if (!loading()) {
          <hf-empty-state
            message="No autonomous fund yet"
            detail="Run bootstrap_autonomous_fund to provision the 3-account fund."
          />
        }
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .agg {
        display: flex;
        gap: 32px;
        padding: 16px;
      }
      hf-regime-strip {
        display: block;
        margin-top: 12px;
      }
      /* These section cards hold content directly (no .card-bd), so the shell .card gives no inner padding — restore it. */
      .card:not(.agg):not(.acct) {
        padding: 16px;
      }
      .kpi-label {
        color: var(--text-3);
        font-size: 12px;
      }
      .kpi-value {
        font-size: 22px;
        font-weight: 600;
      }
      .cards {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 12px;
        margin: 12px 0;
      }
      .acct {
        padding: 14px;
      }
      .acct-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .pill-wrap {
        position: relative;
        display: inline-flex;
      }
      /* The disabled badge doubles as a help trigger — strip the button chrome so it
       still reads as a pill, then add the affordances (help cursor + a small "?"). */
      button.pill-btn {
        font-family: inherit;
        line-height: 1;
        cursor: help;
        -webkit-appearance: none;
        appearance: none;
      }
      button.pill-btn:focus-visible {
        outline: none;
        box-shadow: var(--focus-ring);
      }
      .pill-btn .pill-q {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 12px;
        height: 12px;
        margin-left: 1px;
        border-radius: var(--r-full);
        border: 1px solid currentColor;
        font-size: 8px;
        font-weight: 700;
        line-height: 1;
        opacity: 0.6;
      }
      .pill-btn:hover .pill-q,
      .pill-btn:focus-visible .pill-q {
        opacity: 1;
      }
      .disabled-pop {
        display: block;
        max-width: 240px;
      }
      .disabled-pop b {
        display: block;
        margin-bottom: 3px;
      }
      .acct-name {
        font-weight: 600;
      }
      .acct-kind {
        color: var(--text-3);
        font-size: 12px;
        margin: 2px 0 8px;
      }
      .acct-row {
        display: flex;
        justify-content: space-between;
        padding: 3px 0;
        border-top: 1px solid var(--border);
      }
      .acct-row .sched {
        font-size: 11px;
        font-weight: 500;
        text-align: right;
        max-width: 60%;
      }
      .acct-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 8px;
      }
      .btn-sm {
        padding: 2px 10px;
        font-size: 12px;
      }
      a.btn {
        text-decoration: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: fit-content;
      }
      .acct-actions .queued {
        color: var(--acc-long-fg);
        font-size: 12px;
      }
      .acct-row .muted {
        color: var(--text-3);
        font-weight: 500;
        font-size: 11px;
      }
      .acct-setup {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid var(--border);
      }
      .acct-setup .setup-hint {
        color: var(--text-3);
        font-size: 11.5px;
        margin: 0;
        line-height: 1.4;
      }
      .acct-bt {
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid var(--border);
      }
      .manual-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 12px 16px;
        margin: 12px 0;
        font-size: 12.5px;
      }
      .manual-row .muted {
        color: var(--text-3);
      }
      .manual-actions {
        display: flex;
        gap: 8px;
        flex: none;
      }
      .banner-warn {
        border: 1px solid var(--acc-short);
        border-radius: 6px;
        padding: 8px 12px;
        margin: 0 0 14px;
        font-size: 13px;
        color: var(--text-2);
        background: color-mix(in srgb, var(--acc-short-fg) 8%, transparent);
      }
      .banner-link {
        text-decoration: underline;
        color: var(--acc-info-fg);
      }
      table.corr {
        border-collapse: collapse;
      }
      table.corr th,
      table.corr td {
        padding: 6px 12px;
        text-align: center;
        border: 1px solid var(--border);
      }
      table.corr td.lo {
        color: var(--acc-long-fg);
        background: var(--acc-long-soft);
      }
      table.corr td.hi {
        color: var(--acc-short-fg);
        font-weight: 600;
        background: var(--acc-short-soft);
      }
      /* HHF-14: don't signal diversification risk by colour alone — a fill tint
       plus a glyph keeps the low/high read for colour-blind users. */
      table.corr td.lo::after {
        content: ' ▾';
      }
      table.corr td.hi::after {
        content: ' ▲';
      }
      .btn-danger {
        color: var(--acc-short-fg);
        border-color: var(--acc-short-fg);
      }
    `,
  ],
})
export class FundDashboardPage implements OnInit {
  private readonly store = inject(FundStore);
  private readonly confirm = inject(ConfirmService);
  readonly macro = inject(MacroStore);
  readonly fund = this.store.fund;
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly runningId = signal<number | null>(null);
  readonly queuedId = signal<number | null>(null);

  // The disabled-badge tooltip copy. Mirrors the backend `setup_hint`; falls back
  // to the validation-gate message when the API didn't send one.
  readonly defaultHint = 'Run a validation backtest to unlock the enable toggle.';

  hintFor(a: Pick<FundAccountCard, 'setup_hint'>): string {
    return a.setup_hint?.trim() || this.defaultHint;
  }

  readonly cols = computed(() => {
    const f: FundOverview | null = this.fund();
    const m = f?.correlation?.matrix;
    return m ? Object.keys(m) : [];
  });

  // "active" only means "not halted" — flag the case where the fund is active
  // but every account is disabled, so nothing is actually trading.
  readonly notLive = computed(() => {
    const f = this.fund();
    return !!f && f.state === 'active' && !f.is_live;
  });

  // Deep link the banner to the first account that still needs setup (else the
  // first account), so the warning is one click from the fix.
  readonly setupLink = computed(() => {
    const f = this.fund();
    const accts = f?.per_account ?? [];
    const target = accts.find((a) => !a.is_enabled) ?? accts[0];
    return target ? ['/strategies', target.strategy_id, 'autopilot'] : ['/fund'];
  });

  ngOnInit(): void {
    this.store
      .loadFund()
      .subscribe({ next: () => this.loading.set(false), error: () => this.loading.set(false) });
    if (!this.macro.snapshot()) {
      this.macro.loadSnapshot().subscribe({ error: () => {} });
    }
  }

  async halt(): Promise<void> {
    const n = this.fund()?.per_account.length ?? 0;
    const ok = await this.confirm.ask({
      title: n ? `Halt all ${n} accounts?` : 'Halt the fund?',
      body: 'Every autopilot stops immediately. Open positions are unaffected — nothing new will trade until you clear the halt.',
      confirmLabel: 'Halt fund',
      danger: true,
      requireText: 'HALT',
    });
    if (!ok) return;
    this.busy.set(true);
    this.store
      .haltFund()
      .subscribe({ next: () => this.busy.set(false), error: () => this.busy.set(false) });
  }

  resumeFund(): void {
    this.busy.set(true);
    this.store
      .resumeFund()
      .subscribe({ next: () => this.busy.set(false), error: () => this.busy.set(false) });
  }

  runNow(strategyId: number): void {
    this.runningId.set(strategyId);
    this.queuedId.set(null);
    this.store.runNow(strategyId).subscribe({
      next: () => {
        this.runningId.set(null);
        this.queuedId.set(strategyId);
      },
      error: () => this.runningId.set(null),
    });
  }
}
