import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { CYCLE_ACTIVE_STATUSES, CycleDetail, CycleStatus, ScreenerCandidate } from '../../core/models/strategy.model';

@Component({
  selector: 'hf-strategies-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, DatePipe, DecimalPipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label: store.currentStrategy()?.name || ''}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Strategy</div>
          <h1 style="margin-top:6px">{{ store.currentStrategy()?.name ?? 'Strategy' }}</h1>
          <p style="font-size:13px;color:var(--text-2);margin-top:4px">
            Universe {{ store.currentStrategy()?.universe_name }} ·
            Gross {{ store.currentStrategy()?.target_gross_pct }} ·
            Net {{ store.currentStrategy()?.target_net_pct }} ·
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
        <div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:var(--z-modal);display:flex;align-items:center;justify-content:center;padding:16px"
             (click)="cancelEstimate()">
          <div class="card" style="max-width:640px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-3)"
               (click)="$event.stopPropagation()">
            <div class="card-hd"><span class="title">Confirm cycle dispatch</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
              <p style="font-size:11.5px;color:var(--text-3);margin:0">
                Preset <span class="mono" style="color:var(--text)">{{ est.preset }}</span> ·
                {{ est.n_candidates }} candidates · full council per candidate
              </p>

              <div
                style="border:1px solid;border-radius:6px;padding:12px;display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13px"
                [style.borderColor]="est.exceeds_ceiling ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'"
                [style.background]="est.exceeds_ceiling ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'">
                <div>Est. per-call: <span class="mono">$ {{ est.per_call_usd.toFixed(4) }}</span></div>
                <div>Est. total:
                  <span class="mono" style="font-weight:600"
                    [style.color]="est.exceeds_ceiling ? 'var(--acc-short-fg)' : 'var(--text)'">
                    $ {{ est.est_total_usd.toFixed(2) }}
                  </span>
                </div>
                <div>Cost ceiling: <span class="mono">$ {{ est.cost_ceiling_usd.toFixed(2) }}</span></div>
                <div>n_candidates: <span class="mono">{{ est.n_candidates }}</span></div>
              </div>

              @if (est.exceeds_ceiling) {
                <p style="font-size:11.5px;color:var(--acc-short-fg);margin:0">
                  ⚠ Estimate exceeds your cost ceiling. The cycle will trim K_longs/K_shorts before dispatching — raise the ceiling for a full fan-out.
                </p>
              }

              <div class="eyebrow">Per-agent model assignment</div>
              <table class="tbl">
                <thead><tr>
                  <th>Agent</th><th>Model</th><th>Tier</th><th class="right">$/call</th>
                </tr></thead>
                <tbody>
                  @for (row of est.per_agent; track row.agent) {
                    <tr [style.color]="row.model.startsWith('anthropic') ? 'var(--acc-short-fg)' : null">
                      <td class="mono">{{ row.agent }}</td>
                      <td class="mono">{{ row.model_name || row.model }}</td>
                      <td>{{ row.tier }}</td>
                      <td class="num">{{ row.per_call_usd.toFixed(4) }}</td>
                    </tr>
                  }
                </tbody>
              </table>

              @if (anyAnthropic(est)) {
                <p style="font-size:11.5px;color:var(--acc-short-fg);margin:0">
                  ⚠ This cycle will hit Anthropic for at least one agent. If you didn't intend this, change the model in
                  <a routerLink="/settings/models" style="text-decoration:underline">Settings → Models</a>
                  or pick a different strategy preset.
                </p>
              }

              <div style="display:flex;justify-content:flex-end;gap:8px;border-top:1px solid var(--border);padding-top:12px">
                <button class="btn" (click)="cancelEstimate()">Cancel</button>
                <button class="btn primary" (click)="confirmRun()" [disabled]="running()" data-test="confirm-run"
                  [style.background]="est.exceeds_ceiling ? 'var(--acc-hold)' : null"
                  [style.borderColor]="est.exceeds_ceiling ? 'var(--acc-hold)' : null">
                  {{ running() ? 'Dispatching…' : 'Confirm & run cycle' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      }

      @if (notice()) {
        <div class="pill info" style="margin-bottom:14px;height:auto;padding:8px 12px">
          <span class="dot"></span>{{ notice() }}
        </div>
      }

      <div style="display:grid;grid-template-columns:280px 1fr;gap:18px">
        <section class="card">
          <div class="card-hd"><span class="title">Cycles</span></div>
          <div class="card-bd">
            @if (store.cycles().length === 0) {
              <p style="font-size:11.5px;color:var(--text-3);margin:0">No cycles yet — click "Run cycle now".</p>
            } @else {
              <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;font-size:13px">
                @for (c of store.cycles(); track c.id) {
                  <li>
                    <button (click)="openCycle(c.id)"
                      style="background:transparent;border:0;padding:0;color:var(--acc-info-fg);cursor:pointer;text-align:left">
                      <span class="mono">{{ c.as_of_date }}</span> · {{ c.status }} · g {{ c.gross_pct }}
                    </button>
                  </li>
                }
              </ul>
            }
          </div>
        </section>

        @if (cycle(); as c) {
          <section class="card">
            <div class="card-hd">
              <span class="title">Cycle {{ c.as_of_date }}</span>
              <span class="pill"
                [class.ok]="c.status==='done'"
                [class.warn]="c.status==='running' || c.status==='queued' || c.status==='screening' || c.status==='running_council' || c.status==='constructing'"
                [class.info]="c.status==='awaiting_review'"
                [class.err]="c.status==='failed' || c.status==='cancelled'">
                <span class="dot"></span>{{ c.status }}
              </span>
              <div class="actions">
                <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                  {{ c.finished_at ? (c.finished_at | date: 'short') : '—' }}
                </span>
                <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                  · gross {{ c.gross_pct | number: '1.4-4' }} · net {{ c.net_pct | number: '1.4-4' }}
                </span>
              </div>
            </div>

            @if (c.status === 'awaiting_review') {
              <div class="card-bd" style="display:flex;flex-direction:column;gap:12px;border-bottom:1px solid var(--border)">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                  <span class="eyebrow" data-test="review-panel-title">Review screener picks</span>
                  <span style="font-size:11.5px;color:var(--text-3)">
                    The cycle stopped after the cheap screener pass. Pick which names should get the full council debate, then approve.
                  </span>
                </div>

                @if (reviewLongs(c).length > 0) {
                  <div>
                    <div class="eyebrow" style="color:var(--acc-long-fg);margin-bottom:6px">
                      Long candidates ({{ reviewLongsSelected().size }} / {{ reviewLongs(c).length }} selected)
                    </div>
                    <table class="tbl">
                      <thead><tr>
                        <th style="width:36px">Run?</th>
                        <th>Ticker</th><th>Sector</th>
                        <th class="right">Score</th>
                        <th style="font-size:11.5px;color:var(--text-3)">Why</th>
                      </tr></thead>
                      <tbody>
                        @for (cand of reviewLongs(c); track cand.ticker) {
                          <tr>
                            <td>
                              <input type="checkbox" [checked]="reviewLongsSelected().has(cand.ticker)"
                                     (change)="toggleReviewLong(cand.ticker)"
                                     [attr.data-test]="'review-long-' + cand.ticker" />
                            </td>
                            <td class="mono" style="color:var(--text)">{{ cand.ticker }}</td>
                            <td style="font-size:11.5px;color:var(--text-2)">{{ cand.sector }}</td>
                            <td class="num mono">{{ cand.score | number: '1.3-3' }}</td>
                            <td style="font-size:11.5px;color:var(--text-3)">{{ cand.rationale }}</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                }

                @if (reviewShorts(c).length > 0) {
                  <div>
                    <div class="eyebrow" style="color:var(--acc-short-fg);margin-bottom:6px">
                      Short candidates ({{ reviewShortsSelected().size }} / {{ reviewShorts(c).length }} selected)
                    </div>
                    <table class="tbl">
                      <thead><tr>
                        <th style="width:36px">Run?</th>
                        <th>Ticker</th><th>Sector</th>
                        <th class="right">Score</th>
                        <th style="font-size:11.5px;color:var(--text-3)">Why</th>
                      </tr></thead>
                      <tbody>
                        @for (cand of reviewShorts(c); track cand.ticker) {
                          <tr>
                            <td>
                              <input type="checkbox" [checked]="reviewShortsSelected().has(cand.ticker)"
                                     (change)="toggleReviewShort(cand.ticker)"
                                     [attr.data-test]="'review-short-' + cand.ticker" />
                            </td>
                            <td class="mono" style="color:var(--text)">{{ cand.ticker }}</td>
                            <td style="font-size:11.5px;color:var(--text-2)">{{ cand.sector }}</td>
                            <td class="num mono">{{ cand.score | number: '1.3-3' }}</td>
                            <td style="font-size:11.5px;color:var(--text-3)">{{ cand.rationale }}</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  </div>
                }

                @if (reviewError()) {
                  <p style="color:var(--acc-short-fg);font-size:12px;margin:0" data-test="review-error">{{ reviewError() }}</p>
                }

                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                  <button class="btn primary" (click)="approveCouncil(c)" [disabled]="approving()"
                          data-test="approve-council">
                    {{ approving() ? 'Approving…' : 'Approve & run council (' + reviewSelectedCount() + ')' }}
                  </button>
                  <button class="btn ghost" (click)="rejectCycle(c)" [disabled]="approving() || rejecting()"
                          data-test="reject-cycle">
                    {{ rejecting() ? 'Cancelling…' : 'Reject' }}
                  </button>
                  <span style="font-size:11.5px;color:var(--text-3)">
                    · cost ceiling
                    <span class="mono">$ {{ store.currentStrategy()?.cost_ceiling_per_cycle_usd }}</span>
                  </span>
                </div>
              </div>
            }

            @if (c.candidate_runs && c.candidate_runs.length > 0) {
              <div class="card-bd" style="border-bottom:1px solid var(--border)">
                <div class="eyebrow" style="margin-bottom:6px">
                  Candidate runs ({{ c.candidate_runs.length }})
                </div>
                <table class="tbl">
                  <thead><tr>
                    <th>#</th><th>Ticker</th><th>Side</th><th>Status</th>
                    <th class="right">Cost</th><th>Transcript</th>
                  </tr></thead>
                  <tbody>
                    @for (cr of c.candidate_runs; track cr.run_id) {
                      <tr>
                        <td class="mono">{{ cr.screener_rank }}</td>
                        <td class="mono" style="color:var(--text)">{{ cr.candidate_key }}</td>
                        <td class="mono"
                            [style.color]="cr.side === 'short' ? 'var(--acc-short-fg)' : 'var(--acc-long-fg)'">
                          {{ cr.side }}
                        </td>
                        <td>
                          <span class="pill"
                            [class.ok]="cr.run_status === 'done'"
                            [class.warn]="cr.run_status === 'queued' || cr.run_status === 'running'"
                            [class.err]="cr.run_status === 'failed' || cr.run_status === 'cancelled'"
                            style="height:auto;padding:2px 6px;font-size:11px">
                            {{ cr.run_status }}
                          </span>
                        </td>
                        <td class="num mono">$ {{ cr.run_cost_usd }}</td>
                        <td>
                          <a [routerLink]="['/runs', cr.run_id]"
                             style="color:var(--acc-info-fg)" data-test="candidate-run-link">
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
              <div class="card-bd" style="display:flex;gap:14px;align-items:center;border-top:1px solid var(--border);padding-top:10px">
                <span class="eyebrow">Neutrality</span>
                <span class="mono" [style.color]="netNeutral(c) ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  net $ {{ c.realised_net_pct || c.net_pct | number: '1.4-4' }}
                </span>
                <span class="mono" [style.color]="betaNeutral(c) ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  β {{ c.realised_portfolio_beta | number: '1.3-3' }}
                </span>
                @if (c.beta_diagnostics?.['alpha_clamped']) {
                  <span class="mono" style="color:var(--acc-short-fg);font-size:11.5px">⚠ α clamped — partial breach</span>
                }
                @if (unreliable(c).length) {
                  <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                    unreliable β: {{ unreliable(c).join(', ') }}
                  </span>
                }
              </div>
            }
            <div class="card-bd" style="display:flex;flex-direction:column;gap:18px">
              @if (c.error_message) {
                <p style="color:var(--acc-short-fg);font-size:12px;margin:0">{{ c.error_message }}</p>
              }

              <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
                <div>
                  <div class="eyebrow" style="color:var(--acc-long-fg);margin-bottom:6px">Long book</div>
                  @if (longs().length === 0) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:0">No longs.</p>
                  } @else {
                    <table class="tbl">
                      <tbody>
                        @for (row of longs(); track row.ticker) {
                          <tr>
                            <td class="mono" style="color:var(--text)">{{ row.ticker }}</td>
                            <td class="num">{{ row.weight | number: '1.2-2' }}%</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
                <div>
                  <div class="eyebrow" style="color:var(--acc-short-fg);margin-bottom:6px">Short book</div>
                  @if (shorts().length === 0) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:0">No shorts.</p>
                  } @else {
                    <table class="tbl">
                      <tbody>
                        @for (row of shorts(); track row.ticker) {
                          <tr>
                            <td class="mono" style="color:var(--text)">{{ row.ticker }}</td>
                            <td class="num">{{ row.weight | number: '1.2-2' }}%</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
              </div>

              <div>
                <div class="eyebrow" style="margin-bottom:6px">Sector exposure (signed)</div>
                <table class="tbl">
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

              <div>
                <div class="eyebrow" style="margin-bottom:6px">Rebalance orders ({{ c.orders.length }})</div>
                @if (c.orders.length === 0) {
                  <p style="font-size:11.5px;color:var(--text-3);margin:0">No orders this cycle.</p>
                } @else {
                  <table class="tbl">
                    <thead><tr>
                      <th>Seq</th><th>Side</th><th>Ticker</th>
                      <th class="right">Qty</th><th class="right">Limit</th>
                      <th>Reason</th><th class="right">Notional</th>
                    </tr></thead>
                    <tbody>
                      @for (o of c.orders; track o.id) {
                        <tr>
                          <td class="mono">{{ o.sequence }}</td>
                          <td class="mono"
                            [style.color]="(o.side === 'buy' || o.side === 'cover') ? 'var(--acc-long-fg)' : (o.side === 'sell' || o.side === 'short') ? 'var(--acc-short-fg)' : null">
                            {{ o.side }}
                          </td>
                          <td class="mono" style="color:var(--text)">{{ o.ticker }}</td>
                          <td class="num">{{ o.quantity }}</td>
                          <td class="num">{{ o.limit_price ?? 'mkt' }}</td>
                          <td style="font-size:11.5px;color:var(--text-2)">{{ o.reason }}</td>
                          <td class="num">$ {{ o.estimated_notional_usd }}</td>
                        </tr>
                      }
                    </tbody>
                  </table>
                }
              </div>

              @if (store.currentStrategy()?.kind === 'pairs') {
                <div>
                  <div class="eyebrow" style="margin-bottom:6px">
                    Open pairs ({{ openPairs().length }})
                    @if (councilEnabled()) {
                      <span style="font-size:11.5px;color:var(--text-3);margin-left:8px">· AI council ON</span>
                    }
                  </div>
                  @if (openPairs().length === 0) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:0">No open pairs.</p>
                  } @else {
                    <table class="tbl">
                      <thead><tr>
                        <th>Long</th><th>Short</th><th>Sector</th>
                        <th class="right">Hedge β</th>
                        <th class="right">Entry gap (z)</th>
                        <th class="right">Co-move</th>
                        <th class="right">Fit (p)</th>
                        <th>z trend (30d)</th>
                        @if (councilEnabled()) {
                          <th>Council</th><th class="right">Conf.</th>
                        }
                      </tr></thead>
                      <tbody>
                        @for (p of openPairs(); track $index) {
                          <tr>
                            <td class="mono" style="color:var(--acc-long-fg)">{{ p.leg_a }}</td>
                            <td class="mono" style="color:var(--acc-short-fg)">{{ p.leg_b }}</td>
                            <td style="font-size:11.5px;color:var(--text-2)">{{ p.sector || '—' }}</td>
                            <td class="num">{{ p.hedge_ratio | number: '1.2-3' }}</td>
                            <td class="num">{{ p.entry_z | number: '1.2-2' }}</td>
                            <td class="num">{{ p.correlation | number: '1.2-2' }}</td>
                            <td class="num">{{ p.p_value | number: '1.3-3' }}</td>
                            <td>
                              @if (p.z_history.length >= 2) {
                                <svg [attr.width]="sparkW" [attr.height]="sparkH"
                                  [attr.viewBox]="'0 0 ' + sparkW + ' ' + sparkH"
                                  style="display:block;overflow:visible">
                                  <path [attr.d]="sparkPath(p.z_history)"
                                        fill="none" stroke="var(--acc-info-fg)" stroke-width="1.2" />
                                  <line x1="0" [attr.x2]="sparkW"
                                        [attr.y1]="sparkMidY(p.z_history)" [attr.y2]="sparkMidY(p.z_history)"
                                        stroke="var(--text-3)" stroke-width="0.5" stroke-dasharray="2,2" />
                                </svg>
                              } @else {
                                <span style="font-size:11px;color:var(--text-3)">—</span>
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
                      <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
                        @for (p of openPairs(); track $index) {
                          @if (p.council_thesis) {
                            <div style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--surface-1)">
                              <div style="display:flex;gap:8px;align-items:baseline;font-size:11.5px;color:var(--text-3)">
                                <span class="mono" style="color:var(--text);font-weight:600">{{ p.leg_a }} / {{ p.leg_b }}</span>
                                <span>· council {{ p.council_action }} @ {{ p.council_confidence }}</span>
                              </div>
                              <p style="font-size:12.5px;color:var(--text-2);margin:4px 0 0;white-space:pre-wrap;word-break:break-word">{{ p.council_thesis }}</p>
                            </div>
                          }
                        }
                      </div>
                    }
                  }
                </div>

                @if (closedPairs().length > 0) {
                  <div>
                    <div class="eyebrow" style="margin-bottom:6px">Pairs closed this cycle ({{ closedPairs().length }})</div>
                    <table class="tbl">
                      <thead><tr>
                        <th>Long leg</th><th>Short leg</th>
                        <th>Reason</th>
                        <th class="right">Exit gap (z)</th>
                      </tr></thead>
                      <tbody>
                        @for (cp of closedPairs(); track $index) {
                          <tr>
                            <td class="mono">{{ cp.leg_a || '—' }}</td>
                            <td class="mono">{{ cp.leg_b || '—' }}</td>
                            <td style="font-size:11.5px"
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
                    <summary class="eyebrow" style="cursor:pointer">
                      Council skipped ({{ councilSkipped().length }})
                    </summary>
                    <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
                      @for (sk of councilSkipped(); track $index) {
                        <div style="border:1px solid var(--border);border-radius:6px;padding:6px 10px;background:var(--surface-1)">
                          <div style="font-size:11.5px;color:var(--text-3)">
                            <span class="mono" style="color:var(--text)">{{ sk.leg_a }} / {{ sk.leg_b }}</span>
                            · {{ sk.enter_count }} enter / {{ sk.skip_count }} skip · confidence {{ sk.confidence }}
                          </div>
                          <p style="font-size:12px;color:var(--text-2);margin:4px 0 0">{{ sk.thesis_excerpt }}</p>
                        </div>
                      }
                    </div>
                  </details>
                }

                @if (pairsDiag(); as d) {
                  <p style="font-size:11.5px;color:var(--text-3);margin:0">
                    Screener: evaluated {{ d.n_pairs_evaluated }} same-sector pairs ·
                    {{ d.n_pairs_cointegrated }} statistically fit ·
                    {{ d.n_pairs_above_entry_z }} above the open-trade trigger ·
                    {{ d.n_new_pairs }} opened this cycle.
                  </p>
                }
              }

              @if (store.currentStrategy()?.kind === 'concentrated_long' && c.cycle_outcome === 'held_existing_book') {
                <div class="pill warn" style="height:auto;padding:8px 12px">
                  <span class="dot"></span>
                  No-action cycle: fewer than min_positions cleared the confidence bar. Existing book held; no new target produced.
                </div>
              }

              @if (theses().length > 0) {
                <div>
                  <div class="eyebrow" style="margin-bottom:6px">Per-position thesis ({{ theses().length }})</div>
                  <div style="display:flex;flex-direction:column;gap:10px">
                    @for (t of theses(); track t.ticker) {
                      <div class="card" data-test="position-thesis"
                        style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--surface-1)">
                        <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
                          <span class="mono" style="color:var(--text);font-weight:600">{{ t.ticker }}</span>
                          <span style="font-size:11.5px;color:var(--text-3)">{{ t.sector || '—' }}</span>
                          <span class="mono" style="font-size:11.5px;color:var(--acc-long-fg)">
                            {{ t.weight | number: '1.2-2' }}%
                          </span>
                          <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                            confidence {{ t.aggregate_confidence }}
                          </span>
                        </div>
                        <p style="font-size:12.5px;color:var(--text-2);margin:6px 0 0;white-space:pre-wrap;word-break:break-word">
                          {{ t.thesis }}
                        </p>
                      </div>
                    }
                  </div>
                </div>
              }

              @if (c.sector_veto_log && c.sector_veto_log.length > 0) {
                <details open>
                  <summary class="eyebrow" style="cursor:pointer">
                    Council review ({{ c.sector_veto_log.length }} ETFs · {{ sectorVetoCount(c) }} vetoed)
                  </summary>
                  <ul class="mono" style="margin:8px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px;font-size:11.5px;color:var(--text-2)">
                    @for (v of c.sector_veto_log; track v.ticker) {
                      <li>
                        <span style="color:var(--text)">{{ v.ticker }}</span>
                        @if (v.decision === 'veto') {
                          <span style="color:#d9826e">— vetoed</span>
                          @if (v.rm_veto) { <span> · risk-manager veto</span> }
                          @for (r of v.reasons; track r.persona) {
                            <span> · {{ r.persona }} {{ r.signal }}@{{ r.confidence }}</span>
                          }
                        } @else {
                          <span style="color:#7ec27e">— approved</span>
                          <span style="color:var(--text-3)"> (threshold {{ v.threshold_pct }}%)</span>
                        }
                      </li>
                    }
                  </ul>
                </details>
              }

              @if (c.rejected_candidates.length > 0) {
                <details>
                  <summary class="eyebrow" style="cursor:pointer">
                    Rejected candidates ({{ c.rejected_candidates.length }})
                  </summary>
                  <ul class="mono" style="margin:8px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px;font-size:11.5px;color:var(--text-2)">
                    @for (r of c.rejected_candidates; track $index) {
                      <li><span style="color:var(--text)">{{ r.ticker ?? '(sector)' }}</span> — {{ r.reason }}</li>
                    }
                  </ul>
                </details>
              }
            </div>
          </section>
        }
      </div>
    </hf-app-shell>
  `,
})
export class StrategiesDetailPage implements OnInit, OnDestroy {
  readonly store = inject(StrategiesStore);
  private readonly route = inject(ActivatedRoute);
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  running = signal(false);
  estimating = signal(false);
  estimate = signal<{
    n_candidates: number;
    per_call_usd: number;
    est_total_usd: number;
    cost_ceiling_usd: number;
    exceeds_ceiling: boolean;
    per_agent: { agent: string; model: string; model_name: string; tier: string; per_call_usd: number }[];
    overrides: Record<string, string>;
    preset: string;
  } | null>(null);
  notice = signal<string | null>(null);
  cycle = signal<CycleDetail | null>(null);

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
    this.store.estimate(this.strategyId).subscribe({
      next: (e) => { this.estimating.set(false); this.estimate.set(e); },
      error: () => { this.estimating.set(false); this.notice.set('Failed to fetch cost estimate.'); },
    });
  }
  cancelEstimate(): void { this.estimate.set(null); }
  anyAnthropic(est: { per_agent: { model: string }[] }): boolean {
    return est.per_agent.some((r) => r.model.startsWith('anthropic'));
  }
  confirmRun(): void { this.estimate.set(null); this.runNow(); }

  strategyId = 0;

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
  }
  ngOnDestroy(): void { if (this.pollHandle) clearInterval(this.pollHandle); }

  refreshCycles(): void {
    this.store.listCycles(this.strategyId).subscribe((cs) => {
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
    });
  }

  openCycle(id: number): void {
    this.store.cycleDetail(this.strategyId, id).subscribe((d) => {
      const prev = this.cycle();
      this.cycle.set(d);
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

  runNow(): void {
    this.running.set(true);
    this.notice.set(null);
    this.store.runNow(this.strategyId).subscribe({
      next: (r) => {
        this.running.set(false);
        this.notice.set(`Cycle dispatched (task ${r.task_id}). Refreshing every 5s.`);
        if (this.pollHandle) clearInterval(this.pollHandle);
        this.pollHandle = setInterval(() => this.refreshCycles(), 5000);
      },
      error: () => { this.running.set(false); this.notice.set('Failed to dispatch cycle.'); },
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
