import { Component, OnDestroy, OnInit, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { RunsStore } from '../../abstraction/runs.store';
import { AgentMessage, ALL_PERSONAS, DecisionRow, PERSONA_IDS } from '../../core/models/run.model';
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerAccount, BrokerOrderRow } from '../../core/models/broker.model';
import {
  BrokerOrderTicketDecision,
  BrokerOrderTicketModalComponent,
} from '../broker-accounts/broker-order-ticket.modal';
import { OrderConfirmModalComponent } from '../broker-accounts/order-confirm.modal';
import { ConfidenceMeterComponent } from '../shared/confidence-meter.component';
import { EnterPositionModalComponent } from '../portfolio/enter-position.modal';
import { GlossaryTermComponent } from '../shared/glossary-term.component';
import { InfoTooltipComponent } from '../shared/info-tooltip.component';
import { PopoverComponent } from '../shared/popover.component';
import { PositionSide } from '../../core/models/portfolio.model';
import { RangeRailComponent } from '../shared/range-rail.component';
import { TickerComponent } from '../shared/ticker.component';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';

type RunTab = 'decision' | 'council' | 'risk' | 'cio' | 'raw';
const RUN_TABS: readonly RunTab[] = ['decision', 'council', 'risk', 'cio', 'raw'] as const;

interface PersonaCard {
  id: string;
  displayName: string;
  version: string;
  signal: 'bullish' | 'neutral' | 'bearish' | 'unknown';
  confidence: number;
  thesis: string;
  keyRisks: string[];
  intrinsicValue: number | null;
  marginOfSafety: number | null;
}

@Component({
  selector: 'hf-runs-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent, ConfidenceMeterComponent, EnterPositionModalComponent, BrokerOrderTicketModalComponent, OrderConfirmModalComponent, GlossaryTermComponent, InfoTooltipComponent, PopoverComponent, RangeRailComponent, TickerComponent],
  template: `
    <hf-app-shell [crumbs]="crumbs()">
      <div class="page-head">
        <div>
          <div class="eyebrow">Run · {{ run()?.status || '…' }}</div>
          <h1 class="mt-1.5">
            Run #{{ run()?.id }}
            <span class="text-text-3 font-medium">—</span>
            @for (t of (run()?.tickers || []); track t; let last = $last) {
              <hf-ticker [ticker]="t"></hf-ticker>@if (!last) {<span class="text-text-3">, </span>}
            }
          </h1>
        </div>
        <div class="head-actions">
          @if (canCancel()) {
            <button type="button" class="btn danger" (click)="cancel()" [disabled]="cancelling()">
              {{ cancelling() ? 'Cancelling…' : 'Stop analysis' }}
            </button>
          }
          <a class="btn" routerLink="/runs/new">New run</a>
        </div>
      </div>

      @if (!run()) {
        <p class="text-text-3">Loading…</p>
      } @else {
        @if (run()!.source === 'strategy' && run()!.strategy_backlink; as link) {
          <div class="card mb-3.5 bg-surface-2" data-test="strategy-backlink">
            <div class="card-bd flex items-center gap-2.5 flex-wrap text-2xs">
              <span class="pill bg-surface-2 text-text-3 h-auto py-0.5 px-2">
                <span class="dot"></span>Strategy cycle
              </span>
              <span class="text-text-2">
                Triggered by
                <a [routerLink]="['/strategies', link.strategy_id]" class="text-[var(--acc-info-fg)] no-underline font-semibold">
                  {{ link.strategy_name }}
                </a>
                · cycle <span class="mono">{{ link.as_of_date }}</span>
                ·
                <a [routerLink]="['/strategies', link.strategy_id]"
                   [queryParams]="{ cycle: link.portfolio_target_id }"
                   class="text-[var(--acc-info-fg)] no-underline">
                  open cycle →
                </a>
              </span>
            </div>
          </div>
        }

        @if (personalized(); as p) {
          <div class="card mb-3.5 bg-surface-2" data-test="personalized-badge">
            <div class="card-bd flex items-center gap-2.5 flex-wrap text-2xs">
              <span class="pill"
                    tabindex="0"
                    [attr.aria-describedby]="popPersonalized.open() ? popPersonalized.popoverId : null"
                    (mouseenter)="popPersonalized.show()" (mouseleave)="popPersonalized.maybeHide()"
                    (focus)="popPersonalized.show()" (blur)="popPersonalized.maybeHide()">
                <span class="dot"></span>Personalized
                <hf-popover #popPersonalized placement="bottom" align="start" size="default" role="tooltip">
                  <strong class="block mb-1">Investor profile applied</strong>
                  <span class="block text-text-3 text-[11px]">{{ p }}</span>
                </hf-popover>
              </span>
              <span class="text-text-2">
                The CIO, persona analysts and Risk Manager narrative for this
                run were calibrated to your investor profile.
              </span>
              <a routerLink="/profile" class="text-[var(--acc-info-fg)] no-underline ml-auto">
                View profile →
              </a>
            </div>
          </div>
        }

        @if (evolutionApplied(); as ev) {
          <div class="card mb-3.5 bg-surface-2" data-test="evolved-badge">
            <div class="card-bd flex items-center gap-2.5 flex-wrap text-2xs">
              <span class="pill"
                    tabindex="0"
                    [attr.aria-describedby]="popEvolved.open() ? popEvolved.popoverId : null"
                    (mouseenter)="popEvolved.show()" (mouseleave)="popEvolved.maybeHide()"
                    (focus)="popEvolved.show()" (blur)="popEvolved.maybeHide()">
                <span class="dot"></span>Evolved
                <hf-popover #popEvolved placement="bottom" align="start" size="default" role="tooltip">
                  <strong class="block mb-1">Persona evolution applied</strong>
                  <span class="block text-text-3 text-[11px]">
                    @for (entry of evolutionEntries(); track entry.persona) {
                      <span class="block">{{ entry.persona }} · seq {{ entry.seq }}</span>
                    }
                  </span>
                </hf-popover>
              </span>
              <span class="text-text-2">
                Real-world dossiers for
                {{ evolutionEntries().length }} persona{{ evolutionEntries().length === 1 ? '' : 's' }}
                were appended to their reasoning prompts for this run.
              </span>
              <a routerLink="/settings/models" class="text-[var(--acc-info-fg)] no-underline ml-auto">
                Persona evolution settings →
              </a>
            </div>
          </div>
        }

        @if (run()!.error_message) {
          <section class="card mb-3.5 border-[var(--acc-short-soft)]">
            <div class="card-bd">
              <p class="mono text-[var(--acc-short-fg)] text-2xs m-0 whitespace-pre-wrap">{{ run()!.error_message }}</p>
            </div>
          </section>
        }

        <!-- Tab strip (ADR 0002) -->
        <nav class="tabs mb-3.5" role="tablist" aria-label="Run sections">
          @for (t of tabs; track t.id) {
            <button type="button"
              role="tab"
              class="tab"
              [class.active]="activeTab() === t.id"
              [id]="'tabbtn-' + t.id"
              [attr.aria-controls]="'tab-' + t.id"
              [attr.aria-selected]="activeTab() === t.id"
              [attr.tabindex]="activeTab() === t.id ? 0 : -1"
              [attr.data-tab-id]="t.id"
              (click)="setActiveTab(t.id)"
              (keydown)="onTabKeydown($event, t.id)">
              {{ t.label }}
              @if (tabCounts()[t.id]; as c) {
                <span class="count" [class.attn]="c.attn">{{ c.label }}</span>
              }
            </button>
          }
        </nav>

        @if (activeTab() === 'decision') {
          <div role="tabpanel" id="tab-decision" aria-labelledby="tabbtn-decision" tabindex="0" class="decision-grid">
            <div class="decision-main">
              <!-- Status strip -->
              <section class="card mb-3.5">
                <div class="card-bd grid grid-cols-4 gap-6">
                  <div>
                    <div class="eyebrow">Status</div>
                    <span class="pill"
                      [class.ok]="run()!.status==='done'"
                      [class.err]="run()!.status==='failed'"
                      [class.warn]="run()!.status==='running' || run()!.status==='queued'">
                      <span class="dot"></span>{{ run()!.status }}
                    </span>
                  </div>
                  <div>
                    <div class="eyebrow">As-of</div>
                    <div class="mono text-sm mt-1">{{ run()!.as_of_date }}</div>
                  </div>
                  <div>
                    <div class="eyebrow">Personas</div>
                    <div class="mono text-sm mt-1">{{ personas().length }}</div>
                  </div>
                  <div>
                    <div class="eyebrow">Total cost</div>
                    <div class="mono text-sm mt-1">$ {{ formatCost(run()!.total_cost_usd) }}</div>
                  </div>
                </div>
              </section>

              <!-- Trade levels (WS-5.4.2 range-rails) -->
              @if (statGridVisible()) {
                <section class="card mb-3.5" data-test="trade-levels">
                  <div class="card-hd"><span class="title">Trade levels</span></div>
                  <div class="card-bd">
                    <div class="stat-grid">
                      @if (targetZoneCell(); as tz) {
                        <div class="stat-cell">
                          <div class="eyebrow">Target zone</div>
                          <div class="stat-value mono">{{ fmtMoney(tz.value) }}</div>
                          <hf-range-rail
                            [min]="tz.min" [max]="tz.max" [value]="tz.value"
                            [bandLo]="tz.bandLo" [bandHi]="tz.bandHi"
                            tone="neutral"
                            [format]="fmtMoney"
                            [ariaLabel]="'Current price ' + fmtMoney(tz.value) + ' inside fair-value band ' + fmtMoney(tz.bandLo) + ' to ' + fmtMoney(tz.bandHi)" />
                        </div>
                      }
                      @if (stopLossCell(); as st) {
                        <div class="stat-cell" [class.dim]="isVetoed()">
                          <div class="eyebrow flex items-center gap-1.5">
                            <span><hf-term key="stop-loss">Stop loss</hf-term></span>
                            @if (isVetoed()) { <span class="pill err"><span class="dot"></span>VETO</span> }
                          </div>
                          <div class="stat-value mono">{{ fmtPctDecimal(st.value) }}</div>
                          <hf-range-rail
                            [min]="st.min" [max]="st.max" [value]="st.value"
                            tone="short"
                            [format]="fmtPctDecimal"
                            [ariaLabel]="'Stop loss ' + fmtPctDecimal(st.value)" />
                        </div>
                      }
                      @if (expectedReturnCell(); as er) {
                        <div class="stat-cell">
                          <div class="eyebrow">Expected return</div>
                          <div class="stat-value mono">{{ fmtSignedPctScaled(er.value) }}</div>
                          <hf-range-rail
                            [min]="er.min" [max]="er.max" [value]="er.value"
                            [tone]="er.tone"
                            [format]="fmtSignedPctScaled"
                            [ariaLabel]="'Expected return ' + fmtSignedPctScaled(er.value)" />
                        </div>
                      }
                      @if (drawdownCell(); as dd) {
                        <div class="stat-cell" [class.dim]="isVetoed()">
                          <div class="eyebrow flex items-center gap-1.5">
                            <span><hf-term key="drawdown">Drawdown</hf-term> at stop</span>
                            @if (isVetoed()) { <span class="pill err"><span class="dot"></span>VETO</span> }
                          </div>
                          <div class="stat-value mono">{{ fmtSignedPctDecimal(dd.value) }}</div>
                          <hf-range-rail
                            [min]="dd.min" [max]="dd.max" [value]="dd.value"
                            tone="short"
                            [format]="fmtSignedPctDecimal"
                            [ariaLabel]="'Drawdown at stop ' + fmtSignedPctDecimal(dd.value)" />
                        </div>
                      }
                    </div>
                  </div>
                </section>
              }

              <!-- Macro -->
              @if (macroOutput(); as m) {
                <section class="card mb-3.5">
                  <div class="card-hd"><span class="title">Macro context</span></div>
                  <div class="card-bd">
                    <div class="flex flex-wrap gap-1.5 mb-2.5">
                      <span class="pill"><span class="dot"></span>growth · {{ m['growth_quadrant'] }}</span>
                      <span class="pill"><span class="dot"></span>inflation · {{ m['inflation_regime'] }}</span>
                      <span class="pill"><span class="dot"></span><hf-term key="yield-curve">curve</hf-term> · {{ m['yield_curve_state'] }}</span>
                      <span class="pill"><span class="dot"></span>policy · {{ m['policy_stance'] }}</span>
                    </div>
                    <p class="text-xs text-text-2 m-0">{{ m['narrative'] }}</p>
                  </div>
                </section>
              }

              <!-- News & filings -->
              @if (newsOutput(); as n) {
                <section class="card mb-3.5">
                  <div class="card-hd"><span class="title">News &amp; filings</span></div>
                  <div class="card-bd">
                    <p class="text-xs text-text-2 m-0 mb-3">{{ n['digest'] }}</p>
                    @if (asArray(n['risk_factor_highlights']).length) {
                      <div class="eyebrow mb-1.5">Risk factor highlights</div>
                      <ul class="m-0 pl-[18px] text-xs text-text-2">
                        @for (r of asArray(n['risk_factor_highlights']); track r) { <li>{{ r }}</li> }
                      </ul>
                    }
                    @if (asAnyArray(n['material_events']).length) {
                      <div class="eyebrow m-0 mt-3 mb-1.5">Material events</div>
                      <ul class="m-0 p-0 list-none text-xs">
                        @for (e of asAnyArray(n['material_events']); track e['url']) {
                          <li class="border-t border-solid border-border py-1.5 px-0 flex items-center gap-1.5 flex-wrap">
                            <span class="mono text-[11.5px] text-text-3">{{ e['date'] }}</span>
                            <span class="pill"><span class="dot"></span>{{ e['tag'] }}</span>
                            <span class="mono text-[11px] text-text-3">m={{ e['materiality'] }}</span>
                            <a [href]="e['url']" target="_blank" class="text-[var(--acc-info-fg)]">{{ e['headline'] }}</a>
                          </li>
                        }
                      </ul>
                    }
                    <p class="text-[11.5px] text-text-3 m-0 mt-2.5">
                      Sentiment: <span class="mono">{{ num(n['sentiment_score']) }}</span>
                      @if (asArray(n['sentiment_drivers']).length) {
                        · drivers: {{ asArray(n['sentiment_drivers']).join('; ') }}
                      }
                    </p>
                  </div>
                </section>
              }
            </div>

            <!-- Sticky right rail: final order ticket(s) (WS-5.4.5) -->
            <aside class="order-ticket-rail" aria-label="Order ticket">
              @for (d of run()!.decisions; track d.id) {
                <section class="card border-l-[3px] border-solid"
                  [style.borderLeftColor]="d.action === 'buy' ? 'var(--acc-long)' : d.action === 'sell' ? 'var(--acc-short)' : 'var(--acc-hold)'">
                  <div class="card-bd">
                    <div class="flex flex-col gap-3">
                      <div>
                        <h2 class="text-base font-semibold m-0 flex items-center gap-1.5 flex-wrap">
                          <span>{{ d.action.toUpperCase() }}</span>
                          <hf-ticker [ticker]="d.ticker"></hf-ticker>
                        </h2>
                        <div class="text-2xs text-text-3 mt-2 flex gap-1.5 items-center flex-wrap">
                          @if (d.risk_overrides.veto) {
                            <span class="pill err"><span class="dot"></span>RISK VETO</span>
                          }
                          @if (cioOutput()?.['overrode_pm']) {
                            <span class="pill warn"><span class="dot"></span>CIO OVERRIDE</span>
                          }
                        </div>
                      </div>
                      <hf-confidence-meter
                        [value]="d.confidence"
                        [tone]="d.action === 'buy' ? 'buy' : d.action === 'sell' ? 'sell' : 'hold'"
                        [counts]="stanceCounts()" />
                      <div class="text-xs">
                        <p class="m-0">
                          Target qty: <span class="mono text-text">{{ d.target_quantity }}</span>
                          <hf-info text="Illustrative — computed against a $100K stub portfolio. Replaced by your real broker account balance in P3a (paper trading)."></hf-info>
                        </p>
                        <p class="m-0 mt-0.5">
                          Target weight: <span class="mono text-text">{{ d.target_weight_pct }}%</span>
                        </p>
                        @if (canAddToPortfolio(d)) {
                          <div class="flex flex-col gap-1 mt-2.5">
                            <button class="btn primary sm"
                                    (click)="addToPortfolio(d)"
                                    [attr.data-test]="'add-to-portfolio-' + d.ticker">
                              <svg width="12" height="12" class="mr-1">
                                <use href="/icons.svg#i-plus" />
                              </svg>
                              Add to portfolio
                            </button>
                            <button class="btn sm"
                                    (click)="submitAsBrokerOrder(d)"
                                    [attr.data-test]="'submit-broker-order-' + d.ticker">
                              <svg width="12" height="12" class="mr-1">
                                <use href="/icons.svg#i-link" />
                              </svg>
                              Submit as broker order
                            </button>
                          </div>
                        } @else if (d.side === 'pair') {
                          <p class="m-0 mt-2 text-[11px] text-text-3">
                            Pair decisions can't be entered manually (v1).
                          </p>
                        } @else if (run()!.status !== 'done') {
                          <p class="m-0 mt-2 text-[11px] text-text-3">
                            Wait for the run to complete to enter a position.
                          </p>
                        }
                      </div>
                      <p class="text-xs leading-5 text-text-2 m-0 whitespace-pre-wrap">{{ d.rationale }}</p>
                    </div>
                  </div>
                </section>
              }
            </aside>
          </div>
        }

        @if (activeTab() === 'council') {
          <div role="tabpanel" id="tab-council" aria-labelledby="tabbtn-council" tabindex="0">
            <section class="mb-3.5">
              <h2 class="eyebrow m-0 mb-2.5">Council members</h2>
              <div class="grid grid-cols-[repeat(3,minmax(0,1fr))] gap-3">
                @for (p of personas(); track p.id) {
                  <article class="card border-t-[3px] border-solid"
                    [style.borderTopColor]="p.signal === 'bullish' ? 'var(--acc-long)' : p.signal === 'bearish' ? 'var(--acc-short)' : p.signal === 'neutral' ? 'var(--acc-hold)' : 'var(--border-2)'">
                    <div class="card-bd">
                      <div class="flex justify-between items-baseline">
                        <h3 class="text-sm font-semibold m-0">{{ p.displayName }}</h3>
                        <span class="mono text-[11px] text-text-3">{{ p.id }}&#64;{{ p.version }}</span>
                      </div>
                      <p class="text-xs m-0 mt-1.5">
                        <span class="font-medium uppercase">{{ p.signal }}</span>
                        <span class="text-text-3"> · {{ p.confidence }}% confidence</span>
                      </p>
                      <p [id]="'thesis-' + p.id" class="text-xs text-text-2 m-0 mt-2 leading-[18px]">
                        {{ expanded().has(p.id) ? p.thesis : truncate(p.thesis, 200) }}
                      </p>
                      @if (p.thesis.length > 200) {
                        <button type="button"
                          class="thesis-expand"
                          [attr.aria-expanded]="expanded().has(p.id)"
                          [attr.aria-controls]="'thesis-' + p.id"
                          (click)="toggleExpand(p.id)">
                          {{ expanded().has(p.id) ? 'Collapse' : 'See full reasoning' }}
                        </button>
                      }
                      @if (p.keyRisks.length) {
                        <p class="text-[11.5px] text-text-3 m-0 mt-2">
                          Risks: {{ p.keyRisks.join('; ') }}
                        </p>
                      }
                      @if (p.intrinsicValue !== null) {
                        <p class="text-[11.5px] text-text-3 m-0 mt-1">
                          IV $ {{ p.intrinsicValue }}, <hf-term key="margin-of-safety">MoS</hf-term> {{ p.marginOfSafety }}%
                        </p>
                      }
                    </div>
                  </article>
                }
              </div>
            </section>

            <!-- Dissent -->
            @if (dissent().length) {
              <section class="card mb-3.5 bg-[var(--acc-hold-soft)] border-[var(--acc-hold-soft)]">
                <div class="card-bd">
                  <h2 class="text-sm font-semibold m-0 mb-2 text-[var(--acc-hold-fg)]">Dissenting views</h2>
                  <ul class="m-0 p-0 list-none flex flex-col gap-1.5 text-xs">
                    @for (d of dissent(); track d.name) {
                      <li>
                        <span class="font-medium">{{ displayName(d.name) }}</span>
                        ({{ d.signal }}, {{ d.confidence }}%):
                        <span class="text-text-2">{{ d.thesis_summary }}</span>
                      </li>
                    }
                  </ul>
                </div>
              </section>
            }
          </div>
        }

        @if (activeTab() === 'risk') {
          <div role="tabpanel" id="tab-risk" aria-labelledby="tabbtn-risk" tabindex="0">
            <h2 class="eyebrow m-0 mb-2.5">Risk analysis</h2>
            <!-- Risk Manager -->
            @if (riskOutput(); as risk) {
              <section class="card mb-3.5">
                <div class="card-hd">
                  <span class="title"><hf-term key="rm">Risk Manager</hf-term></span>
                  @if (risk['veto']) {
                    <span class="pill err"><span class="dot"></span><hf-term key="veto">VETO</hf-term></span>
                  }
                </div>
                <div class="card-bd">
                  <div class="grid grid-cols-3 gap-4 text-xs">
                    <div>
                      <div class="eyebrow">Max position</div>
                      <div class="mono mt-1">{{ pct(risk['max_position_pct_for_this_trade']) }}</div>
                    </div>
                    <div>
                      <div class="eyebrow"><hf-term key="stop-loss">Stop loss</hf-term></div>
                      <div class="mono mt-1">{{ pct(risk['stop_loss_pct']) }}</div>
                    </div>
                    <div>
                      <div class="eyebrow">Hard caps applied</div>
                      <div class="mono mt-1 text-[11.5px]">
                        {{ (asArray(risk['hard_caps_applied'])).join(', ') || 'none' }}
                      </div>
                    </div>
                  </div>
                  <p class="text-xs text-text-2 m-0 mt-3.5 whitespace-pre-wrap">{{ risk['rationale'] }}</p>
                </div>
              </section>
            }

            <!-- Risk context (P02a review) -->
            @if (riskContext(); as rc) {
              <section class="card mb-3.5" data-test="risk-context">
                <div class="card-hd">
                  <span class="title">Risk context</span>
                  <span class="pill"
                    [class.warn]="rc.mode === 'stub'"
                    [class.ok]="rc.mode === 'real'">
                    <span class="dot"></span>{{ rc.mode || 'unknown' }}
                  </span>
                </div>
                <div class="card-bd">
                  <p class="text-xs text-text-2 m-0">{{ rc.notes }}</p>
                  @if (rc.mode === 'stub' && rc.stub_nav_usd) {
                    <p class="text-[11.5px] text-text-3 m-0 mt-1.5">
                      Stub NAV: <span class="mono">$ {{ rc.stub_nav_usd }}</span>
                    </p>
                  }
                  @if (rc.mode === 'real' && rc.cash_balance_usd) {
                    <p class="text-[11.5px] text-text-3 m-0 mt-1.5">
                      Portfolio cash: <span class="mono">$ {{ rc.cash_balance_usd }}</span>
                    </p>
                  }
                </div>
              </section>
            }

            <!-- Valuation -->
            @if (valuationOutput(); as v) {
              <section class="card mb-3.5">
                <div class="card-hd"><span class="title">Valuation</span></div>
                <div class="card-bd">
                  <div class="grid grid-cols-4 gap-3.5 text-xs">
                    <div><div class="eyebrow"><hf-term key="dcf">DCF</hf-term></div><div class="mono mt-1">{{ num(v['dcf_fair_value']) }}</div></div>
                    <div><div class="eyebrow">Multiples</div><div class="mono mt-1">{{ num(v['multiples_fair_value']) }}</div></div>
                    <div><div class="eyebrow">Residual income</div><div class="mono mt-1">{{ num(v['residual_income_fair_value']) }}</div></div>
                    <div><div class="eyebrow">Current price</div><div class="mono mt-1">{{ num(v['current_price']) }}</div></div>
                    <div><div class="eyebrow"><hf-term key="fair-value">FV</hf-term> low</div><div class="mono mt-1">{{ num(v['fair_value_low']) }}</div></div>
                    <div><div class="eyebrow"><hf-term key="fair-value">FV</hf-term> high</div><div class="mono mt-1">{{ num(v['fair_value_high']) }}</div></div>
                    <div class="col-span-2"><div class="eyebrow">Upside</div><div class="mono mt-1">{{ num(v['upside_pct']) }}%</div></div>
                  </div>
                  <p class="text-[11.5px] text-text-3 m-0 mt-2.5">
                    Most sensitive: {{ v['most_sensitive_assumption'] }}
                  </p>
                </div>
              </section>
            }

            @if (!riskOutput() && !riskContext() && !valuationOutput()) {
              <p class="text-text-3">No risk output recorded for this run.</p>
            }
          </div>
        }

        @if (activeTab() === 'cio') {
          <div role="tabpanel" id="tab-cio" aria-labelledby="tabbtn-cio" tabindex="0">
            @if (cioOutput(); as c) {
              <section class="card mb-3.5 border-l-[3px] border-solid"
                [style.borderLeftColor]="c['overrode_pm'] ? 'var(--acc-hold)' : 'var(--acc-info)'">
                <div class="card-bd">
                  <div class="flex justify-between items-start">
                    <h2 class="text-base font-semibold m-0 flex items-center gap-2">
                      Chief Investment Officer
                      @if (c['overrode_pm']) {
                        <span class="pill warn"><span class="dot"></span>OVERRIDE</span>
                      } @else {
                        <span class="pill info"><span class="dot"></span>RATIFIED PM</span>
                      }
                    </h2>
                    <span class="mono text-[11px] text-text-3">confidence {{ c['confidence'] }}</span>
                  </div>
                  <p class="text-xs text-text-2 m-0 mt-2.5">{{ c['outlook'] }}</p>
                  @if (c['overrode_pm'] && c['override_reason']) {
                    <p class="text-xs m-0 mt-2">
                      <span class="font-semibold">Override reason:</span> {{ c['override_reason'] }}
                    </p>
                  }
                  @if (c['stop_loss_pct']) {
                    <p class="text-[11px] text-text-3 m-0 mt-2">
                      Stop loss: <span class="mono">{{ pct(c['stop_loss_pct']) }}</span>
                    </p>
                  }
                  @if (pmDecisionMsg(); as pm) {
                    <details class="mt-3 text-[11px] text-text-3">
                      <summary class="cursor-pointer">Original PM ticket (pre-CIO)</summary>
                      <pre class="m-0 mt-1.5 bg-surface-2 p-2 rounded-sm overflow-auto font-mono text-[11.5px] text-text-2">action: {{ pm['action'] }}, weight: {{ pm['target_weight_pct'] }}%, qty: {{ pm['target_quantity'] }}
{{ pm['rationale'] }}</pre>
                    </details>
                  }
                </div>
              </section>
            } @else {
              <p class="text-text-3">No CIO output recorded for this run.</p>
            }
          </div>
        }

        @if (activeTab() === 'raw') {
          <div role="tabpanel" id="tab-raw" aria-labelledby="tabbtn-raw" tabindex="0">
            <h2 class="eyebrow m-0 mb-2.5">Raw artifacts</h2>
            <!-- Evidence trail (P01 review) -->
            @if (evidenceItems().length || providerStateEntries().length) {
              <section class="card mb-3.5" data-test="evidence-trail">
                <div class="card-hd"><span class="title">Evidence &amp; sources</span></div>
                <div class="card-bd">
                  @if (providerStateEntries().length) {
                    <div class="eyebrow mb-1.5">Provider status</div>
                    <div class="flex flex-wrap gap-1.5 mb-3">
                      @for (p of providerStateEntries(); track p.name) {
                        <span class="pill"
                          [class.ok]="p.state === 'configured'"
                          [class.err]="p.state === 'missing'">
                          <span class="dot"></span>{{ p.name }} · {{ p.state }}
                        </span>
                      }
                    </div>
                  }
                  @if (evidenceItems().length) {
                    <div class="eyebrow mb-1.5">Citations</div>
                    <ul class="m-0 p-0 list-none text-xs">
                      @for (it of evidenceItems(); track it.label + it.source + it.url) {
                        <li class="border-t border-solid border-border py-1.5 px-0 flex flex-wrap gap-2 items-center">
                          <span class="pill"><span class="dot"></span>{{ it.agent }}</span>
                          <span class="text-text-2">{{ it.label }}</span>
                          <span class="mono text-[11px] text-text-3">
                            {{ it.provider }}{{ it.as_of ? ' · ' + it.as_of : '' }}
                          </span>
                          @if (it.url) {
                            <a [href]="it.url" target="_blank" rel="noopener" class="text-[var(--acc-info-fg)] text-[11.5px]">open ↗</a>
                          }
                        </li>
                      }
                    </ul>
                  }
                </div>
              </section>
            }

            <!-- LLM calls -->
            @if (run()!.llm_calls.length) {
              <section class="card">
                <div class="card-hd"><span class="title">LLM calls</span></div>
                <table class="tbl">
                  <thead><tr>
                    <th>Agent</th><th>Provider</th><th>Model</th>
                    <th class="right">In</th><th class="right">Out</th>
                    <th class="right">Cost</th><th class="right">ms</th>
                  </tr></thead>
                  <tbody>
                    @for (c of run()!.llm_calls; track c.id) {
                      <tr>
                        <td>{{ c.agent_name }}</td>
                        <td class="text-text-2">{{ c.provider }}</td>
                        <td class="mono text-[11.5px]">{{ c.model }}</td>
                        <td class="num">{{ c.prompt_tokens }}</td>
                        <td class="num">{{ c.completion_tokens }}</td>
                        <td class="num">$ {{ formatCost(c.cost_usd) }}</td>
                        <td class="num">{{ c.latency_ms }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </section>
            }

            @if (!evidenceItems().length && !providerStateEntries().length && !run()!.llm_calls.length) {
              <p class="text-text-3">No raw artifacts recorded for this run.</p>
            }
          </div>
        }

        <!-- Always-rendered floating UI -->
        <hf-enter-position-modal
          [open]="entryOpen()"
          [prefill]="entryPrefill()"
          (closed)="onEntryClosed($event)" />

        <!-- Submit-as-broker-order flow: ticket → gated confirm → broker. -->
        @if (brokerTicket(); as t) {
          <hf-broker-order-ticket-modal
            [decision]="t.decision"
            [accounts]="t.accounts"
            (closed)="brokerTicket.set(null)"
            (review)="onTicketReview($event)" />
        }
        @if (confirmingOrder(); as ord) {
          <hf-order-confirm-modal
            [order]="ord"
            [account]="confirmingAccount()"
            (closed)="onBrokerConfirmClosed()"
            (confirmed)="onBrokerConfirmed($event)" />
        }

        <div role="status" aria-live="polite" class="visually-hidden">
          @if (toastMsg(); as t) { {{ t }} }
        </div>
        @if (toastMsg(); as t) {
          <div class="pill ok fixed bottom-[18px] right-[18px] h-auto py-2 px-3 z-[var(--z-modal)]"
               data-test="position-saved-toast">
            <span class="dot"></span>{{ t }}
            <a routerLink="/portfolio"
               class="ml-1.5 text-[var(--acc-info-fg)] underline">View portfolio →</a>
          </div>
        }
      }
    </hf-app-shell>
  `,
  styles: [`
    .thesis-expand {
      font-size: 11.5px;
      color: var(--acc-info-fg);
      margin-top: 4px;
      background: transparent;
      border: 0;
      cursor: pointer;
      padding: 0;
    }
  `],
})
export class RunsDetailPage implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly store = inject(RunsStore);
  private readonly profiles = inject(TickerProfileStore);
  readonly run = this.store.currentRun;
  readonly expanded = signal<Set<string>>(new Set());
  readonly cancelling = signal(false);
  private prefetchedTickers = new Set<string>();

  // Tabs (ADR 0002): five-tab decomposition of the long single-scroll page.
  readonly tabs: { id: RunTab; label: string }[] = [
    { id: 'decision', label: 'Decision' },
    { id: 'council', label: 'Council' },
    { id: 'risk', label: 'Risk' },
    { id: 'cio', label: 'CIO' },
    { id: 'raw', label: 'Raw' },
  ];
  readonly activeTab = signal<RunTab>('decision');

  readonly tabCounts = computed<Partial<Record<RunTab, { label: string; attn: boolean }>>>(() => {
    const personas = this.personas().length;
    const evidence = this.evidenceItems().length;
    const llmCalls = this.run()?.llm_calls?.length ?? 0;
    const risk = this.riskOutput();
    const cio = this.cioOutput();
    const raw = evidence + llmCalls;
    return {
      council: personas ? { label: String(personas), attn: false } : undefined,
      risk: risk?.['veto'] ? { label: 'VETO', attn: true } : undefined,
      cio: cio?.['overrode_pm'] ? { label: 'OVERRIDE', attn: true } : undefined,
      raw: raw ? { label: String(raw), attn: false } : undefined,
    };
  });

  constructor() {
    // WS-2: prefetch identity for every ticker the run + its decisions reference,
    // so popovers on the heading and decision cards open with the name already
    // cached. Idempotent — skips tickers already in flight or resolved.
    effect(() => {
      const r = this.run();
      if (!r) return;
      const tickers = new Set<string>();
      (r.tickers || []).forEach((t) => tickers.add(t));
      (r.decisions || []).forEach((d) => d.ticker && tickers.add(d.ticker));
      const fresh = [...tickers].filter((t) => !this.prefetchedTickers.has(t));
      if (!fresh.length) return;
      fresh.forEach((t) => this.prefetchedTickers.add(t));
      this.profiles.fetchNames(fresh).subscribe();
    });
  }

  crumbs = computed(() => [
    { label: 'Runs', link: '/runs' },
    { label: `#${this.run()?.id ?? ''}` },
  ]);

  readonly canCancel = computed(() => {
    const s = this.run()?.status;
    return s === 'queued' || s === 'running';
  });

  /** P3-prereq-5 WS-C/WS-D: agent_brief that was injected for this run,
   *  empty string when no personalization was applied. */
  readonly personalized = computed<string | null>(() => {
    const meta = this.run()?.investor_profile_applied as
      | { applied?: boolean; agent_brief?: string }
      | undefined;
    if (!meta?.applied) return null;
    return meta.agent_brief || '(no agent brief recorded)';
  });

  /** P3-D WS-D: per-persona evolving notes injected into this run. Empty
   *  object when no persona had an applicable revision (live-only by design;
   *  always empty for backtest-derived data). */
  readonly evolutionApplied = computed<{
    asOfDate: string;
    revisions: Record<string, number>;
  } | null>(() => {
    const meta = this.run()?.persona_evolution_applied as
      | { applied?: boolean; as_of_date?: string; revisions?: Record<string, number> }
      | undefined;
    if (!meta?.applied || !meta.revisions) return null;
    if (Object.keys(meta.revisions).length === 0) return null;
    return {
      asOfDate: meta.as_of_date || '',
      revisions: meta.revisions,
    };
  });

  readonly evolutionEntries = computed<{ persona: string; seq: number }[]>(() => {
    const applied = this.evolutionApplied();
    if (!applied) return [];
    return Object.entries(applied.revisions).map(([persona, seq]) => ({
      persona,
      seq: Number(seq),
    }));
  });

  cancel(): void {
    const id = this.run()?.id;
    if (!id) return;
    this.cancelling.set(true);
    this.store.cancelRun(id).subscribe({
      next: () => {
        this.cancelling.set(false);
        this.store.pollRun(id);
      },
      error: () => this.cancelling.set(false),
    });
  }

  private readonly messageByAgent = computed(() => {
    const map = new Map<string, AgentMessage>();
    for (const m of this.run()?.messages ?? []) map.set(m.agent_name, m);
    return map;
  });

  readonly personas = computed<PersonaCard[]>(() => {
    const run = this.run();
    if (!run) return [];
    const versions = run.agent_versions ?? {};
    const msgs = this.messageByAgent();
    const out: PersonaCard[] = [];
    for (const p of ALL_PERSONAS) {
      if (!msgs.has(p.id)) continue;
      const payload = msgs.get(p.id)!.parsed_output as Record<string, unknown>;
      out.push({
        id: p.id,
        displayName: p.name,
        version: versions[p.id] ?? '—',
        signal: (payload['signal'] as PersonaCard['signal']) ?? 'unknown',
        confidence: Number(payload['confidence'] ?? 0),
        thesis: String(payload['thesis'] ?? ''),
        keyRisks: Array.isArray(payload['key_risks']) ? (payload['key_risks'] as string[]) : [],
        intrinsicValue: payload['intrinsic_value_estimate'] as number | null,
        marginOfSafety: payload['margin_of_safety_pct'] as number | null,
      });
    }
    return out;
  });

  readonly riskOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('risk');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly valuationOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('valuation');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly macroOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('macro');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly newsOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('news_digest');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly cioOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('cio');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly pmDecisionMsg = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('pm_decision');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  // WS-5.4.2: range-rail cells on the Decision tab. Each cell returns null
  // when its source data is absent — the template hides the cell individually
  // rather than fabricating placeholder values.
  readonly targetZoneCell = computed<{
    min: number; max: number; value: number; bandLo: number; bandHi: number;
  } | null>(() => {
    const v = this.valuationOutput();
    if (!v) return null;
    const fvLo = Number(v['fair_value_low']);
    const fvHi = Number(v['fair_value_high']);
    const cp = Number(v['current_price']);
    if (!Number.isFinite(fvLo) || !Number.isFinite(fvHi) || !Number.isFinite(cp)) return null;
    const min = Math.floor(Math.min(fvLo, cp) * 0.9);
    const max = Math.ceil(Math.max(fvHi, cp) * 1.1);
    return { min, max, value: cp, bandLo: fvLo, bandHi: fvHi };
  });

  readonly stopLossCell = computed<{ min: number; max: number; value: number } | null>(() => {
    const r = this.riskOutput();
    if (!r) return null;
    const v = Number(r['stop_loss_pct']);
    if (!Number.isFinite(v)) return null;
    return { min: 0, max: 0.20, value: v };
  });

  readonly expectedReturnCell = computed<{
    min: number; max: number; value: number; tone: 'long' | 'short' | 'neutral';
  } | null>(() => {
    const v = this.valuationOutput();
    if (!v) return null;
    const up = Number(v['upside_pct']);
    if (!Number.isFinite(up)) return null;
    const span = Math.max(5, Math.abs(up));
    const tone: 'long' | 'short' | 'neutral' = up > 0 ? 'long' : up < 0 ? 'short' : 'neutral';
    return { min: -span, max: span, value: up, tone };
  });

  readonly drawdownCell = computed<{ min: number; max: number; value: number } | null>(() => {
    const r = this.riskOutput();
    if (!r) return null;
    const d = this.run()?.decisions?.[0];
    if (!d) return null;
    const stop = Number(r['stop_loss_pct']);
    const tw = Number(d.target_weight_pct);
    if (!Number.isFinite(stop) || !Number.isFinite(tw)) return null;
    return { min: -0.05, max: 0, value: -(tw / 100) * stop };
  });

  readonly statGridVisible = computed(() =>
    !!(this.targetZoneCell() || this.stopLossCell() || this.expectedReturnCell() || this.drawdownCell()),
  );

  readonly isVetoed = computed(() => !!(this.riskOutput()?.['veto']));

  // Format callbacks bound as instance arrow functions so they can be passed
  // straight to hf-range-rail without losing `this` context.
  readonly fmtMoney = (v: number) => `$ ${v.toFixed(2)}`;
  readonly fmtPctDecimal = (v: number) => `${(v * 100).toFixed(1)}%`;
  readonly fmtSignedPctScaled = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
  readonly fmtSignedPctDecimal = (v: number) => `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;

  // P02a review: surface the stub/real label so users don't treat
  // illustrative target sizing as portfolio-grade advice.
  readonly riskContext = computed(() => {
    const rc = this.run()?.risk_context;
    if (!rc || !rc.mode) return null;
    return rc;
  });

  // P01 review: provider + citation list rendered as the evidence trail.
  readonly evidenceItems = computed(() => {
    const items = this.run()?.evidence?.items ?? [];
    return Array.isArray(items) ? items : [];
  });

  readonly providerStateEntries = computed(() => {
    const providers = this.run()?.evidence?.providers ?? {};
    return Object.entries(providers).map(([name, state]) => ({ name, state }));
  });

  asAnyArray(v: unknown): Record<string, unknown>[] {
    return Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
  }

  readonly stanceCounts = computed(() => {
    const cards = this.personas();
    return {
      bull: cards.filter((p) => p.signal === 'bullish').length,
      bear: cards.filter((p) => p.signal === 'bearish').length,
      neutral: cards.filter((p) => p.signal === 'neutral').length,
    };
  });

  readonly dissent = computed(() => {
    const run = this.run();
    if (!run) return [];
    const seen = new Set<string>();
    const out: { name: string; signal: string; confidence: number; thesis_summary: string }[] = [];
    for (const d of run.decisions) {
      for (const v of d.dissenting_views ?? []) {
        if (seen.has(v.name)) continue;
        seen.add(v.name);
        out.push(v);
      }
    }
    return out;
  });

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    if (id) this.store.pollRun(id);
    const t = this.route.snapshot.queryParamMap.get('tab');
    if (t && this.isRunTab(t)) this.activeTab.set(t);
  }

  ngOnDestroy(): void { this.store.stopPolling(); }

  private isRunTab(t: string): t is RunTab {
    return (RUN_TABS as readonly string[]).includes(t);
  }

  setActiveTab(t: RunTab): void {
    if (this.activeTab() === t) return;
    this.activeTab.set(t);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { tab: t === 'decision' ? null : t },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  onTabKeydown(ev: KeyboardEvent, current: RunTab): void {
    const idx = this.tabs.findIndex((t) => t.id === current);
    let next = idx;
    if (ev.key === 'ArrowRight') next = (idx + 1) % this.tabs.length;
    else if (ev.key === 'ArrowLeft') next = (idx - 1 + this.tabs.length) % this.tabs.length;
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = this.tabs.length - 1;
    else return;
    ev.preventDefault();
    const nextTab = this.tabs[next].id;
    this.setActiveTab(nextTab);
    queueMicrotask(() => {
      document.querySelector<HTMLElement>(`[data-tab-id="${nextTab}"]`)?.focus();
    });
  }

  toggleExpand(id: string): void {
    const next = new Set(this.expanded());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.expanded.set(next);
  }

  truncate(s: string, n: number): string { return s.length <= n ? s : s.slice(0, n).trimEnd() + '…'; }
  formatCost(s: string): string { const n = Number(s); return Number.isFinite(n) ? n.toFixed(4) : s; }
  num(v: unknown): string { if (v == null) return '—'; const n = Number(v); return Number.isFinite(n) ? n.toFixed(2) : String(v); }
  pct(v: unknown): string { if (v == null) return '—'; const n = Number(v); return Number.isFinite(n) ? (n * 100).toFixed(2) + '%' : String(v); }
  asArray(v: unknown): string[] { return Array.isArray(v) ? (v as string[]) : []; }
  displayName(id: string): string { return ALL_PERSONAS.find((p) => p.id === id)?.name ?? id; }
  protected readonly _personaIds = PERSONA_IDS;

  // P3: Add-to-portfolio.
  entryOpen = signal(false);
  entryPrefill = signal<{ runId?: number; decisionId?: number; ticker?: string; side?: PositionSide } | null>(null);
  toastMsg = signal<string | null>(null);
  private toastHandle: ReturnType<typeof setTimeout> | null = null;

  // Submit-as-broker-order flow: ticket modal → gated confirm modal → broker.
  brokerTicket = signal<{ decision: BrokerOrderTicketDecision; accounts: BrokerAccount[] } | null>(null);
  confirmingOrder = signal<BrokerOrderRow | null>(null);
  confirmingAccount = signal<BrokerAccount | null>(null);

  canAddToPortfolio(d: { side?: string; action: string }): boolean {
    if (this.run()?.status !== 'done') return false;
    if (d.side === 'pair') return false;
    return true;
  }

  addToPortfolio(d: { id: number; ticker: string; side?: string; action: string }): void {
    const run = this.run();
    if (!run) return;
    const inferred: PositionSide =
      d.side === 'short' ? 'short' :
      d.action === 'open_short' ? 'short' : 'long';
    this.entryPrefill.set({
      runId: run.id,
      decisionId: d.id,
      ticker: d.ticker,
      side: inferred,
    });
    this.entryOpen.set(true);
  }

  // P3a-1: send a Decision to the broker. Opens the ticket modal (pick
  // account + quantity), which creates a draft and hands off to the gated
  // confirm modal that actually transmits to the broker.
  private readonly brokerStore = inject(BrokerStore);

  submitAsBrokerOrder(d: DecisionRow): void {
    const run = this.run();
    if (!run) return;
    const side: 'buy' | 'sell' =
      d.action === 'sell' || d.side === 'short' ? 'sell' : 'buy';
    const open = (list: BrokerAccount[]) => {
      const active = list.filter(
        (a) => a.is_active && a.connection_status === 'active',
      );
      if (active.length === 0) {
        this.flashToast(
          'No active broker accounts — connect one in Broker accounts first.',
        );
        return;
      }
      this.brokerTicket.set({
        decision: { id: d.id, ticker: d.ticker, side, targetQuantity: d.target_quantity },
        accounts: active,
      });
    };
    const accounts = this.brokerStore.accounts();
    if (accounts.length === 0) {
      this.brokerStore.loadAccounts().subscribe({
        next: (list) => open(list as unknown as BrokerAccount[]),
        error: () => open([]),
      });
    } else {
      open(accounts);
    }
  }

  // Draft created in the ticket modal → open the gated confirm modal.
  onTicketReview(e: { order: BrokerOrderRow; account: BrokerAccount }): void {
    this.brokerTicket.set(null);
    this.confirmingAccount.set(e.account);
    this.confirmingOrder.set(e.order);
  }

  onBrokerConfirmClosed(): void {
    this.confirmingOrder.set(null);
    this.confirmingAccount.set(null);
  }

  onBrokerConfirmed(row: BrokerOrderRow): void {
    const label = this.confirmingAccount()?.label ?? 'broker';
    this.onBrokerConfirmClosed();
    this.flashToast(`Submitted ${row.ticker} ${row.side} → ${label}.`);
  }

  private flashToast(msg: string): void {
    this.toastMsg.set(msg);
    if (this.toastHandle) clearTimeout(this.toastHandle);
    this.toastHandle = setTimeout(() => this.toastMsg.set(null), 6000);
  }

  onEntryClosed(e: { saved: boolean }): void {
    this.entryOpen.set(false);
    const ticker = this.entryPrefill()?.ticker;
    this.entryPrefill.set(null);
    if (e.saved) {
      this.toastMsg.set(`${ticker ?? 'Position'} saved to portfolio.`);
      if (this.toastHandle) clearTimeout(this.toastHandle);
      this.toastHandle = setTimeout(() => this.toastMsg.set(null), 6000);
    }
  }
}
