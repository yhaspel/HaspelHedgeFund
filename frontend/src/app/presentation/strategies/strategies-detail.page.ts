import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { GlossaryTermComponent } from '../shared/glossary-term.component';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { CYCLE_ACTIVE_STATUSES, CycleDetail, CycleEstimate, CycleMarkedSnapshot, CycleStatus, EnrollmentResult, EnrollmentRow, ScreenerCandidate } from '../../core/models/strategy.model';
import { RegimeContextWidgetComponent } from './regime-context-widget.component';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { TickerComponent } from '../shared/ticker.component';
import { ModalComponent } from '../shared/modal.component';

@Component({
  selector: 'hf-strategies-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, DatePipe, DecimalPipe, AppShellComponent, EmptyStateComponent, GlossaryTermComponent, RegimeContextWidgetComponent, TickerComponent, ModalComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label: store.currentStrategy()?.name || ''}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Strategy</div>
          <h1 class="mt-1.5">{{ store.currentStrategy()?.name ?? 'Strategy' }}</h1>
          <p class="text-xs text-text-2 mt-1">
            <hf-term key="universe">Universe</hf-term> {{ store.currentStrategy()?.universe_name }} ·
            <hf-term key="gross-exposure">Gross</hf-term> {{ store.currentStrategy()?.target_gross_pct }} ·
            <hf-term key="net-exposure">Net</hf-term> {{ store.currentStrategy()?.target_net_pct }} ·
            K {{ store.currentStrategy()?.top_k_longs }}L / {{ store.currentStrategy()?.top_k_shorts }}S
          </p>
        </div>
        <div class="head-actions">
          <button class="btn primary" (click)="openEstimate()" [disabled]="running() || estimating()" data-test="run-now">
            {{ running() ? 'Dispatching…' : estimating() ? 'Estimating…' : 'Run cycle now' }}
          </button>
          <a class="btn ghost" routerLink="/strategies">All strategies</a>
        </div>
      </div>

      @if (estimate(); as est) {
        <hf-modal titleId="estimate-modal-title" (closed)="cancelEstimate()">
          <div class="card est-modal" (click)="$event.stopPropagation()">

            <div class="est-modal__head">
              <div class="card-hd"><h2 class="title" id="estimate-modal-title">Confirm cycle dispatch</h2></div>
              <div class="est-modal__head-bd">
                <p class="text-[11.5px] text-text-3 m-0">
                  Preset <span class="mono text-text">{{ est.preset }}</span> ·
                  {{ est.n_candidates }} candidates · full council per candidate
                </p>

                <div
                  class="border border-solid rounded-md p-3 grid grid-cols-2 gap-y-1.5 gap-x-6 text-xs"
                  [style.borderColor]="est.exceeds_ceiling ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'"
                  [style.background]="est.exceeds_ceiling ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'">
                  <div>Est. per-call: <span class="mono">$ {{ est.per_call_usd.toFixed(4) }}</span></div>
                  <div>Est. total:
                    <span class="mono font-semibold"
                      [style.color]="est.exceeds_ceiling ? 'var(--acc-short-fg)' : 'var(--text)'">
                      $ {{ est.est_total_usd.toFixed(2) }}
                    </span>
                  </div>
                  <div>Cost ceiling: <span class="mono">$ {{ est.cost_ceiling_usd.toFixed(2) }}</span></div>
                  <div>n_candidates: <span class="mono">{{ est.n_candidates }}</span></div>
                </div>

                @if (est.exceeds_ceiling) {
                  <p class="text-[11.5px] text-[var(--acc-short-fg)] m-0">
                    ⚠ Estimate exceeds your cost ceiling. The cycle will trim K_longs/K_shorts before dispatching — raise the ceiling for a full fan-out.
                  </p>
                }

                <div class="eyebrow">Per-agent model assignment</div>

                <div class="cyc-tier-bar">
                  <label class="cyc-field">
                    <span>Price tier</span>
                    <select [ngModel]="cycleTier()" (ngModelChange)="onCycleTierChange($event)"
                            [disabled]="reEstimating()" data-test="cyc-tier">
                      @for (t of tiers(); track t.name) {
                        <option [ngValue]="t.name">{{ t.label }}</option>
                      }
                    </select>
                  </label>
                  <label class="cyc-field">
                    <span>Set all to</span>
                    <select [ngModel]="cycleModelAll()" (ngModelChange)="onCycleModelAll($event)"
                            [disabled]="reEstimating() || !tierMenuModels().length" data-test="cyc-model-all">
                      <option [ngValue]="null">— per-agent (varied) —</option>
                      @for (m of tierMenuModels(); track m.id) {
                        <option [ngValue]="m.id" [disabled]="!m.available">{{ m.label }}</option>
                      }
                    </select>
                  </label>
                  @if (reEstimating()) {
                    <span class="cyc-hint">Re-estimating…</span>
                  } @else {
                    <span class="cyc-hint">Applies to this cycle only — saved defaults untouched.</span>
                  }
                </div>
              </div>
            </div>

            <div class="est-modal__scroll scroll-area">
              <table class="tbl">
                <thead><tr>
                  <th scope="col">Agent</th><th scope="col">Model</th>
                  <th scope="col">Tier</th><th scope="col" class="right">$/call</th>
                </tr></thead>
                <tbody>
                  @for (row of est.per_agent; track row.agent) {
                    <tr [style.color]="row.model.startsWith('anthropic') ? 'var(--acc-short-fg)' : null">
                      <td class="mono">{{ row.agent }}</td>
                      <td>
                        <select class="input sans cyc-agent-select"
                                [ngModel]="cycleOverrides()[row.agent] || row.model"
                                (ngModelChange)="onAgentModelChange(row.agent, $event)"
                                [disabled]="reEstimating()"
                                [attr.data-test]="'cyc-model-' + row.agent">
                          @for (m of agentModelOptions(row.agent); track m.id) {
                            <option [value]="m.id" [disabled]="!m.available">{{ m.label }}</option>
                          }
                        </select>
                      </td>
                      <td>{{ row.tier }}</td>
                      <td class="num">{{ row.per_call_usd.toFixed(4) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>

            <div class="est-modal__foot">
              @if (anyAnthropic(est)) {
                <p class="text-[11.5px] text-[var(--acc-short-fg)] m-0">
                  ⚠ This cycle will hit Anthropic for at least one agent. If you didn't intend this, change the model in
                  <a routerLink="/settings/models" class="underline">Settings → Models</a>
                  or pick a different strategy preset.
                </p>
              }
              <div class="est-modal__actions">
                <button class="btn" (click)="cancelEstimate()">Cancel</button>
                <button class="btn primary" (click)="confirmRun()" [disabled]="running() || reEstimating()" data-test="confirm-run"
                  [style.background]="est.exceeds_ceiling ? 'var(--acc-hold)' : null"
                  [style.borderColor]="est.exceeds_ceiling ? 'var(--acc-hold)' : null">
                  {{ running() ? 'Dispatching…' : 'Confirm & run cycle' }}
                </button>
              </div>
            </div>

          </div>
        </hf-modal>
      }

      @if (enrollPreview(); as enr) {
        <hf-modal titleId="enroll-modal-title" (closed)="closeEnroll()">
          <div class="card est-modal" (click)="$event.stopPropagation()">
            <div class="est-modal__head">
              <div class="card-hd">
                <h2 class="title" id="enroll-modal-title">Enter strategy — cycle {{ enr.as_of_date }}</h2>
              </div>
              <div class="est-modal__head-bd">
                <p class="text-[11.5px] text-text-3 m-0">
                  Enters the <span class="mono text-text">{{ store.currentStrategy()?.name || enr.portfolio.name }}</span>
                  book (shown under Portfolios) · cash <span class="mono">$ {{ enr.portfolio.cash }}</span> ·
                  {{ enr.portfolio.positions_count }} held
                </p>
                <p class="text-[11.5px] text-text-3 m-0">
                  Makes the book match this cycle's target — holdings not in the target are closed.
                </p>
                <p class="text-[11.5px] text-text-3 m-0">
                  {{ enr.totals.n_open }} open · {{ enr.totals.n_increase }} increase ·
                  {{ enr.totals.n_reduce }} reduce · {{ enr.totals.n_close }} close ·
                  {{ enr.totals.n_skip }} skipped
                </p>
              </div>
            </div>
            <div class="est-modal__scroll scroll-area">
              <table class="tbl">
                <thead><tr>
                  <th class="w-8"><span class="visually-hidden">Approve</span></th>
                  <th>Ticker</th><th>Side</th><th>Action</th>
                  <th class="right">Quantity</th><th class="right">Notional</th>
                  <th class="right">Mark</th><th>Notes</th>
                </tr></thead>
                <tbody>
                  @for (row of enr.rows; track row.ticker) {
                    <tr [class.opacity-50]="!isActionable(row)">
                      <td>
                        <input type="checkbox"
                               [checked]="isApproved(row.ticker)"
                               [disabled]="!isActionable(row)"
                               (change)="toggleApprove(row.ticker, $event)"
                               [attr.aria-label]="'Approve ' + row.ticker" />
                      </td>
                      <td><hf-ticker [ticker]="row.ticker"></hf-ticker></td>
                      <td [class.text-[var(--acc-long-fg)]]="row.side==='long'"
                          [class.text-[var(--acc-short-fg)]]="row.side==='short'">{{ row.side }}</td>
                      <td class="mono text-[11.5px]">{{ row.action }}</td>
                      <td class="num">
                        @if (isActionable(row) && row.action !== 'close') {
                          <input type="number" class="qty-input"
                                 [value]="overrideValue(row)"
                                 (input)="setOverride(row.ticker, $event)"
                                 [attr.aria-label]="'Quantity for ' + row.ticker" />
                        } @else {
                          {{ row.suggested_quantity }}
                        }
                      </td>
                      <td class="num">$ {{ row.target_notional_usd }}</td>
                      <td class="num">
                        {{ row.mark_price ?? '—' }}
                        <span class="text-text-3 text-[10px]">{{ row.mark_source }}</span>
                      </td>
                      <td class="text-[11px] text-[var(--acc-hold-fg)]">{{ row.warnings.join(', ') }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <div class="est-modal__foot">
              <p class="text-[11.5px] text-text-3 m-0">
                Materializes positions into this strategy's book only — the Manual Book is untouched.
              </p>
              <div class="est-modal__actions">
                <button class="btn" (click)="closeEnroll()">Skip</button>
                <button class="btn ghost" (click)="confirmEnroll('auto')"
                        [disabled]="enrolling()" data-test="enroll-auto">
                  {{ enrolling() ? 'Entering…' : 'Enter all' }}
                </button>
                <button class="btn primary" (click)="confirmEnroll('manual')"
                        [disabled]="enrolling() || approvedCount() === 0" data-test="enroll-manual">
                  Enter selected ({{ approvedCount() }})
                </button>
              </div>
            </div>
          </div>
        </hf-modal>
      }

      @if (notice()) {
        <div role="status" aria-live="polite" class="pill info mb-3.5 h-auto py-2 px-3">
          <span class="dot"></span>{{ notice() }}
        </div>
      }

      <div class="grid grid-cols-[280px_1fr] gap-[18px]">
        <section class="card">
          <div class="card-hd"><h2 class="title">Cycles</h2></div>
          <div class="card-bd">
            @if (store.cycles().length === 0 && !cyclesLoaded()) {
              <div class="flex flex-col gap-2" aria-busy="true" aria-label="Loading cycles">
                @for (_ of [1,2,3]; track $index) {
                  <div class="skel h-[18px] w-full"></div>
                }
              </div>
            } @else if (store.cycles().length === 0) {
              <hf-empty-state
                [compact]="true"
                message="No cycles yet."
                detail='Click "Run cycle now" above to kick off the first one.'>
              </hf-empty-state>
            } @else {
              <ul class="list-none p-0 m-0 flex flex-col gap-1.5 text-xs">
                @for (c of store.cycles(); track c.id) {
                  <li>
                    <button (click)="openCycle(c.id)"
                      class="bg-transparent border-0 p-0 text-[var(--acc-info-fg)] cursor-pointer text-left">
                      <span class="mono">{{ c.as_of_date }}</span> · {{ c.status }} · g {{ c.gross_pct }}
                    </button>
                    @if (c.superseded_by) {
                      <span class="pill" title="Rerun as cycle #{{ c.superseded_by }}"
                            [style.margin-left.px]="6">↻ superseded</span>
                    } @else if (c.enrolled_at) {
                      <span class="pill ok" title="Positions materialized into the book"
                            [style.margin-left.px]="6">enrolled</span>
                    }
                  </li>
                }
              </ul>
            }
          </div>
        </section>

        <div>
          <hf-regime-context-widget
            [benchmark]="store.currentStrategy()?.benchmark_ticker || 'SPY'"
            [asOf]="cycle()?.as_of_date" />
        @if (cycle(); as c) {
          <section class="card">
            <div class="card-hd">
              <h2 class="title">Cycle {{ c.as_of_date }}</h2>
              <span class="pill"
                [class.ok]="c.status==='done'"
                [class.warn]="c.status==='running' || c.status==='queued' || c.status==='screening' || c.status==='running_council' || c.status==='constructing'"
                [class.info]="c.status==='awaiting_review'"
                [class.err]="c.status==='failed' || c.status==='cancelled'">
                <span class="dot"></span>{{ c.status }}
              </span>
              <div class="actions">
                <span class="mono text-[11.5px] text-text-3">
                  {{ c.finished_at ? (c.finished_at | date: 'short') : '—' }}
                </span>
                <span class="mono text-[11.5px] text-text-3">
                  · gross {{ c.gross_pct | number: '1.4-4' }} · net {{ c.net_pct | number: '1.4-4' }}
                </span>
                @if ((c.status === 'failed' || c.status === 'cancelled') && !c.superseded_by) {
                  <button type="button" class="btn ghost sm" (click)="rerunCycle(c.id)"
                          [disabled]="rerunningCycleId() === c.id"
                          data-test="rerun-cycle">
                    {{ rerunningCycleId() === c.id ? 'Rerunning…' : '↻ Rerun cycle' }}
                  </button>
                }
                @if (c.superseded_by) {
                  <button type="button" class="btn ghost sm" (click)="openCycle(c.superseded_by!)"
                          data-test="view-superseding">
                    ↻ superseded — view rerun
                  </button>
                }
                @if (c.status === 'done' && !c.enrolled_at) {
                  <button type="button" class="btn primary sm" (click)="openEnroll(c.id)"
                          [disabled]="enrollLoading()" data-test="enter-strategy">
                    {{ enrollLoading() ? 'Loading…' : 'Enter strategy' }}
                  </button>
                }
                @if (c.enrolled_at) {
                  <a class="btn ghost sm" [routerLink]="['/portfolios']"
                     data-test="enrolled-link">
                    ✓ Enrolled — view book
                  </a>
                }
              </div>
            </div>

            @if (c.status === 'awaiting_review') {
              <div class="card-bd flex flex-col gap-3 border-b border-solid border-border">
                <div class="flex items-center gap-2.5 flex-wrap">
                  <span class="eyebrow" data-test="review-panel-title">Review screener picks</span>
                  <span class="text-[11.5px] text-text-3">
                    The cycle stopped after the cheap screener pass. Pick which names should get the full council debate, then approve.
                  </span>
                </div>

                @if (reviewLongs(c).length > 0) {
                  <div>
                    <div class="eyebrow text-[var(--acc-long-fg)] mb-1.5">
                      Long candidates ({{ reviewLongsSelected().size }} / {{ reviewLongs(c).length }} selected)
                    </div>
                    <table class="tbl">
                      <thead><tr>
                        <th scope="col" class="w-9"><span class="visually-hidden">Include in council</span></th>
                        <th scope="col">Ticker</th><th scope="col">Name</th><th scope="col">Sector</th>
                        <th scope="col" class="right">Score</th>
                        <th scope="col" class="text-[11.5px] text-text-3">Why</th>
                      </tr></thead>
                      <tbody>
                        @for (cand of reviewLongs(c); track cand.ticker) {
                          <tr>
                            <td>
                              <input type="checkbox" [checked]="reviewLongsSelected().has(cand.ticker)"
                                     (change)="toggleReviewLong(cand.ticker)"
                                     [attr.aria-label]="'Include long candidate ' + cand.ticker"
                                     [attr.data-test]="'review-long-' + cand.ticker" />
                            </td>
                            <td><hf-ticker [ticker]="cand.ticker"></hf-ticker></td>
                            <td class="text-text-2 text-[12.5px]">{{ nameFor(cand.ticker) }}</td>
                            <td class="text-[11.5px] text-text-2">{{ cand.sector }}</td>
                            <td class="num mono">{{ cand.score | number: '1.3-3' }}</td>
                            <td class="text-[11.5px] text-text-3">{{ cand.rationale }}</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                }

                @if (reviewShorts(c).length > 0) {
                  <div>
                    <div class="eyebrow text-[var(--acc-short-fg)] mb-1.5">
                      Short candidates ({{ reviewShortsSelected().size }} / {{ reviewShorts(c).length }} selected)
                    </div>
                    <table class="tbl">
                      <thead><tr>
                        <th scope="col" class="w-9"><span class="visually-hidden">Include in council</span></th>
                        <th scope="col">Ticker</th><th scope="col">Name</th><th scope="col">Sector</th>
                        <th scope="col" class="right">Score</th>
                        <th scope="col" class="text-[11.5px] text-text-3">Why</th>
                      </tr></thead>
                      <tbody>
                        @for (cand of reviewShorts(c); track cand.ticker) {
                          <tr>
                            <td>
                              <input type="checkbox" [checked]="reviewShortsSelected().has(cand.ticker)"
                                     (change)="toggleReviewShort(cand.ticker)"
                                     [attr.aria-label]="'Include short candidate ' + cand.ticker"
                                     [attr.data-test]="'review-short-' + cand.ticker" />
                            </td>
                            <td><hf-ticker [ticker]="cand.ticker"></hf-ticker></td>
                            <td class="text-text-2 text-[12.5px]">{{ nameFor(cand.ticker) }}</td>
                            <td class="text-[11.5px] text-text-2">{{ cand.sector }}</td>
                            <td class="num mono">{{ cand.score | number: '1.3-3' }}</td>
                            <td class="text-[11.5px] text-text-3">{{ cand.rationale }}</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                }

                @if (reviewError()) {
                  <p role="alert" class="text-[var(--acc-short-fg)] text-2xs m-0" data-test="review-error">{{ reviewError() }}</p>
                }

                <div class="flex gap-2 items-center flex-wrap">
                  <button class="btn primary" (click)="approveCouncil(c)" [disabled]="approving()"
                          data-test="approve-council">
                    {{ approving() ? 'Approving…' : 'Approve & run council (' + reviewSelectedCount() + ')' }}
                  </button>
                  <button class="btn ghost" (click)="rejectCycle(c)" [disabled]="approving() || rejecting()"
                          data-test="reject-cycle">
                    {{ rejecting() ? 'Cancelling…' : 'Reject' }}
                  </button>
                  <span class="text-[11.5px] text-text-3">
                    · cost ceiling
                    <span class="mono">$ {{ store.currentStrategy()?.cost_ceiling_per_cycle_usd }}</span>
                  </span>
                </div>
              </div>
            }

            @if (c.candidate_runs && c.candidate_runs.length > 0) {
              <div class="card-bd border-b border-solid border-border">
                <div class="eyebrow mb-1.5">
                  Candidate runs ({{ c.candidate_runs.length }})
                </div>
                <table class="tbl">
                  <thead><tr>
                    <th scope="col">#</th><th scope="col">Ticker</th>
                    <th scope="col">Side</th><th scope="col">Status</th>
                    <th scope="col" class="right">Cost</th><th scope="col">Transcript</th>
                  </tr></thead>
                  <tbody>
                    @for (cr of c.candidate_runs; track cr.run_id) {
                      <tr>
                        <td class="mono">{{ cr.screener_rank }}</td>
                        <td class="mono text-text">
                          @for (leg of cr.candidate_key.split('/'); track $index; let last = $last) {
                            <hf-ticker [ticker]="leg"></hf-ticker>@if (!last) {<span class="text-text-3 px-0.5">/</span>}
                          }
                        </td>
                        <td class="mono"
                            [style.color]="cr.side === 'short' ? 'var(--acc-short-fg)' : 'var(--acc-long-fg)'">
                          {{ cr.side }}
                        </td>
                        <td>
                          <span class="pill h-auto py-0.5 px-1.5 text-[11px]"
                            [class.ok]="cr.run_status === 'done'"
                            [class.warn]="cr.run_status === 'queued' || cr.run_status === 'running'"
                            [class.err]="cr.run_status === 'failed' || cr.run_status === 'cancelled'">
                            {{ cr.run_status }}
                          </span>
                        </td>
                        <td class="num mono">$ {{ cr.run_cost_usd }}</td>
                        <td>
                          <a [routerLink]="['/runs', cr.run_id]"
                             class="text-[var(--acc-info-fg)]" data-test="candidate-run-link">
                            Open run #{{ cr.run_id }} →
                          </a>
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            }
            @if (store.currentStrategy()?.kind === 'market_neutral') {
              <div class="card-bd flex gap-3.5 items-center border-t border-solid border-border pt-2.5">
                <span class="eyebrow">Neutrality</span>
                <span class="mono" [style.color]="netNeutral(c) ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  <hf-term key="net-exposure">net</hf-term> $ {{ c.realised_net_pct || c.net_pct | number: '1.4-4' }}
                </span>
                <span class="mono" [style.color]="betaNeutral(c) ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  <hf-term key="beta">β</hf-term> {{ c.realised_portfolio_beta | number: '1.3-3' }}
                </span>
                @if (c.beta_diagnostics?.['alpha_clamped']) {
                  <span class="mono text-[var(--acc-short-fg)] text-[11.5px]">⚠ α clamped — partial breach</span>
                }
                @if (unreliable(c).length) {
                  <span class="mono text-[11.5px] text-text-3">
                    unreliable β: {{ unreliable(c).join(', ') }}
                  </span>
                }
              </div>
            }
            <div class="card-bd flex flex-col gap-[18px]">
              @if (c.error_message) {
                <p class="text-[var(--acc-short-fg)] text-2xs m-0">{{ c.error_message }}</p>
              }

              <div class="strat-group">
                <div class="strat-group-hd">Book structure</div>
                <p class="text-[11.5px] text-text-3 m-0 mb-2">
                  The cycle's <strong>target</strong> — what this strategy wants to hold. The paper
                  book's actual holdings (after you Enter) show under Portfolios.
                </p>
                @if (c.status === 'done' && c.marked_snapshot; as snap) {
                <div data-test="marked-snapshot" class="marked-snapshot-card">
                  <div class="flex items-baseline justify-between gap-3 flex-wrap">
                    <div>
                      <div class="eyebrow">Marked book (vs cycle as_of)</div>
                      <div class="flex items-baseline gap-3.5 mt-1 flex-wrap">
                        <div class="text-[20px] font-semibold"
                             [style.color]="snap.since_as_of_pct && +snap.since_as_of_pct > 0 ? 'var(--acc-long-fg)' : (snap.since_as_of_pct && +snap.since_as_of_pct < 0 ? 'var(--acc-short-fg)' : null)">
                          @if (snap.since_as_of_pct !== null) {
                            {{ +snap.since_as_of_pct > 0 ? '+' : '' }}{{ +snap.since_as_of_pct | number: '1.2-2' }}%
                          } @else { — }
                        </div>
                        <div class="mono text-[11.5px] text-text-3">
                          marked gross {{ snap.marked_gross_pct !== null ? (+snap.marked_gross_pct | number: '1.2-2') : '—' }}% ·
                          marked net {{ snap.marked_net_pct !== null ? (+snap.marked_net_pct | number: '1.2-2') : '—' }}%
                        </div>
                      </div>
                      <div class="mono text-[11px] text-text-3 mt-1">
                        as_of {{ c.as_of_date }} → mark {{ snap.mark_as_of }} · stamped {{ snap.snapshot_at | date: 'short' }}
                      </div>
                    </div>
                    <button class="btn ghost sm" (click)="refreshCycleMark(c)" [disabled]="refreshingMark()" data-test="refresh-cycle-mark">
                      <svg width="12" height="12" class="mr-1"><use href="/icons.svg#i-rerun" /></svg>
                      {{ refreshingMark() ? 'Refreshing…' : 'Refresh mark' }}
                    </button>
                  </div>

                  @for (w of snap.warnings; track w) {
                    <div class="pill warn h-auto py-1 px-2 w-fit"><span class="dot"></span>{{ w }}</div>
                  }

                  @if (markedRows(snap).length > 0) {
                    <table class="tbl">
                      <thead>
                        <tr>
                          <th scope="col">Ticker</th>
                          <th scope="col">Name</th>
                          <th scope="col" class="right">Weight</th>
                          <th scope="col" class="right">As_of px</th>
                          <th scope="col" class="right">Mark px</th>
                          <th scope="col" class="right">Return</th>
                          <th scope="col" class="right">Contribution</th>
                        </tr>
                      </thead>
                      <tbody>
                        @for (row of markedRows(snap); track row.ticker) {
                          <tr>
                            <td><hf-ticker [ticker]="row.ticker"></hf-ticker></td>
                            <td class="text-text-2 text-[12.5px]">{{ nameFor(row.ticker) }}</td>
                            <td class="num mono"
                                [style.color]="row.weight_pct >= 0 ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                              {{ row.weight_pct >= 0 ? '+' : '' }}{{ row.weight_pct | number: '1.2-2' }}%
                            </td>
                            <td class="num mono">{{ row.as_of_price ?? '—' }}</td>
                            <td class="num mono">{{ row.mark_price ?? '—' }}</td>
                            <td class="num mono"
                                [style.color]="row.return_pct !== null && row.return_pct > 0 ? 'var(--acc-long-fg)' : (row.return_pct !== null && row.return_pct < 0 ? 'var(--acc-short-fg)' : null)">
                              @if (row.return_pct === null) { — } @else {
                                {{ row.return_pct > 0 ? '+' : '' }}{{ row.return_pct | number: '1.2-2' }}%
                              }
                            </td>
                            <td class="num mono"
                                [style.color]="row.contribution_pp > 0 ? 'var(--acc-long-fg)' : (row.contribution_pp < 0 ? 'var(--acc-short-fg)' : null)">
                              {{ row.contribution_pp > 0 ? '+' : '' }}{{ row.contribution_pp | number: '1.2-2' }}pp
                            </td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  } @else {
                    <hf-empty-state [compact]="true" message="No marked positions — empty target book."></hf-empty-state>
                  }
                </div>
              }

              <div class="grid grid-cols-2 gap-[18px]">
                <div>
                  <div class="eyebrow text-[var(--acc-long-fg)] mb-1.5">Long book</div>
                  @if (longs().length === 0) {
                    <p class="text-[11.5px] text-text-3 m-0">No longs.</p>
                  } @else {
                    <table class="tbl">
                      <thead>
                        <tr>
                          <th scope="col">Ticker</th>
                          <th scope="col">Name</th>
                          <th scope="col" class="right">Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        @for (row of longs(); track row.ticker) {
                          <tr>
                            <td><hf-ticker [ticker]="row.ticker"></hf-ticker></td>
                            <td class="text-text-2 text-[12.5px]">{{ nameFor(row.ticker) }}</td>
                            <td class="num">{{ row.weight | number: '1.2-2' }}%</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
                <div>
                  <div class="eyebrow text-[var(--acc-short-fg)] mb-1.5">Short book</div>
                  @if (shorts().length === 0) {
                    <p class="text-[11.5px] text-text-3 m-0">No shorts.</p>
                  } @else {
                    <table class="tbl">
                      <thead>
                        <tr>
                          <th scope="col">Ticker</th>
                          <th scope="col">Name</th>
                          <th scope="col" class="right">Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        @for (row of shorts(); track row.ticker) {
                          <tr>
                            <td><hf-ticker [ticker]="row.ticker"></hf-ticker></td>
                            <td class="text-text-2 text-[12.5px]">{{ nameFor(row.ticker) }}</td>
                            <td class="num">{{ row.weight | number: '1.2-2' }}%</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
              </div>

              <div>
                <div class="eyebrow mb-1.5">Sector exposure (signed)</div>
                <table class="tbl">
                  <thead>
                    <tr>
                      <th scope="col">Sector</th>
                      <th scope="col" class="right">Weight</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (row of sectorRows(); track row.sector) {
                      <tr>
                        <td>{{ row.sector || '—' }}</td>
                        <td class="num"
                          [style.color]="row.weight > 0 ? 'var(--acc-long-fg)' : row.weight < 0 ? 'var(--acc-short-fg)' : null">
                          {{ row.weight | number: '1.2-2' }}%
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              </div><!-- /.strat-group Book structure -->

              <div class="strat-group">
                <div class="strat-group-hd">Orders</div>
                <div>
                <div class="eyebrow mb-1.5">Rebalance orders ({{ c.orders.length }})</div>
                @if (c.orders.length === 0) {
                  <p class="text-[11.5px] text-text-3 m-0">No orders this cycle.</p>
                } @else {
                  <table class="tbl">
                    <thead><tr>
                      <th scope="col">Seq</th><th scope="col">Side</th>
                      <th scope="col">Ticker</th><th scope="col">Name</th>
                      <th scope="col" class="right">Qty</th><th scope="col" class="right">Limit</th>
                      <th scope="col">Reason</th><th scope="col" class="right">Notional</th>
                    </tr></thead>
                    <tbody>
                      @for (o of c.orders; track o.id) {
                        <tr>
                          <td class="mono">{{ o.sequence }}</td>
                          <td class="mono"
                            [style.color]="(o.side === 'buy' || o.side === 'cover') ? 'var(--acc-long-fg)' : (o.side === 'sell' || o.side === 'short') ? 'var(--acc-short-fg)' : null">
                            {{ o.side }}
                          </td>
                          <td><hf-ticker [ticker]="o.ticker"></hf-ticker></td>
                          <td class="text-text-2 text-[12.5px]">{{ nameFor(o.ticker) }}</td>
                          <td class="num">{{ o.quantity }}</td>
                          <td class="num">{{ o.limit_price ?? 'mkt' }}</td>
                          <td class="text-[11.5px] text-text-2">{{ o.reason }}</td>
                          <td class="num">$ {{ o.estimated_notional_usd }}</td>
                        </tr>
                      }
                    </tbody>
                  </table>
                }
              </div>
              </div><!-- /.strat-group Orders -->

              @if (store.currentStrategy()?.kind === 'pairs') {
              <div class="strat-group">
                <div class="strat-group-hd">Pairs</div>
                <div>
                  <div class="eyebrow mb-1.5">
                    Open pairs ({{ openPairs().length }})
                    @if (councilEnabled()) {
                      <span class="text-[11.5px] text-text-3 ml-2">· AI council ON</span>
                    }
                  </div>
                  @if (openPairs().length === 0) {
                    <p class="text-[11.5px] text-text-3 m-0">No open pairs.</p>
                  } @else {
                    <table class="tbl">
                      <thead><tr>
                        <th scope="col">Long</th><th scope="col">Short</th><th scope="col">Sector</th>
                        <th scope="col" class="right">Hedge <hf-term key="beta">β</hf-term></th>
                        <th scope="col" class="right">Entry gap (<hf-term key="zscore">z</hf-term>)</th>
                        <th scope="col" class="right"><hf-term key="cointegration">Co-move</hf-term></th>
                        <th scope="col" class="right">Fit (p)</th>
                        <th scope="col">z trend (30d)</th>
                        @if (councilEnabled()) {
                          <th scope="col">Council</th><th scope="col" class="right">Conf.</th>
                        }
                      </tr></thead>
                      <tbody>
                        @for (p of openPairs(); track $index) {
                          <tr>
                            <td><hf-ticker [ticker]="p.leg_a"></hf-ticker></td>
                            <td><hf-ticker [ticker]="p.leg_b"></hf-ticker></td>
                            <td class="text-[11.5px] text-text-2">{{ p.sector || '—' }}</td>
                            <td class="num">{{ p.hedge_ratio | number: '1.2-3' }}</td>
                            <td class="num">{{ p.entry_z | number: '1.2-2' }}</td>
                            <td class="num">{{ p.correlation | number: '1.2-2' }}</td>
                            <td class="num">{{ p.p_value | number: '1.3-3' }}</td>
                            <td>
                              @if (p.z_history.length >= 2) {
                                <svg [attr.width]="sparkW" [attr.height]="sparkH"
                                  [attr.viewBox]="'0 0 ' + sparkW + ' ' + sparkH"
                                  class="block overflow-visible">
                                  <path [attr.d]="sparkPath(p.z_history)"
                                        fill="none" stroke="var(--acc-info-fg)" stroke-width="1.2" />
                                  <line x1="0" [attr.x2]="sparkW"
                                        [attr.y1]="sparkMidY(p.z_history)" [attr.y2]="sparkMidY(p.z_history)"
                                        stroke="var(--text-3)" stroke-width="0.5" stroke-dasharray="2,2" />
                                </svg>
                              } @else {
                                <span class="text-[11px] text-text-3">—</span>
                              }
                            </td>
                            @if (councilEnabled()) {
                              <td class="mono"
                                [style.color]="p.council_action === 'enter' ? 'var(--acc-long-fg)' : 'var(--text-3)'">
                                {{ p.council_action || '—' }}
                              </td>
                              <td class="num">{{ p.council_confidence ?? '—' }}</td>
                            }
                          </tr>
                        }
                      </tbody>
                    </table>
                    @if (councilEnabled()) {
                      <div class="flex flex-col gap-2 mt-2">
                        @for (p of openPairs(); track $index) {
                          @if (p.council_thesis) {
                            <div class="border border-solid border-border rounded-md py-2 px-2.5 bg-surface-2">
                              <div class="flex gap-2 items-baseline text-[11.5px] text-text-3">
                                <span class="font-semibold text-text inline-flex items-baseline gap-1">
                                  <hf-ticker [ticker]="p.leg_a"></hf-ticker>
                                  <span class="text-text-3">/</span>
                                  <hf-ticker [ticker]="p.leg_b"></hf-ticker>
                                </span>
                                <span>· council {{ p.council_action }} @ {{ p.council_confidence }}</span>
                              </div>
                              <p class="text-[12.5px] text-text-2 m-0 mt-1 whitespace-pre-wrap break-words">{{ p.council_thesis }}</p>
                            </div>
                          }
                        }
                      </div>
                    }
                  }
                </div>

                @if (closedPairs().length > 0) {
                  <div>
                    <div class="eyebrow mb-1.5">Pairs closed this cycle ({{ closedPairs().length }})</div>
                    <table class="tbl">
                      <thead><tr>
                        <th scope="col">Long leg</th><th scope="col">Short leg</th>
                        <th scope="col">Reason</th>
                        <th scope="col" class="right">Exit gap (z)</th>
                      </tr></thead>
                      <tbody>
                        @for (cp of closedPairs(); track $index) {
                          <tr>
                            <td>
                              @if (cp.leg_a) {
                                <hf-ticker [ticker]="cp.leg_a"></hf-ticker>
                              } @else { <span class="mono">—</span> }
                            </td>
                            <td>
                              @if (cp.leg_b) {
                                <hf-ticker [ticker]="cp.leg_b"></hf-ticker>
                              } @else { <span class="mono">—</span> }
                            </td>
                            <td class="text-[11.5px]"
                              [style.color]="cp.reason === 'reverted' ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                              {{ cp.reason === 'reverted' ? 'mean-reverted (profit-take)'
                                 : cp.reason === 'stopped' ? 'bail-out (gap kept widening)'
                                 : cp.reason }}
                            </td>
                            <td class="num">{{ cp.z ?? '—' }}</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                }

                @if (councilSkipped().length > 0) {
                  <details>
                    <summary class="eyebrow cursor-pointer">
                      Council skipped ({{ councilSkipped().length }})
                    </summary>
                    <div class="flex flex-col gap-1.5 mt-2">
                      @for (sk of councilSkipped(); track $index) {
                        <div class="border border-solid border-border rounded-md py-1.5 px-2.5 bg-surface-2">
                          <div class="text-[11.5px] text-text-3 flex flex-wrap items-baseline gap-1">
                            <span class="text-text inline-flex items-baseline gap-1">
                              <hf-ticker [ticker]="sk.leg_a"></hf-ticker>
                              <span class="text-text-3">/</span>
                              <hf-ticker [ticker]="sk.leg_b"></hf-ticker>
                            </span>
                            <span>· {{ sk.enter_count }} enter / {{ sk.skip_count }} skip · confidence {{ sk.confidence }}</span>
                          </div>
                          <p class="text-2xs text-text-2 m-0 mt-1">{{ sk.thesis_excerpt }}</p>
                        </div>
                      }
                    </div>
                  </details>
                }

                @if (pairsDiag(); as d) {
                  <p class="text-[11.5px] text-text-3 m-0">
                    <hf-term key="screener">Screener</hf-term>: evaluated {{ d.n_pairs_evaluated }} same-sector pairs ·
                    {{ d.n_pairs_cointegrated }} statistically fit ·
                    {{ d.n_pairs_above_entry_z }} above the open-trade trigger ·
                    {{ d.n_new_pairs }} opened this cycle.
                  </p>
                }
              </div><!-- /.strat-group Pairs -->
              }

              @if (store.currentStrategy()?.kind === 'concentrated_long' && c.cycle_outcome === 'held_existing_book') {
                <div class="pill warn h-auto py-2 px-3">
                  <span class="dot"></span>
                  No-action cycle: fewer than min_positions cleared the confidence bar. Existing book held; no new target produced.
                </div>
              }

              @if (theses().length > 0) {
                <div class="strat-group">
                  <div class="strat-group-hd">Per-position theses ({{ theses().length }})</div>
                  <div class="flex flex-col gap-2.5">
                    @for (t of theses(); track t.ticker) {
                      <div class="card thesis-card" data-test="position-thesis">
                        <div class="flex gap-2.5 items-baseline flex-wrap">
                          <span class="font-semibold text-text">
                            <hf-ticker [ticker]="t.ticker"></hf-ticker>
                          </span>
                          <span class="text-[11.5px] text-text-2">{{ nameFor(t.ticker) }}</span>
                          <span class="text-[11.5px] text-text-3">{{ t.sector || '—' }}</span>
                          <span class="mono text-[11.5px] text-[var(--acc-long-fg)]">
                            {{ t.weight | number: '1.2-2' }}%
                          </span>
                          <span class="mono text-[11.5px] text-text-3">
                            confidence {{ t.aggregate_confidence }}
                          </span>
                        </div>
                        <p class="text-[12.5px] text-text-2 m-0 mt-1.5 whitespace-pre-wrap break-words">
                          {{ t.thesis }}
                        </p>
                      </div>
                    }
                  </div>
                </div>
              }

              @if (c.sector_veto_log && c.sector_veto_log.length > 0) {
                <details open>
                  <summary class="eyebrow cursor-pointer">
                    Council review ({{ c.sector_veto_log.length }} ETFs · {{ sectorVetoCount(c) }} <hf-term key="veto">vetoed</hf-term>)
                  </summary>
                  <ul class="mono m-0 mt-2 p-0 list-none flex flex-col gap-1.5 text-[11.5px] text-text-2">
                    @for (v of c.sector_veto_log; track v.ticker) {
                      <li>
                        <hf-ticker [ticker]="v.ticker"></hf-ticker>
                        @if (v.decision === 'veto') {
                          <span class="text-[var(--acc-short-fg)]">— <hf-term key="veto">vetoed</hf-term></span>
                          @if (v.rm_veto) { <span> · <hf-term key="rm">risk-manager</hf-term> <hf-term key="veto">veto</hf-term></span> }
                          @for (r of v.reasons; track r.persona) {
                            <span> · {{ r.persona }} {{ r.signal }}@{{ r.confidence }}</span>
                          }
                        } @else {
                          <span class="text-[var(--acc-long-fg)]">— approved</span>
                          <span class="text-text-3"> (threshold {{ v.threshold_pct }}%)</span>
                        }
                      </li>
                    }
                  </ul>
                </details>
              }

              @if (c.rejected_candidates.length > 0) {
                <details>
                  <summary class="eyebrow cursor-pointer">
                    Rejected candidates ({{ c.rejected_candidates.length }})
                  </summary>
                  <ul class="mono m-0 mt-2 p-0 list-none flex flex-col gap-1 text-[11.5px] text-text-2">
                    @for (r of c.rejected_candidates; track $index) {
                      <li>
                        @if (r.ticker) {
                          <hf-ticker [ticker]="r.ticker"></hf-ticker>
                        } @else {
                          <span class="text-text-3">(sector)</span>
                        }
                        — {{ r.reason }}
                      </li>
                    }
                  </ul>
                </details>
              }
            </div>
          </section>
        } @else if (store.cycles().length > 0) {
          <section class="card" aria-busy="true" aria-label="Loading cycle">
            <div class="card-hd"><h2 class="title">Cycle</h2></div>
            <div class="card-bd flex flex-col gap-2.5">
              <div class="skel h-3.5 w-2/5"></div>
              <div class="grid grid-cols-4 gap-3.5">
                @for (_ of [1,2,3,4]; track $index) {
                  <div class="skel h-[60px]"></div>
                }
              </div>
              <div class="skel h-3.5 w-full"></div>
              <div class="skel h-3.5 w-4/5"></div>
            </div>
          </section>
        }
        </div>
      </div>

      @if (store.currentStrategy(); as strat) {
        <section class="card mt-[18px]">
          <div class="card-hd"><h2 class="title">Strategy settings</h2></div>
          <div class="card-bd flex flex-col gap-4">
            <label class="flex items-start gap-2.5 cursor-pointer">
              <input type="checkbox" class="mt-0.5"
                     [checked]="strat.auto_enroll_on_done"
                     [disabled]="savingAutoEnroll()"
                     (change)="onAutoEnrollToggle($event)"
                     data-test="auto-enroll-toggle" />
              <span>
                <span class="text-text font-medium text-xs">Auto-enter on cycle done</span>
                <span class="block text-[11.5px] text-text-3">
                  When a cycle completes, materialize its target weights into this
                  strategy's book automatically — skipping the manual confirm step.
                </span>
              </span>
            </label>

            <div class="border-t border-solid border-border pt-3.5">
              <div class="eyebrow text-[var(--acc-short-fg)] mb-1.5">Danger zone</div>
              @if ((strat.targets_count_active ?? 0) === 0) {
                <div class="flex items-center gap-3 flex-wrap">
                  <button type="button" class="btn danger sm"
                          (click)="confirmDelete(strat.id, strat.name)"
                          [disabled]="deleting()" data-test="delete-strategy">
                    {{ deleting() ? 'Deleting…' : 'Delete strategy' }}
                  </button>
                  <span class="text-[11.5px] text-text-3">
                    Removes this strategy and its (empty) book. Cannot be undone.
                  </span>
                </div>
              } @else {
                <p class="text-[11.5px] text-text-3 m-0">
                  This strategy has {{ strat.targets_count_active }} non-cancelled
                  {{ strat.targets_count_active === 1 ? 'cycle' : 'cycles' }} and cannot be
                  deleted — its history is preserved. Cancel its cycles or deactivate it instead.
                </p>
              }
            </div>
          </div>
        </section>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .qty-input {
        width: 72px;
        text-align: right;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-4);
        padding: 2px 6px;
        font-family: var(--font-mono, monospace);
        font-size: 12px;
        color: var(--text);
      }
      /* Confirm-cycle modal — fixed header + footer, only the agent list scrolls. */
      .est-modal {
        max-width: 640px;
        width: 100%;
        max-height: 90vh;
        box-shadow: var(--shadow-3);
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .est-modal__head {
        flex: 0 0 auto;
      }
      .est-modal__head-bd {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        border-bottom: 1px solid var(--border);
      }
      /* Only the agent list scrolls; the table header (.tbl thead th, already
         position: sticky) pins to the top of this region. */
      .est-modal__scroll {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        padding: 0 16px;
      }
      .est-modal__foot {
        flex: 0 0 auto;
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding: 12px 16px;
        border-top: 1px solid var(--border);
      }
      .est-modal__actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      /* Confirm-cycle modal — transient tier / model override controls. */
      .cyc-tier-bar {
        display: flex;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
        padding: 8px 10px;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-6);
      }
      .cyc-field {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        color: var(--text-2);
      }
      .cyc-field select {
        padding: 5px 8px;
        border-radius: var(--r-4);
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
        font-size: 12px;
        max-width: 240px;
      }
      .cyc-field select:disabled {
        opacity: 0.5;
      }
      .cyc-hint {
        font-size: 10.5px;
        color: var(--text-3);
        margin-left: auto;
      }
      .cyc-agent-select {
        width: 100%;
        max-width: 320px;
        font-size: 12px;
        padding: 3px 6px;
      }
      /* Marked-book snapshot card — uses --r-8 radius token. */
      .marked-snapshot-card {
        border: 1px solid var(--border);
        border-radius: var(--r-8);
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      /* Per-position thesis card — soft surface chip. */
      .thesis-card {
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 10px;
        background: var(--surface-2);
      }
      /* WS-6 DC-09 — visual grouping for the cycle main column. */
      .strat-group {
        background: var(--surface-2);
        border-radius: var(--r-6);
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .strat-group-hd {
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-2);
        padding-bottom: 8px;
        border-bottom: 1px solid var(--border);
      }
      /* Snapshot card nested inside .strat-group should match the surface
         the group sits on, otherwise the nested surface-2 looks doubled. */
      .strat-group .marked-snapshot-card {
        background: var(--surface);
      }
    `,
  ],
})
export class StrategiesDetailPage implements OnInit, OnDestroy {
  readonly store = inject(StrategiesStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly profiles = inject(TickerProfileStore);
  private readonly graphs = inject(GraphsStore);
  private readonly models = inject(ModelsStore);
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  /** Flips true after the first `listCycles` call settles so the left-rail
   *  can show a skeleton during the initial fetch (rather than the
   *  "No cycles yet." empty state, which only applies to true zero-cycle
   *  strategies). */
  readonly cyclesLoaded = signal(false);

  nameFor(ticker: string | null | undefined): string {
    if (!ticker) return '—';
    void this.profiles._bump();
    return this.profiles.name(ticker) || '—';
  }

  private _collectCycleTickers(d: CycleDetail | null | undefined): string[] {
    if (!d) return [];
    const tk = new Set<string>();
    const addArr = (rows: { ticker?: string | null }[] | undefined) => {
      (rows ?? []).forEach((r) => r?.ticker && tk.add(r.ticker));
    };
    addArr((d as { longs?: { ticker: string }[] }).longs);
    addArr((d as { shorts?: { ticker: string }[] }).shorts);
    addArr(d.orders as unknown as { ticker?: string }[]);
    addArr(d.screener_ranking?.long_candidates);
    addArr(d.screener_ranking?.short_candidates);
    if (d.marked_snapshot?.per_ticker) {
      for (const k of Object.keys(d.marked_snapshot.per_ticker)) tk.add(k);
    }
    if (Array.isArray((d as { theses?: { ticker?: string }[] }).theses)) {
      addArr((d as { theses?: { ticker?: string }[] }).theses);
    }
    const addPairs = (
      pairs: { leg_a?: string | null; leg_b?: string | null }[] | undefined,
    ) => {
      (pairs ?? []).forEach((p) => {
        if (p.leg_a) tk.add(p.leg_a);
        if (p.leg_b) tk.add(p.leg_b);
      });
    };
    const dd = d as {
      open_pairs?: { leg_a?: string; leg_b?: string }[];
      closed_pairs?: { leg_a?: string; leg_b?: string }[];
      council_skipped_pairs?: { leg_a?: string; leg_b?: string }[];
    };
    addPairs(dd.open_pairs);
    addPairs(dd.closed_pairs);
    addPairs(dd.council_skipped_pairs);
    return [...tk];
  }

  running = signal(false);
  estimating = signal(false);
  estimate = signal<CycleEstimate | null>(null);
  notice = signal<string | null>(null);
  cycle = signal<CycleDetail | null>(null);

  // P4c: transient (this-run-only) tier/model override state for the dispatch
  // modal. `cycleTier` mirrors the chosen price tier; `cycleOverrides` is the
  // full per-agent model map currently in effect (seeded from the estimate);
  // `cycleModelAll` is the bulk "apply one model to every agent" pick.
  // `cycleExplicit` = the user tweaked per-agent / bulk models (vs only the
  // tier), so the dispatch sends an explicit map rather than a preset name.
  cycleTier = signal<string | null>(null);
  cycleOverrides = signal<Record<string, string>>({});
  cycleModelAll = signal<string | null>(null);
  cycleDirty = signal(false);
  cycleExplicit = signal(false);
  reEstimating = signal(false);

  // P2l: review-panel state. Refresh whenever a new awaiting_review cycle loads.
  approving = signal(false);
  rejecting = signal(false);
  reviewError = signal<string | null>(null);
  private readonly _reviewLongs = signal<Set<string>>(new Set());
  private readonly _reviewShorts = signal<Set<string>>(new Set());
  reviewLongsSelected = this._reviewLongs.asReadonly();
  reviewShortsSelected = this._reviewShorts.asReadonly();

  openEstimate(): void {
    this.estimating.set(true);
    this.notice.set(null);
    // Load the price-tier registry (cached) + model catalog so the modal's
    // selectors have labels and per-tier menus.
    this.graphs.loadRegistry().subscribe();
    if (this.models.models().length === 0) this.models.loadAll().subscribe();
    this.store.estimate(this.strategyId).subscribe({
      next: (e) => {
        this.estimating.set(false);
        this.estimate.set(e);
        this.cycleOverrides.set({ ...e.overrides });
        this.cycleTier.set(e.preset || null);
        this.cycleModelAll.set(null);
        this.cycleDirty.set(false);
        this.cycleExplicit.set(false);
      },
      error: () => { this.estimating.set(false); this.notice.set('Failed to fetch cost estimate.'); },
    });
  }
  cancelEstimate(): void {
    this.estimate.set(null);
    this.cycleDirty.set(false);
    this.cycleExplicit.set(false);
    this.cycleModelAll.set(null);
  }
  anyAnthropic(est: { per_agent: { model: string }[] }): boolean {
    return est.per_agent.some((r) => r.model.startsWith('anthropic'));
  }
  confirmRun(): void {
    // Transient override, this run only: an explicit per-agent map if the user
    // tweaked models, else just the chosen tier (let the server expand it),
    // else nothing (resolve from the strategy's saved defaults as before).
    let body: { preset?: string; model_overrides?: Record<string, string> } = {};
    if (this.cycleExplicit()) {
      body = { model_overrides: this.cycleOverrides() };
    } else if (this.cycleDirty() && this.cycleTier()) {
      body = { preset: this.cycleTier()! };
    }
    this.estimate.set(null);
    this.runNow(body);
  }

  // --- P4c dispatch-modal tier / model selection -----------------------------

  /** Price tiers from the graph registry — {name,label,default_model,models}. */
  tiers(): { name: string; label: string; default_model: string | null; models: string[] }[] {
    return this.graphs.registry()?.tiers ?? [];
  }

  /** Curated model menu for the selected tier (the "Set all to" dropdown). */
  tierMenuModels(): { id: string; label: string; available: boolean }[] {
    const t = this.tiers().find((x) => x.name === this.cycleTier());
    if (!t) return [];
    const all = this.models.models();
    return t.models.map((id) => {
      const m = all.find((x) => x.id === id);
      return {
        id,
        label: m ? `${m.supports_reasoning ? '🧠 ' : ''}${m.display_name} · ${m.tier}` : id,
        available: m ? m.available : true,
      };
    });
  }

  /** Options for a per-agent dropdown: the active tier's menu (or the full
   *  catalog when no tier is selected), plus the agent's current pick if it
   *  isn't in the menu — so the select never renders blank. */
  agentModelOptions(agent: string): { id: string; label: string; available: boolean }[] {
    const all = this.models.models();
    const tier = this.tiers().find((x) => x.name === this.cycleTier());
    const base = tier
      ? all.filter((m) => tier.models.includes(m.id) || m.provider === 'ollama')
      : all;
    const out = base.map((m) => ({
      id: m.id,
      label: `${m.supports_reasoning ? '🧠 ' : ''}${m.display_name} · ${m.tier}`,
      available: m.available,
    }));
    const cur = this.cycleOverrides()[agent];
    if (cur && !out.some((o) => o.id === cur)) {
      const stale = all.find((m) => m.id === cur);
      out.push(stale
        ? { id: cur, label: `${stale.supports_reasoning ? '🧠 ' : ''}${stale.display_name} · ${stale.tier}`, available: stale.available }
        : { id: cur, label: cur, available: true });
    }
    return out;
  }

  /** Re-fetch the authoritative estimate for a transient override, then sync
   *  local state from the response. */
  /** Snapshot of the transient selection so a failed re-estimate can roll the
   *  modal back to the last good state — keeping the dropdowns, the cost table,
   *  and the body confirmRun() would send all internally consistent. */
  private snapshotCycleState(): {
    tier: string | null; overrides: Record<string, string>;
    modelAll: string | null; explicit: boolean; dirty: boolean;
  } {
    return {
      tier: this.cycleTier(),
      overrides: { ...this.cycleOverrides() },
      modelAll: this.cycleModelAll(),
      explicit: this.cycleExplicit(),
      dirty: this.cycleDirty(),
    };
  }

  private reEstimate(
    body: { preset?: string; model_overrides?: Record<string, string> },
    rollback: {
      tier: string | null; overrides: Record<string, string>;
      modelAll: string | null; explicit: boolean; dirty: boolean;
    },
  ): void {
    this.reEstimating.set(true);
    this.cycleDirty.set(true);
    this.store.estimateWith(this.strategyId, body).subscribe({
      next: (e) => {
        this.reEstimating.set(false);
        this.estimate.set(e);
        this.cycleOverrides.set({ ...e.overrides });
      },
      error: () => {
        this.reEstimating.set(false);
        this.notice.set('Failed to re-estimate with the selected models — reverted.');
        this.cycleTier.set(rollback.tier);
        this.cycleOverrides.set(rollback.overrides);
        this.cycleModelAll.set(rollback.modelAll);
        this.cycleExplicit.set(rollback.explicit);
        this.cycleDirty.set(rollback.dirty);
      },
    });
  }

  onCycleTierChange(name: string | null): void {
    if (!name) return;
    const snap = this.snapshotCycleState();
    this.cycleTier.set(name);
    this.cycleModelAll.set(null);
    this.cycleExplicit.set(false);
    // A tier switch applies that tier's per-agent spread (the "set"); the
    // server expands it (resolving any local model) and echoes the map back.
    this.reEstimate({ preset: name }, snap);
  }

  onCycleModelAll(modelId: string | null): void {
    const snap = this.snapshotCycleState();
    this.cycleModelAll.set(modelId ?? null);
    if (!modelId) {
      // Revert to the selected tier's varied per-agent spread.
      if (this.cycleTier()) {
        this.cycleExplicit.set(false);
        this.reEstimate({ preset: this.cycleTier()! }, snap);
      }
      return;
    }
    // Apply one model to every agent in the current map, overwriting the spread.
    const next: Record<string, string> = {};
    for (const a of Object.keys(this.cycleOverrides())) next[a] = modelId;
    this.cycleExplicit.set(true);
    this.reEstimate({ preset: this.cycleTier() ?? undefined, model_overrides: next }, snap);
  }

  onAgentModelChange(agent: string, modelId: string): void {
    const snap = this.snapshotCycleState();
    this.cycleModelAll.set(null);
    this.cycleExplicit.set(true);
    const next = { ...this.cycleOverrides(), [agent]: modelId };
    this.reEstimate({ preset: this.cycleTier() ?? undefined, model_overrides: next }, snap);
  }

  strategyId = 0;

  // P3 addendum: cycle-level mark-to-market snapshot.
  refreshingMark = signal(false);

  markedRows(snap: CycleMarkedSnapshot): {
    ticker: string;
    weight_pct: number;
    as_of_price: string | null;
    mark_price: string | null;
    return_pct: number | null;
    contribution_pp: number;
  }[] {
    return Object.entries(snap.per_ticker)
      .map(([ticker, row]) => ({
        ticker,
        weight_pct: Number(row.weight_pct),
        as_of_price: row.as_of_price,
        mark_price: row.mark_price,
        return_pct: row.return_pct === null ? null : Number(row.return_pct),
        contribution_pp: Number(row.contribution_pp),
      }))
      .sort((a, b) => Math.abs(b.contribution_pp) - Math.abs(a.contribution_pp));
  }

  refreshCycleMark(c: CycleDetail): void {
    this.refreshingMark.set(true);
    this.store.refreshCycleMark(this.strategyId, c.id).subscribe({
      next: (snap) => {
        this.refreshingMark.set(false);
        this.cycle.set({ ...c, marked_snapshot: snap });
      },
      error: () => this.refreshingMark.set(false),
    });
  }

  longs() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.target_weights)
      .filter(([, w]) => Number(w) > 0)
      .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
      .sort((a, b) => b.weight - a.weight);
  }
  shorts() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.target_weights)
      .filter(([, w]) => Number(w) < 0)
      .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
      .sort((a, b) => a.weight - b.weight);
  }
  sectorVetoCount(c: CycleDetail): number {
    return (c.sector_veto_log ?? []).filter(v => v.decision === 'veto').length;
  }

  netNeutral(c: CycleDetail): boolean {
    const tol = Number(this.store.currentStrategy()?.neutrality_tolerance_dollar_pct ?? 0.02);
    return Math.abs(Number(c.realised_net_pct ?? c.net_pct)) <= tol;
  }
  unreliable(c: CycleDetail): string[] {
    const u = (c.beta_diagnostics as Record<string, unknown> | undefined)?.['unreliable'];
    return Array.isArray(u) ? (u as string[]) : [];
  }
  betaNeutral(c: CycleDetail): boolean {
    const tol = Number(this.store.currentStrategy()?.neutrality_tolerance_beta ?? 0.05);
    return Math.abs(Number(c.realised_portfolio_beta ?? 0)) <= tol;
  }
  theses() {
    const c = this.cycle(); if (!c) return [];
    const pt = c.per_position_thesis || {};
    return Object.entries(pt).map(([ticker, v]) => ({
      ticker,
      sector: v.sector,
      action: v.action,
      aggregate_confidence: v.aggregate_confidence,
      thesis: v.thesis,
      weight: (Number(c.target_weights[ticker] || 0)) * 100,
    })).sort((a, b) => b.weight - a.weight);
  }
  readonly sparkW = 88;
  readonly sparkH = 22;
  openPairs(): {
    leg_a: string; leg_b: string; sector: string;
    hedge_ratio: number; entry_z: number;
    correlation: number; p_value: number;
    council_action: string; council_confidence: number | null;
    council_thesis: string;
    z_history: number[];
  }[] {
    const c = this.cycle(); if (!c) return [];
    const arr = (c.beta_diagnostics as Record<string, unknown> | undefined)?.['open_pairs'];
    if (!Array.isArray(arr)) return [];
    return (arr as Record<string, unknown>[]).map((p) => ({
      leg_a: String(p['leg_a'] ?? ''),
      leg_b: String(p['leg_b'] ?? ''),
      sector: String(p['sector'] ?? ''),
      hedge_ratio: Number(p['hedge_ratio'] ?? 0),
      entry_z: Number(p['entry_z'] ?? 0),
      correlation: Number(p['correlation'] ?? 0),
      p_value: Number(p['p_value'] ?? 0),
      council_action: String(p['council_action'] ?? ''),
      council_confidence: p['council_confidence'] == null ? null : Number(p['council_confidence']),
      council_thesis: String(p['council_thesis'] ?? ''),
      z_history: Array.isArray(p['z_history']) ? (p['z_history'] as number[]).map(Number) : [],
    }));
  }
  sparkPath(zs: number[]): string {
    if (zs.length < 2) return '';
    const w = this.sparkW, h = this.sparkH;
    const lo = Math.min(...zs), hi = Math.max(...zs);
    const range = Math.max(0.001, hi - lo);
    const xs = zs.map((_, i) => (i * w) / (zs.length - 1));
    const ys = zs.map((v) => h - ((v - lo) / range) * h);
    return xs.map((x, i) => `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${ys[i].toFixed(1)}`).join(' ');
  }
  sparkMidY(zs: number[]): number {
    if (zs.length < 2) return this.sparkH / 2;
    const lo = Math.min(...zs), hi = Math.max(...zs);
    if (lo >= 0 || hi <= 0) return this.sparkH / 2;
    const range = Math.max(0.001, hi - lo);
    return this.sparkH - ((0 - lo) / range) * this.sparkH;
  }
  closedPairs(): { leg_a: string; leg_b: string; reason: string; z: number | null }[] {
    const c = this.cycle(); if (!c) return [];
    const arr = (c.beta_diagnostics as Record<string, unknown> | undefined)?.['closes'];
    if (!Array.isArray(arr)) return [];
    return (arr as Record<string, unknown>[]).map((cl) => ({
      leg_a: String(cl['leg_a'] ?? ''),
      leg_b: String(cl['leg_b'] ?? ''),
      reason: String(cl['reason'] ?? ''),
      z: cl['z'] == null ? null : Number(cl['z']),
    }));
  }
  councilEnabled(): boolean {
    const c = this.cycle(); if (!c) return false;
    return Boolean((c.beta_diagnostics as Record<string, unknown> | undefined)?.['council_enabled']);
  }
  councilSkipped(): { leg_a: string; leg_b: string; confidence: number;
                      enter_count: number; skip_count: number; thesis_excerpt: string }[] {
    const c = this.cycle(); if (!c) return [];
    const arr = (c.beta_diagnostics as Record<string, unknown> | undefined)?.['council_log'];
    if (!Array.isArray(arr)) return [];
    return (arr as Record<string, unknown>[])
      .filter((r) => r['action'] !== 'enter')
      .map((r) => ({
        leg_a: String(r['leg_a'] ?? ''),
        leg_b: String(r['leg_b'] ?? ''),
        confidence: Number(r['confidence'] ?? 0),
        enter_count: Number(r['enter_count'] ?? 0),
        skip_count: Number(r['skip_count'] ?? 0),
        thesis_excerpt: String(r['thesis_excerpt'] ?? ''),
      }));
  }
  pairsDiag(): { n_pairs_evaluated: number; n_pairs_cointegrated: number;
                  n_pairs_above_entry_z: number; n_new_pairs: number } | null {
    const c = this.cycle(); if (!c) return null;
    const d = c.beta_diagnostics as Record<string, unknown> | undefined;
    if (!d) return null;
    return {
      n_pairs_evaluated: Number(d['n_pairs_evaluated'] ?? 0),
      n_pairs_cointegrated: Number(d['n_pairs_cointegrated'] ?? 0),
      n_pairs_above_entry_z: Number(d['n_pairs_above_entry_z'] ?? 0),
      n_new_pairs: Number(d['n_new_pairs'] ?? 0),
    };
  }
  sectorRows() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.sector_exposure)
      .map(([sector, w]) => ({ sector, weight: Number(w) * 100 }))
      .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  }

  ngOnInit(): void {
    this.strategyId = Number(this.route.snapshot.paramMap.get('id'));
    this.store.detail(this.strategyId).subscribe();
    this.refreshCycles();
    // Warm the price-tier registry (cached) so the dispatch modal's tier
    // selector is populated before the user can open it.
    this.graphs.loadRegistry().subscribe();
  }
  ngOnDestroy(): void { if (this.pollHandle) clearInterval(this.pollHandle); }

  // ---- P4 WS-E: enter strategy (enrollment) ------------------------------
  enrollPreview = signal<EnrollmentResult | null>(null);
  enrollLoading = signal(false);
  enrolling = signal(false);
  private _approved = signal<Set<string>>(new Set());
  private _overrides = signal<Map<string, string>>(new Map());

  private _actionable(row: EnrollmentRow): boolean {
    return ['open', 'increase', 'reduce', 'close'].includes(row.action);
  }
  isActionable(row: EnrollmentRow): boolean { return this._actionable(row); }
  isApproved(ticker: string): boolean { return this._approved().has(ticker); }
  approvedCount(): number { return this._approved().size; }

  openEnroll(targetId: number): void {
    if (this.enrollLoading()) return;
    this.enrollLoading.set(true);
    this.notice.set(null);
    this.store.previewEnrollment(this.strategyId, targetId).subscribe({
      next: (res) => {
        this.enrollLoading.set(false);
        // Default-approve every actionable row.
        this._approved.set(new Set(res.rows.filter((r) => this._actionable(r)).map((r) => r.ticker)));
        this._overrides.set(new Map());
        this.enrollPreview.set(res);
      },
      error: (e) => {
        this.enrollLoading.set(false);
        this.notice.set(e?.error?.detail || 'Could not load the enrollment preview.');
      },
    });
  }

  toggleApprove(ticker: string, ev: Event): void {
    const next = new Set(this._approved());
    if ((ev.target as HTMLInputElement).checked) next.add(ticker);
    else next.delete(ticker);
    this._approved.set(next);
  }

  overrideValue(row: EnrollmentRow): string {
    const ov = this._overrides().get(row.ticker);
    if (ov !== undefined) return ov;
    // Show the unsigned magnitude of the suggested quantity.
    return String(Math.abs(Number(row.suggested_quantity)));
  }

  setOverride(ticker: string, ev: Event): void {
    const next = new Map(this._overrides());
    next.set(ticker, (ev.target as HTMLInputElement).value);
    this._overrides.set(next);
  }

  closeEnroll(): void {
    this.enrollPreview.set(null);
    this._approved.set(new Set());
    this._overrides.set(new Map());
  }

  confirmEnroll(mode: 'auto' | 'manual'): void {
    const enr = this.enrollPreview();
    if (!enr || this.enrolling()) return;
    this.enrolling.set(true);
    const body: { mode: 'auto' | 'manual'; approved_tickers?: string[]; override_quantities?: Record<string, string> } =
      mode === 'auto'
        ? { mode: 'auto' }
        : {
            mode: 'manual',
            approved_tickers: [...this._approved()],
            override_quantities: Object.fromEntries(this._overrides()),
          };
    this.store.applyEnrollment(this.strategyId, enr.target_id, body).subscribe({
      next: (res) => {
        this.enrolling.set(false);
        this.closeEnroll();
        this.notice.set(`Enrolled ${res.rows.length} position(s) into the strategy book.`);
        this.refreshCycles();
      },
      error: (e) => {
        this.enrolling.set(false);
        this.notice.set(e?.error?.detail || 'Enrollment failed.');
      },
    });
  }

  refreshCycles(): void {
    this.store.listCycles(this.strategyId).subscribe({
      next: (cs) => {
        this.cyclesLoaded.set(true);
        if (cs.length === 0) return;
        const open = this.cycle();
        // If the user manually opened an older cycle, keep refreshing it;
        // otherwise always prefer the most recently created row so a freshly
        // dispatched cycle appears on the page after run-now.
        if (!open || open.id === cs[0].id) {
          this.openCycle(cs[0].id);
        } else {
          this.openCycle(open.id);
        }
      },
      error: () => this.cyclesLoaded.set(true),
    });
  }

  openCycle(id: number): void {
    this.store.cycleDetail(this.strategyId, id).subscribe((d) => {
      const prev = this.cycle();
      this.cycle.set(d);
      // WS-2: prefetch identity for every ticker the cycle references.
      const tickers = this._collectCycleTickers(d);
      if (tickers.length) this.profiles.fetchNames(tickers).subscribe();
      // Seed review selections whenever a cycle BECOMES awaiting_review
      // (covers fresh-load, cycle-switch, and same-row resurrection where
      // a cancelled target is reused on the same day).
      const becameAwaitingReview =
        d.status === 'awaiting_review'
        && (!prev || prev.id !== d.id || prev.status !== 'awaiting_review');
      if (becameAwaitingReview) {
        this._reviewLongs.set(new Set(
          (d.screener_ranking?.long_candidates ?? []).map((c) => c.ticker),
        ));
        this._reviewShorts.set(new Set(
          (d.screener_ranking?.short_candidates ?? []).map((c) => c.ticker),
        ));
        this.reviewError.set(null);
      }
      // Auto-poll non-terminal cycles every 5s.
      if (CYCLE_ACTIVE_STATUSES.includes(d.status as CycleStatus)) {
        if (!this.pollHandle) {
          this.pollHandle = setInterval(() => this.refreshCycles(), 5000);
        }
      } else if (this.pollHandle) {
        clearInterval(this.pollHandle);
        this.pollHandle = null;
      }
    });
  }

  runNow(body: { preset?: string; model_overrides?: Record<string, string> } = {}): void {
    this.running.set(true);
    this.notice.set(null);
    this.store.runNow(this.strategyId, body).subscribe({
      next: (r) => {
        this.running.set(false);
        this.notice.set(`Cycle dispatched (task ${r.task_id}). Refreshing every 5s.`);
        if (this.pollHandle) clearInterval(this.pollHandle);
        this.pollHandle = setInterval(() => this.refreshCycles(), 5000);
      },
      error: () => { this.running.set(false); this.notice.set('Failed to dispatch cycle.'); },
    });
  }

  // P4 WS-C: delete this strategy (gated server-side on no non-cancelled
  // cycles + an empty book). Confirm first; navigate away on success.
  deleting = signal(false);
  confirmDelete(id: number, name: string): void {
    if (this.deleting()) return;
    const ok = confirm(
      `Delete strategy "${name}"? This also removes its (empty) strategy book. `
      + 'This cannot be undone.',
    );
    if (!ok) return;
    this.deleting.set(true);
    this.store.deleteStrategy(id).subscribe({
      next: () => { this.deleting.set(false); this.router.navigate(['/strategies']); },
      error: (e) => {
        this.deleting.set(false);
        this.notice.set(e?.error?.detail || 'Could not delete this strategy.');
      },
    });
  }

  // P4 WS-E: persist the auto-enter-on-cycle-done preference.
  savingAutoEnroll = signal(false);
  onAutoEnrollToggle(ev: Event): void {
    const value = (ev.target as HTMLInputElement).checked;
    this.savingAutoEnroll.set(true);
    this.store.setAutoEnroll(this.strategyId, value).subscribe({
      next: () => this.savingAutoEnroll.set(false),
      error: () => {
        this.savingAutoEnroll.set(false);
        this.notice.set('Could not update auto-enter preference.');
      },
    });
  }

  // P4 WS-B: rerun a terminal cycle. Dispatches a fresh cycle for the same
  // as_of; the old row is stamped superseded. Repolls the cycles list so the
  // new row (assigned by the chord callback) surfaces.
  rerunningCycleId = signal<number | null>(null);
  rerunCycle(targetId: number): void {
    if (this.rerunningCycleId() !== null) return;
    this.rerunningCycleId.set(targetId);
    this.notice.set(null);
    this.store.rerunCycle(this.strategyId, targetId).subscribe({
      next: (r) => {
        this.rerunningCycleId.set(null);
        this.notice.set(`Cycle rerun dispatched (task ${r.task_id}). Refreshing every 5s.`);
        if (this.pollHandle) clearInterval(this.pollHandle);
        this.pollHandle = setInterval(() => this.refreshCycles(), 5000);
      },
      error: () => {
        this.rerunningCycleId.set(null);
        this.notice.set('Failed to rerun cycle.');
      },
    });
  }

  // ---- P2l review helpers ------------------------------------------------

  reviewLongs(c: CycleDetail): ScreenerCandidate[] {
    return c.screener_ranking?.long_candidates ?? [];
  }
  reviewShorts(c: CycleDetail): ScreenerCandidate[] {
    return c.screener_ranking?.short_candidates ?? [];
  }
  reviewSelectedCount(): number {
    return this._reviewLongs().size + this._reviewShorts().size;
  }
  toggleReviewLong(ticker: string): void {
    const next = new Set(this._reviewLongs());
    if (next.has(ticker)) next.delete(ticker);
    else next.add(ticker);
    this._reviewLongs.set(next);
  }
  toggleReviewShort(ticker: string): void {
    const next = new Set(this._reviewShorts());
    if (next.has(ticker)) next.delete(ticker);
    else next.add(ticker);
    this._reviewShorts.set(next);
  }

  approveCouncil(c: CycleDetail): void {
    if (this.reviewSelectedCount() === 0) {
      this.reviewError.set('Pick at least one candidate to send to the council.');
      return;
    }
    this.approving.set(true);
    this.reviewError.set(null);
    this.store.approveCouncil(this.strategyId, c.id, {
      long_tickers: Array.from(this._reviewLongs()),
      short_tickers: Array.from(this._reviewShorts()),
    }).subscribe({
      next: (r) => {
        this.approving.set(false);
        this.notice.set(`Approved — ${r.n_candidates} candidate run${r.n_candidates === 1 ? '' : 's'} dispatched.`);
        this.openCycle(c.id);
      },
      error: (e) => {
        this.approving.set(false);
        this.reviewError.set(
          e?.error?.detail
            || (e?.error?.estimate?.exceeds_ceiling
                  ? `Approved set exceeds your cost ceiling ($${e.error.estimate.est_total_usd}).`
                  : null)
            || 'Failed to approve cycle.',
        );
      },
    });
  }

  rejectCycle(c: CycleDetail): void {
    this.rejecting.set(true);
    this.reviewError.set(null);
    this.store.rejectCycle(this.strategyId, c.id).subscribe({
      next: () => {
        this.rejecting.set(false);
        this.notice.set('Cycle cancelled.');
        this.refreshCycles();
      },
      error: (e) => {
        this.rejecting.set(false);
        this.reviewError.set(e?.error?.detail || 'Failed to cancel cycle.');
      },
    });
  }
}
