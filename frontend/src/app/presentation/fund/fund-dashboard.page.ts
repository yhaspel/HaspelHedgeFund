import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { FundStore } from '../../abstraction/fund.store';
import { MacroStore } from '../../abstraction/macro.store';
import { FundMemberCard, FundOverview } from '../../core/models/autopilot.model';
import { AppShellComponent } from '../shared/app-shell.component';
import { ConfirmService } from '../shared/confirm.service';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { ErrorStateComponent } from '../shared/error-state.component';
import { isOverdue, nextRunLabel, scheduleWithZone } from '../shared/schedule-format';
import { apiErrorMessage } from '../../core/api/api-error';
import { PopoverComponent } from '../shared/popover.component';
import { RegimeStripComponent } from '../dashboard/regime-strip.component';
import { strategyKindLabel } from '../../core/models/strategy.model';
import { FundActivityComponent } from './fund-activity.component';
import { FundCompositeComponent } from './fund-composite.component';
import { FundHistoryComponent } from './fund-history.component';
import { FundMembersEditorComponent } from './fund-members-editor.component';
import { FundSettingsComponent } from './fund-settings.component';
import { SchedulerHealthComponent } from './scheduler-health.component';

// P7 §14 / P14 — the headline fund view AND the one place the fund is managed:
// the shared paper account (settings), the member strategies + their share of
// the pool (roster editor), one card per member sleeve, the aggregate panel,
// reset / flatten, the realized correlation matrix and the fund-level kill
// switch. Paper-only.
@Component({
  selector: 'hf-fund-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    AppShellComponent,
    EmptyStateComponent,
    ErrorStateComponent,
    PopoverComponent,
    RegimeStripComponent,
    FundActivityComponent,
    FundCompositeComponent,
    FundHistoryComponent,
    FundMembersEditorComponent,
    FundSettingsComponent,
    SchedulerHealthComponent,
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
              <button
                class="btn btn-danger"
                (click)="halt()"
                [disabled]="busy() || !f.members_count"
                [title]="f.members_count ? '' : 'No strategies in the fund yet'"
              >
                {{ haltLabel() }}
              </button>
            }
            @if (f.state === 'halted') {
              <button class="btn" (click)="resumeFund()" [disabled]="busy()">
                Clear fund halt
              </button>
            }
            <button
              class="btn"
              (click)="toggleManage()"
              [attr.aria-expanded]="manageOpen()"
              aria-controls="fund-manage"
            >
              {{ manageOpen() ? 'Hide settings' : 'Manage fund' }}
            </button>
          </div>
        }
      </div>

      <p class="disclaimer">Educational use only — not investment advice. Paper trading only.</p>

      <!-- The fetch failed: say so and offer a retry. NEVER the create flow —
           a 5xx must not invite the owner of a live fund to build a new one. -->
      @if (!fund() && loadError()) {
        <hf-error-state
          title="Couldn't load your fund"
          [detail]="loadError()"
          (retry)="loadFund()"
        ></hf-error-state>
      }

      <!-- No fund yet (a SUCCESSFUL {fund: null}): the set-up flow IS the page. -->
      @if (!fund() && !loading() && !loadError() && loaded()) {
        <hf-empty-state
          message="No autonomous fund yet"
          detail="Choose the one paper account the fund will trade, then pick the strategies that share its pool."
        />
        <hf-fund-settings [fund]="null" (saved$)="onSaved($event)" />
      }

      @if (fund(); as f) {
        @if (!f.is_configured) {
          <div class="banner-warn" role="status">
            ⚠ The fund has <b>no paper account yet</b> — nothing can trade. Choose the shared
            account under <b>Manage fund</b>, add your strategies, then <b>Reset</b> to split its
            cash between them.
          </div>
        } @else if (notLive()) {
          <div class="banner-warn" role="status">
            ⚠ This fund is <b>active</b> but no strategies are enabled — nothing will trade yet.
            @if (f.members_count) {
              Use <b>Set up</b> on a card below, or
              <a [routerLink]="setupLink()" class="banner-link">open a strategy’s panel</a>, to
              validate and enable it.
            } @else {
              Add strategies under <b>Manage fund</b> to give the pool something to trade.
            }
          </div>
        }

        <!-- Aggregate panel: the shared ACCOUNT is the truth. -->
        <section class="card agg">
          <div class="kpi">
            <div class="kpi-label">{{ f.is_configured ? 'Account NAV' : 'Aggregate NAV' }}</div>
            <div class="kpi-value">
              {{ +f.aggregate_nav | currency: 'USD' : 'symbol' : '1.0-0' }}
            </div>
          </div>
          @if (f.broker_account; as acc) {
            <div class="kpi">
              <div class="kpi-label">Cash</div>
              <div class="kpi-value">{{ +acc.cash | currency: 'USD' : 'symbol' : '1.0-0' }}</div>
            </div>
          }
          <div class="kpi">
            <div class="kpi-label">Drawdown</div>
            <div class="kpi-value" [class.dd-breach]="ddBreached()">
              {{ f.drawdown_pct !== null ? (f.drawdown_pct | number: '1.1-1') + '%' : '—' }}
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
          @if (unallocated() !== null) {
            <div class="kpi">
              <div class="kpi-label">Unallocated</div>
              <div class="kpi-value" [class.muted-val]="unallocated() === 0">
                {{ unallocated()! | currency: 'USD' : 'symbol' : '1.0-0' }}
              </div>
            </div>
          }
        </section>

        <!-- The shared account row. -->
        @if (f.broker_account; as acc) {
          <section class="card acct-row">
            <div>
              <b>Trading account:</b> {{ acc.label }}
              <span class="muted">
                · {{ acc.broker_display }} · {{ acc.mode }} ·
                <span
                  class="pill"
                  [class.ok]="acc.connection_status === 'active'"
                  [class.err]="acc.connection_status !== 'active'"
                  ><span class="dot"></span>{{ acc.connection_status }}</span
                >
                · {{ acc.positions_count }} position{{ acc.positions_count === 1 ? '' : 's' }}
                @if (f.inflight_orders) {
                  · {{ f.inflight_orders }} order{{ f.inflight_orders === 1 ? '' : 's' }} in flight
                }
              </span>
            </div>
            <div class="manual-actions">
              <a class="btn btn-sm" [routerLink]="['/broker-accounts', acc.id]">Account →</a>
            </div>
          </section>
        }

        <!-- Macro regime strip (HMM/Markov consensus + investment clock). -->
        <hf-regime-strip [snapshot]="macro.snapshot()" />

        <!-- Manage: settings + roster + reset. Open by default until the fund is configured
             and has members. -->
        @if (manageOpen()) {
          <div id="fund-manage" class="manage">
            <hf-fund-settings [fund]="f" (saved$)="onSaved($event)" />
            <hf-fund-members-editor [fund]="f" (saved$)="onSaved($event)" />

            <!-- Reset / flatten: the fresh-start controls. -->
            <section class="card reset-card">
              <h2>Fresh start</h2>
              <p class="muted lead">
                <b>Reset</b> splits the account's cash between the members by their share and
                re-arms every drawdown breaker from today. It needs a <b>flat</b> account — use
                <b>Flatten</b> first to queue closing orders for every position (or reset the paper
                account on the broker's side to a clean $100k and Sync).
              </p>
              <div class="reset-status" role="status">
                @if (f.reset.ready) {
                  <span class="pill ok"><span class="dot"></span>ready to reset</span>
                  <span class="muted"
                    >Account is flat; {{ f.members_count }} member{{
                      f.members_count === 1 ? '' : 's'
                    }}
                    will be funded by their share.</span
                  >
                } @else {
                  <span class="pill warn"><span class="dot"></span>not ready</span>
                  <span class="muted">{{ resetReason() }}</span>
                }
              </div>
              <div class="reset-actions">
                <button
                  class="btn"
                  (click)="flatten()"
                  [disabled]="busy() || !f.is_configured || !f.broker_account?.positions_count"
                  [title]="f.broker_account?.positions_count ? '' : 'Nothing to flatten'"
                >
                  Flatten account
                </button>
                <button class="btn primary" (click)="reset()" [disabled]="busy() || !f.reset.ready">
                  Reset fund
                </button>
                @if (actionNote()) {
                  <span class="action-note">{{ actionNote() }}</span>
                }
                @if (actionError()) {
                  <span class="action-error" role="alert">{{ actionError() }}</span>
                }
              </div>
            </section>
          </div>
        }

        <!-- Member cards: one per sleeve. -->
        <div class="members-head">
          <h2>Strategies · {{ f.members_count }}</h2>
          @if (!manageOpen()) {
            <button class="btn btn-sm" (click)="toggleManage()">Add / change strategies</button>
          }
        </div>
        @if (!f.members_count) {
          <p class="muted empty-members">
            No strategies in the fund yet — pick them under <b>Manage fund</b>.
          </p>
        }
        <section class="cards">
          @for (a of f.members; track a.strategy_id) {
            <div class="card acct">
              <div class="acct-head">
                <a [routerLink]="['/fund/strategies', a.strategy_id]" class="acct-name">{{
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
              <div class="acct-kind" [attr.data-test]="'member-kind-' + a.strategy_id">
                {{ kindLabel(a.kind) }} ·
                <b>{{ +a.allocation_pct | number: '1.0-2' }}%</b> of the pool
              </div>
              <div class="acct-row">
                <span>Sleeve NAV</span
                ><b>{{ a.nav ? (+a.nav | currency: 'USD' : 'symbol' : '1.0-0') : '—' }}</b>
              </div>
              <div class="acct-row">
                <span>Capital / P&amp;L</span
                ><b>
                  {{ +a.initial_capital | currency: 'USD' : 'symbol' : '1.0-0' }}
                  @if (a.pnl_pct !== null) {
                    <span class="pnl" [class.up]="a.pnl_pct > 0" [class.down]="a.pnl_pct < 0"
                      >{{ a.pnl_pct > 0 ? '+' : '' }}{{ a.pnl_pct | number: '1.1-1' }}%</span
                    >
                  }
                </b>
              </div>
              <div class="acct-row">
                <span>Cash · positions</span
                ><b
                  >{{ +a.cash | currency: 'USD' : 'symbol' : '1.0-0' }} · {{ a.positions_count }}</b
                >
              </div>
              <div class="acct-row">
                <span>Rolling Sharpe</span
                ><b>{{ a.rolling_sharpe !== null ? (a.rolling_sharpe | number: '1.2-2') : '—' }}</b>
              </div>
              <!-- Always a cadence; label it inactive while disabled so it doesn't imply an imminent fire.
                   The cron text is the AUTOPILOT's local time, so its zone is spelled out. -->
              @if (a.cron_description) {
                <div class="acct-row">
                  <span>{{ a.is_enabled ? 'Schedule' : 'Cadence' }}</span>
                  <b class="sched" [title]="memberSchedule(a)">{{ memberSchedule(a) }}</b>
                </div>
              }
              <!-- Next run is meaningful only when enabled; otherwise say so plainly.
                   Rendered in the same zone as the cadence above, with a date, and
                   flagged when the scheduler has already missed it. -->
              @if (a.is_enabled) {
                <div class="acct-row">
                  <span>Next run</span
                  ><b data-test="member-next-run" [class.overdue]="memberOverdue(a)"
                    >{{ a.next_run_at ? memberNextRun(a) : '—' }}</b
                  >
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
                  <button
                    class="btn btn-sm"
                    (click)="disable(a)"
                    [disabled]="memberBusy() === a.strategy_id"
                  >
                    Disable
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
                  <div class="acct-actions">
                    @if (a.can_enable) {
                      <button
                        class="btn btn-sm primary"
                        (click)="enable(a)"
                        [disabled]="memberBusy() === a.strategy_id"
                      >
                        {{ memberBusy() === a.strategy_id ? 'Enabling…' : 'Enable' }}
                      </button>
                    }
                    <a
                      class="btn btn-sm"
                      [class.btn-primary]="!a.can_enable"
                      [routerLink]="['/fund/strategies', a.strategy_id]"
                    >
                      {{ a.can_enable ? 'Review →' : 'Set up →' }}
                    </a>
                  </div>
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

        @if (f.leaving.length) {
          <p class="muted leaving" role="status">
            Leaving the fund (closing orders in progress):
            @for (l of f.leaving; track l.strategy_id) {
              <b>{{ l.name }}</b> ({{ l.positions_count }} position{{
                l.positions_count === 1 ? '' : 's'
              }})
            }
          </p>
        }

        <!-- WAVE 3 item 3: is the scheduler dispatching, and what has the fund
             actually done? A dead beat used to look exactly like a quiet week. -->
        <hf-scheduler-health class="block mb-[18px]" />
        <hf-fund-activity class="block mb-[18px]" />

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
              Insufficient data — need ≥ {{ f.correlation.min_sample }} weekly returns per strategy
              (measure, don't assume). Two equity strategies will run a high pairwise number once
              available — that is information, not a bug.
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
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .agg {
        display: flex;
        gap: 32px;
        padding: 16px;
        flex-wrap: wrap;
      }
      hf-regime-strip {
        display: block;
        margin-top: 12px;
      }
      /* These section cards hold content directly (no .card-bd), so the shell .card gives no inner padding — restore it. */
      .card:not(.agg):not(.acct):not(.acct-row):not(.manual-row) {
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
      .kpi-value.muted-val {
        color: var(--text-3);
      }
      .acct-row.card,
      .acct-row {
        font-size: 12.5px;
      }
      /* A next_run_at in the past means the scheduler missed the fire. */
      .acct-row b.overdue {
        color: var(--acc-short-fg);
      }
      section.acct-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 10px 16px;
        margin: 12px 0 0;
        border-top: none;
      }
      section.acct-row .pill {
        vertical-align: middle;
      }
      .members-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin: 18px 0 4px;
      }
      .members-head h2,
      .reset-card > h2 {
        font-size: var(--fs-11);
        line-height: 16px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--text-3);
        font-weight: 500;
        margin: 0;
      }
      .reset-card > h2 {
        margin-bottom: 8px;
      }
      .reset-card .lead {
        font-size: 12.5px;
        margin: 0 0 10px;
        max-width: 78ch;
      }
      .reset-status {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 12.5px;
        margin-bottom: 10px;
        flex-wrap: wrap;
      }
      .reset-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
      .action-note {
        color: var(--acc-long-fg);
        font-size: 12px;
      }
      .action-error {
        color: var(--acc-short-fg);
        font-size: 12px;
      }
      .manage {
        margin-top: 12px;
      }
      .empty-members {
        margin: 6px 0 12px;
        font-size: 12.5px;
      }
      .leaving {
        font-size: 12.5px;
        margin: 0 0 12px;
      }
      .cards {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 12px;
        margin: 8px 0 12px;
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
      .acct-kind b {
        color: var(--text-2);
      }
      .acct .acct-row {
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
      .pnl {
        font-size: 11px;
        margin-left: 4px;
        color: var(--text-3);
      }
      .pnl.up {
        color: var(--acc-long-fg);
      }
      .pnl.down {
        color: var(--acc-short-fg);
      }
      .acct-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 8px;
        flex-wrap: wrap;
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
      .acct-setup .acct-actions {
        margin-top: 0;
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
      .muted {
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
      .kpi-value.dd-breach {
        color: var(--acc-short-fg);
      }
    `,
  ],
})
export class FundDashboardPage implements OnInit {
  private readonly store = inject(FundStore);

  /** WAVE 3 — `trend` / `sector_momentum` / `news_sentiment` used to render as
   *  raw slugs on member cards because the frontend kind list was stale. */
  readonly kindLabel = strategyKindLabel;
  private readonly confirm = inject(ConfirmService);
  readonly macro = inject(MacroStore);
  readonly fund = this.store.fund;
  readonly loading = signal(true);
  /** GET /fund/ failed. Distinct from "the user has no fund". */
  readonly loadError = signal<string | null>(null);
  /** GET /fund/ succeeded at least once, so `fund() === null` really means
   *  "no fund yet" and the create flow is the right thing to show. */
  readonly loaded = signal(false);
  readonly busy = signal(false);
  readonly runningId = signal<number | null>(null);
  readonly queuedId = signal<number | null>(null);
  readonly memberBusy = signal<number | null>(null);
  readonly actionNote = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  // The manage drawer (settings + roster + reset). Opens itself while the fund
  // still needs setting up; the user toggles it afterwards.
  private readonly manageToggled = signal<boolean | null>(null);
  readonly manageOpen = computed(() => {
    const t = this.manageToggled();
    if (t !== null) return t;
    const f = this.fund();
    return !!f && (!f.is_configured || f.members_count === 0);
  });

  // The disabled-badge tooltip copy. Mirrors the backend `setup_hint`; falls back
  // to the validation-gate message when the API didn't send one.
  readonly defaultHint = 'Run a validation backtest to unlock the enable toggle.';

  hintFor(a: Pick<FundMemberCard, 'setup_hint'>): string {
    return a.setup_hint?.trim() || this.defaultHint;
  }

  readonly cols = computed(() => {
    const f: FundOverview | null = this.fund();
    const m = f?.correlation?.matrix;
    return m ? Object.keys(m) : [];
  });

  // "active" only means "not halted" — flag the case where the fund is active
  // but every member is disabled, so nothing is actually trading.
  readonly notLive = computed(() => {
    const f = this.fund();
    return !!f && f.state === 'active' && !f.is_live;
  });

  // Paint the drawdown KPI red once it's at/past the halt limit.
  readonly ddBreached = computed(() => {
    const f = this.fund();
    return !!f && f.drawdown_pct !== null && f.drawdown_pct >= +f.fund_dd_halt_pct;
  });

  // The kill switch names what it stops — the live member count, never a
  // hard-coded "3" (the fund had shrunk to 2 accounts while the label said 3).
  readonly haltLabel = computed(() => {
    const n = this.fund()?.members_count ?? 0;
    return n ? `Halt all ${n} strateg${n === 1 ? 'y' : 'ies'}` : 'Halt fund';
  });

  // Account NAV − Σ sleeve NAV (null while unconfigured).
  readonly unallocated = computed(() => {
    const f = this.fund();
    if (!f || f.unallocated_nav === null) return null;
    return Math.round(+f.unallocated_nav);
  });

  readonly resetReason = computed(() => {
    const f = this.fund();
    switch (f?.reset.reason) {
      case 'no_account':
        return 'Choose the fund’s paper account first.';
      case 'positions':
        return `The account still holds ${f!.reset.positions} position${
          f!.reset.positions === 1 ? '' : 's'
        } — flatten first.`;
      case 'inflight_orders':
        return `${f!.reset.inflight_orders} order${
          f!.reset.inflight_orders === 1 ? ' is' : 's are'
        } still in flight — wait for them to settle.`;
      case 'no_members':
        return 'Add at least one strategy first.';
      default:
        return '';
    }
  });

  // Deep link the banner to the first member that still needs setup (else the
  // first member), so the warning is one click from the fix.
  readonly setupLink = computed(() => {
    const f = this.fund();
    const members = f?.members ?? [];
    const target = members.find((a) => !a.is_enabled) ?? members[0];
    return target ? ['/fund/strategies', target.strategy_id] : ['/fund'];
  });

  ngOnInit(): void {
    this.loadFund();
    if (!this.macro.snapshot()) {
      this.macro.loadSnapshot().subscribe({ error: () => {} });
    }
  }

  /**
   * A failed GET /fund/ used to be indistinguishable from "no fund exists":
   * the template branched on `!fund() && !loading()` and showed the create
   * flow, so a 503 (or an expired session) invited the owner of a live fund to
   * set one up from scratch. Track the failure separately and only offer the
   * create flow on a successful `{fund: null}`.
   */
  loadFund(): void {
    this.loading.set(true);
    this.loadError.set(null);
    this.store.loadFund().subscribe({
      next: () => {
        this.loading.set(false);
        this.loaded.set(true);
      },
      error: (e: unknown) => {
        this.loading.set(false);
        this.loadError.set(apiErrorMessage(e, 'Could not load your fund.'));
      },
    });
  }

  toggleManage(): void {
    this.manageToggled.set(!this.manageOpen());
  }

  onSaved(_f: FundOverview): void {
    // The store already holds the fresh overview; refresh dependents that read
    // their own endpoints (history, composite) lazily on their next load.
    this.actionError.set(null);
  }

  async halt(): Promise<void> {
    const n = this.fund()?.members_count ?? 0;
    const ok = await this.confirm.ask({
      title: n ? `Halt all ${n} strateg${n === 1 ? 'y' : 'ies'}?` : 'Halt the fund?',
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

  // Clearing a drawdown halt is an acknowledgment, not a retry: the backend
  // rebases the fund + sleeve peaks to current equity so the breakers re-arm
  // from today's level (otherwise the stale peak would re-halt on the next
  // sweep, forever). Spell that out before acting.
  async resumeFund(): Promise<void> {
    const f = this.fund();
    const limit = f ? +f.fund_dd_halt_pct : 0;
    const ok = await this.confirm.ask({
      title: 'Clear halt & re-arm breakers?',
      body:
        'Resuming acknowledges the drawdown: the fund and sleeve peaks reset to current ' +
        'equity, all strategies resume on their schedules, and the drawdown breakers re-arm' +
        (limit ? ` — a fresh ${limit}% drawdown from here halts again.` : '.'),
      confirmLabel: 'Clear & re-arm',
    });
    if (!ok) return;
    this.busy.set(true);
    this.store
      .resumeFund()
      .subscribe({ next: () => this.busy.set(false), error: () => this.busy.set(false) });
  }

  // Fresh start: the account's cash is split by allocation; every breaker re-arms.
  async reset(): Promise<void> {
    const f = this.fund();
    if (!f) return;
    const ok = await this.confirm.ask({
      title: 'Reset the fund?',
      body:
        `The account's cash (${this.money(f.broker_account?.cash)}) is split between the ` +
        `${f.members_count} member strateg${f.members_count === 1 ? 'y' : 'ies'} by their share, ` +
        'every drawdown peak is rebased to today, and the fund halt (if any) is cleared. ' +
        'Enabled strategies start trading their fresh slice on their next scheduled run.',
      confirmLabel: 'Reset fund',
      danger: true,
      requireText: 'RESET',
    });
    if (!ok) return;
    this.runAction(this.store.resetFund(), 'Fund reset — sleeves funded by their share.');
  }

  // Queue closing orders for every position in the shared account.
  async flatten(): Promise<void> {
    const f = this.fund();
    if (!f) return;
    const n = f.broker_account?.positions_count ?? 0;
    const ok = await this.confirm.ask({
      title: `Flatten ${n} position${n === 1 ? '' : 's'}?`,
      body:
        'Closing orders are queued for every position in the account (held for the next open if ' +
        'the market is closed) and attributed to the strategies that hold them. Once the account ' +
        'is flat you can Reset.',
      confirmLabel: 'Flatten account',
      danger: true,
    });
    if (!ok) return;
    this.runAction(this.store.flattenFund(), 'Closing orders queued.');
  }

  enable(a: FundMemberCard): void {
    this.memberBusy.set(a.strategy_id);
    this.store.enable(a.strategy_id).subscribe({
      next: () => this.reloadAfterMember(),
      error: (e) => {
        this.memberBusy.set(null);
        this.actionError.set(e?.error?.detail ?? `Could not enable ${a.name}.`);
      },
    });
  }

  async disable(a: FundMemberCard): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Disable ${a.name}?`,
      body: 'Its autopilot stops firing; its open positions are left as they are.',
      confirmLabel: 'Disable',
    });
    if (!ok) return;
    this.memberBusy.set(a.strategy_id);
    this.store.disable(a.strategy_id).subscribe({
      next: () => this.reloadAfterMember(),
      error: (e) => {
        this.memberBusy.set(null);
        this.actionError.set(e?.error?.detail ?? `Could not disable ${a.name}.`);
      },
    });
  }

  private reloadAfterMember(): void {
    this.store.loadFund().subscribe({
      next: () => this.memberBusy.set(null),
      error: () => this.memberBusy.set(null),
    });
  }

  private runAction(obs: Observable<unknown>, note: string): void {
    this.actionNote.set(null);
    this.actionError.set(null);
    this.busy.set(true);
    obs.subscribe({
      next: () => {
        this.busy.set(false);
        this.actionNote.set(note);
        setTimeout(() => this.actionNote.set(null), 4000);
      },
      error: (e: { error?: { detail?: string } }) => {
        this.busy.set(false);
        this.actionError.set(e?.error?.detail ?? 'The action failed.');
      },
    });
  }

  private money(v: string | undefined | null): string {
    const n = v ? +v : 0;
    return n.toLocaleString('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    });
  }

  /**
   * "Run now" on a member card fires a LIVE autopilot cycle: the council runs
   * and the resulting orders go straight to the fund's paper brokerage account.
   * Every other live action on this page (halt / resume / reset / flatten /
   * disable) is confirm-gated; this one was not, and a refusal (409 "autopilot
   * halted") was swallowed entirely.
   */
  async runNow(strategyId: number): Promise<void> {
    if (this.runningId() !== null) return;
    const name = this.fund()?.members.find((m) => m.strategy_id === strategyId)?.name;
    const ok = await this.confirm.ask({
      title: name ? `Run ${name} now?` : 'Run this strategy now?',
      body:
        'This runs the council immediately, outside the schedule, and submits the resulting '
        + 'orders to the fund’s paper brokerage account with no further confirmation. '
        + 'It also bills the LLM cost to your account.',
      confirmLabel: 'Run now and submit orders',
    });
    if (!ok) return;
    this.runningId.set(strategyId);
    this.queuedId.set(null);
    this.actionError.set(null);
    this.store.runNow(strategyId).subscribe({
      next: () => {
        this.runningId.set(null);
        this.queuedId.set(strategyId);
      },
      error: (e: unknown) => {
        this.runningId.set(null);
        this.actionError.set(
          apiErrorMessage(e, name ? `Could not run ${name}.` : 'Could not run this strategy.'),
        );
      },
    });
  }

  // --- schedule / next-run rendering (one clock face, the autopilot's) ------
  readonly memberSchedule = (a: FundMemberCard): string =>
    scheduleWithZone(a.cron_description, a.timezone);
  readonly memberNextRun = (a: FundMemberCard): string =>
    nextRunLabel(a.next_run_at, a.timezone);
  readonly memberOverdue = (a: FundMemberCard): boolean => isOverdue(a.next_run_at);
}
