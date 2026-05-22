import { Component, OnDestroy, OnInit, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { RunsStore } from '../../abstraction/runs.store';
import { AgentMessage, ALL_PERSONAS, PERSONA_IDS } from '../../core/models/run.model';
import { ConfidenceMeterComponent } from '../shared/confidence-meter.component';
import { EnterPositionModalComponent } from '../portfolio/enter-position.modal';
import { PositionSide } from '../../core/models/portfolio.model';
import { TickerComponent } from '../shared/ticker.component';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';

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
  imports: [CommonModule, RouterLink, AppShellComponent, ConfidenceMeterComponent, EnterPositionModalComponent, TickerComponent],
  template: `
    <hf-app-shell [crumbs]="crumbs()">
      <div class="page-head">
        <div>
          <div class="eyebrow">Run · {{ run()?.status || '…' }}</div>
          <h1 style="margin-top:6px">
            Run #{{ run()?.id }}
            <span style="color:var(--text-3);font-weight:500">—</span>
            @for (t of (run()?.tickers || []); track t; let last = $last) {
              <hf-ticker [ticker]="t"></hf-ticker>@if (!last) {<span style="color:var(--text-3)">, </span>}
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
        <p style="color:var(--text-3)">Loading…</p>
      } @else {
        @if (run()!.source === 'strategy' && run()!.strategy_backlink; as link) {
          <div class="card" style="margin-bottom:14px;background:var(--surface-2)" data-test="strategy-backlink">
            <div class="card-bd" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px">
              <span class="pill" style="background:var(--surface-2);color:var(--text-3);height:auto;padding:2px 8px">
                <span class="dot"></span>Strategy cycle
              </span>
              <span style="color:var(--text-2)">
                Triggered by
                <a [routerLink]="['/strategies', link.strategy_id]" style="color:var(--acc-info-fg);text-decoration:none;font-weight:600">
                  {{ link.strategy_name }}
                </a>
                · cycle <span class="mono">{{ link.as_of_date }}</span>
                ·
                <a [routerLink]="['/strategies', link.strategy_id]"
                   [queryParams]="{ cycle: link.portfolio_target_id }"
                   style="color:var(--acc-info-fg);text-decoration:none">
                  open cycle →
                </a>
              </span>
            </div>
          </div>
        }
        <!-- Status strip -->
        <section class="card" style="margin-bottom:14px">
          <div class="card-bd" style="display:grid;grid-template-columns:repeat(4,1fr);gap:24px">
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
              <div class="mono" style="font-size:14px;margin-top:4px">{{ run()!.as_of_date }}</div>
            </div>
            <div>
              <div class="eyebrow">Personas</div>
              <div class="mono" style="font-size:14px;margin-top:4px">{{ personas().length }}</div>
            </div>
            <div>
              <div class="eyebrow">Total cost</div>
              <div class="mono" style="font-size:14px;margin-top:4px">$ {{ formatCost(run()!.total_cost_usd) }}</div>
            </div>
          </div>
        </section>

        @if (run()!.error_message) {
          <section class="card" style="margin-bottom:14px;border-color:var(--acc-short-soft)">
            <div class="card-bd">
              <p class="mono" style="color:var(--acc-short-fg);font-size:12px;margin:0;white-space:pre-wrap">{{ run()!.error_message }}</p>
            </div>
          </section>
        }

        <!-- Final order ticket(s) -->
        @for (d of run()!.decisions; track d.id) {
          <section class="card" style="margin-bottom:14px;border-left:3px solid;"
            [style.borderLeftColor]="d.action === 'buy' ? 'var(--acc-long)' : d.action === 'sell' ? 'var(--acc-short)' : 'var(--acc-hold)'">
            <div class="card-bd">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px">
                <div>
                  <h2 style="font-size:18px;font-weight:600;margin:0;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                    <span>Final order ticket: {{ d.action.toUpperCase() }}</span>
                    <hf-ticker [ticker]="d.ticker"></hf-ticker>
                  </h2>
                  <div style="font-size:12px;color:var(--text-3);margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                    @if (d.risk_overrides.veto) {
                      <span class="pill err"><span class="dot"></span>RISK VETO</span>
                    }
                    @if (cioOutput()?.['overrode_pm']) {
                      <span class="pill warn"><span class="dot"></span>CIO OVERRIDE</span>
                    }
                  </div>
                  <div style="margin-top:10px;max-width:280px">
                    <hf-confidence-meter
                      [value]="d.confidence"
                      [tone]="d.action === 'buy' ? 'buy' : d.action === 'sell' ? 'sell' : 'hold'"
                      [counts]="stanceCounts()" />
                  </div>
                </div>
                <div style="text-align:right;font-size:13px">
                  <p style="margin:0">
                    Target qty: <span class="mono" style="color:var(--text)">{{ d.target_quantity }}</span>
                    <span style="margin-left:4px;color:var(--text-3);cursor:help"
                      title="Illustrative — computed against a $100K stub portfolio. Replaced by your real broker account balance in P3a (paper trading).">ⓘ</span>
                  </p>
                  <p style="margin:2px 0 0">
                    Target weight: <span class="mono" style="color:var(--text)">{{ d.target_weight_pct }}%</span>
                  </p>
                  @if (canAddToPortfolio(d)) {
                    <button class="btn primary sm" style="margin-top:10px"
                            (click)="addToPortfolio(d)"
                            [attr.data-test]="'add-to-portfolio-' + d.ticker">
                      <svg width="12" height="12" style="margin-right:4px">
                        <use href="/icons.svg#i-plus" />
                      </svg>
                      Add to portfolio
                    </button>
                  } @else if (d.side === 'pair') {
                    <p style="margin:8px 0 0;font-size:11px;color:var(--text-3)">
                      Pair decisions can't be entered manually (v1).
                    </p>
                  } @else if (run()!.status !== 'done') {
                    <p style="margin:8px 0 0;font-size:11px;color:var(--text-3)">
                      Wait for the run to complete to enter a position.
                    </p>
                  }
                </div>
              </div>
              <p style="font-size:13px;line-height:20px;color:var(--text-2);margin:12px 0 0;white-space:pre-wrap">{{ d.rationale }}</p>
            </div>
          </section>
        }

        <hf-enter-position-modal
          [open]="entryOpen()"
          [prefill]="entryPrefill()"
          (closed)="onEntryClosed($event)" />

        <div role="status" aria-live="polite" class="visually-hidden">
          @if (toastMsg(); as t) { {{ t }} }
        </div>
        @if (toastMsg(); as t) {
          <div class="pill ok" style="position:fixed;bottom:18px;right:18px;height:auto;padding:8px 12px;z-index:var(--z-modal)"
               data-test="position-saved-toast">
            <span class="dot"></span>{{ t }}
            <a routerLink="/portfolio"
               style="margin-left:6px;color:var(--acc-info-fg);text-decoration:underline">View portfolio →</a>
          </div>
        }

        <!-- CIO -->
        @if (cioOutput(); as c) {
          <section class="card" style="margin-bottom:14px;border-left:3px solid;"
            [style.borderLeftColor]="c['overrode_pm'] ? 'var(--acc-hold)' : 'var(--acc-info)'">
            <div class="card-bd">
              <div style="display:flex;justify-content:space-between;align-items:flex-start">
                <h2 style="font-size:16px;font-weight:600;margin:0;display:flex;align-items:center;gap:8px">
                  Chief Investment Officer
                  @if (c['overrode_pm']) {
                    <span class="pill warn"><span class="dot"></span>OVERRIDE</span>
                  } @else {
                    <span class="pill info"><span class="dot"></span>RATIFIED PM</span>
                  }
                </h2>
                <span class="mono" style="font-size:11px;color:var(--text-3)">confidence {{ c['confidence'] }}</span>
              </div>
              <p style="font-size:13px;color:var(--text-2);margin:10px 0 0">{{ c['outlook'] }}</p>
              @if (c['overrode_pm'] && c['override_reason']) {
                <p style="font-size:13px;margin:8px 0 0">
                  <span style="font-weight:600">Override reason:</span> {{ c['override_reason'] }}
                </p>
              }
              @if (c['stop_loss_pct']) {
                <p style="font-size:11px;color:var(--text-3);margin:8px 0 0">
                  Stop loss: <span class="mono">{{ pct(c['stop_loss_pct']) }}</span>
                </p>
              }
              @if (pmDecisionMsg(); as pm) {
                <details style="margin-top:12px;font-size:11px;color:var(--text-3)">
                  <summary style="cursor:pointer">Original PM ticket (pre-CIO)</summary>
                  <pre style="margin:6px 0 0;background:var(--surface-2);padding:8px;border-radius:4px;overflow:auto;font-family:var(--font-mono);font-size:11.5px;color:var(--text-2)">action: {{ pm['action'] }}, weight: {{ pm['target_weight_pct'] }}%, qty: {{ pm['target_quantity'] }}
{{ pm['rationale'] }}</pre>
                </details>
              }
            </div>
          </section>
        }

        <!-- Risk Manager -->
        @if (riskOutput(); as risk) {
          <section class="card" style="margin-bottom:14px">
            <div class="card-hd">
              <span class="title">Risk Manager</span>
              @if (risk['veto']) {
                <span class="pill err"><span class="dot"></span>VETO</span>
              }
            </div>
            <div class="card-bd">
              <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;font-size:13px">
                <div>
                  <div class="eyebrow">Max position</div>
                  <div class="mono" style="margin-top:4px">{{ pct(risk['max_position_pct_for_this_trade']) }}</div>
                </div>
                <div>
                  <div class="eyebrow">Stop loss</div>
                  <div class="mono" style="margin-top:4px">{{ pct(risk['stop_loss_pct']) }}</div>
                </div>
                <div>
                  <div class="eyebrow">Hard caps applied</div>
                  <div class="mono" style="margin-top:4px;font-size:11.5px">
                    {{ (asArray(risk['hard_caps_applied'])).join(', ') || 'none' }}
                  </div>
                </div>
              </div>
              <p style="font-size:13px;color:var(--text-2);margin:14px 0 0;white-space:pre-wrap">{{ risk['rationale'] }}</p>
            </div>
          </section>
        }

        <!-- Valuation -->
        @if (valuationOutput(); as v) {
          <section class="card" style="margin-bottom:14px">
            <div class="card-hd"><span class="title">Valuation</span></div>
            <div class="card-bd">
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;font-size:13px">
                <div><div class="eyebrow">DCF</div><div class="mono" style="margin-top:4px">{{ num(v['dcf_fair_value']) }}</div></div>
                <div><div class="eyebrow">Multiples</div><div class="mono" style="margin-top:4px">{{ num(v['multiples_fair_value']) }}</div></div>
                <div><div class="eyebrow">Residual income</div><div class="mono" style="margin-top:4px">{{ num(v['residual_income_fair_value']) }}</div></div>
                <div><div class="eyebrow">Current price</div><div class="mono" style="margin-top:4px">{{ num(v['current_price']) }}</div></div>
                <div><div class="eyebrow">FV low</div><div class="mono" style="margin-top:4px">{{ num(v['fair_value_low']) }}</div></div>
                <div><div class="eyebrow">FV high</div><div class="mono" style="margin-top:4px">{{ num(v['fair_value_high']) }}</div></div>
                <div style="grid-column:span 2"><div class="eyebrow">Upside</div><div class="mono" style="margin-top:4px">{{ num(v['upside_pct']) }}%</div></div>
              </div>
              <p style="font-size:11.5px;color:var(--text-3);margin:10px 0 0">
                Most sensitive: {{ v['most_sensitive_assumption'] }}
              </p>
            </div>
          </section>
        }

        <!-- Macro -->
        @if (macroOutput(); as m) {
          <section class="card" style="margin-bottom:14px">
            <div class="card-hd"><span class="title">Macro context</span></div>
            <div class="card-bd">
              <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px">
                <span class="pill"><span class="dot"></span>growth · {{ m['growth_quadrant'] }}</span>
                <span class="pill"><span class="dot"></span>inflation · {{ m['inflation_regime'] }}</span>
                <span class="pill"><span class="dot"></span>curve · {{ m['yield_curve_state'] }}</span>
                <span class="pill"><span class="dot"></span>policy · {{ m['policy_stance'] }}</span>
              </div>
              <p style="font-size:13px;color:var(--text-2);margin:0">{{ m['narrative'] }}</p>
            </div>
          </section>
        }

        <!-- News & filings -->
        @if (newsOutput(); as n) {
          <section class="card" style="margin-bottom:14px">
            <div class="card-hd"><span class="title">News &amp; filings</span></div>
            <div class="card-bd">
              <p style="font-size:13px;color:var(--text-2);margin:0 0 12px">{{ n['digest'] }}</p>
              @if (asArray(n['risk_factor_highlights']).length) {
                <div class="eyebrow" style="margin-bottom:6px">Risk factor highlights</div>
                <ul style="margin:0;padding-left:18px;font-size:13px;color:var(--text-2)">
                  @for (r of asArray(n['risk_factor_highlights']); track r) { <li>{{ r }}</li> }
                </ul>
              }
              @if (asAnyArray(n['material_events']).length) {
                <div class="eyebrow" style="margin:12px 0 6px">Material events</div>
                <ul style="margin:0;padding:0;list-style:none;font-size:13px">
                  @for (e of asAnyArray(n['material_events']); track e['url']) {
                    <li style="border-top:1px solid var(--border);padding:6px 0;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                      <span class="mono" style="font-size:11.5px;color:var(--text-3)">{{ e['date'] }}</span>
                      <span class="pill"><span class="dot"></span>{{ e['tag'] }}</span>
                      <span class="mono" style="font-size:11px;color:var(--text-3)">m={{ e['materiality'] }}</span>
                      <a [href]="e['url']" target="_blank" style="color:var(--acc-info-fg)">{{ e['headline'] }}</a>
                    </li>
                  }
                </ul>
              }
              <p style="font-size:11.5px;color:var(--text-3);margin:10px 0 0">
                Sentiment: <span class="mono">{{ num(n['sentiment_score']) }}</span>
                @if (asArray(n['sentiment_drivers']).length) {
                  · drivers: {{ asArray(n['sentiment_drivers']).join('; ') }}
                }
              </p>
            </div>
          </section>
        }

        <!-- Council -->
        <section style="margin-bottom:14px">
          <div class="eyebrow" style="margin-bottom:10px">Council</div>
          <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px">
            @for (p of personas(); track p.id) {
              <article class="card" style="border-top:3px solid"
                [style.borderTopColor]="p.signal === 'bullish' ? 'var(--acc-long)' : p.signal === 'bearish' ? 'var(--acc-short)' : p.signal === 'neutral' ? 'var(--acc-hold)' : 'var(--border-2)'">
                <div class="card-bd">
                  <div style="display:flex;justify-content:space-between;align-items:baseline">
                    <h3 style="font-size:14px;font-weight:600;margin:0">{{ p.displayName }}</h3>
                    <span class="mono" style="font-size:11px;color:var(--text-3)">{{ p.id }}&#64;{{ p.version }}</span>
                  </div>
                  <p style="font-size:13px;margin:6px 0 0">
                    <span style="font-weight:500;text-transform:uppercase">{{ p.signal }}</span>
                    <span style="color:var(--text-3)"> · {{ p.confidence }}% confidence</span>
                  </p>
                  <p style="font-size:13px;color:var(--text-2);margin:8px 0 0;line-height:18px">
                    {{ expanded().has(p.id) ? p.thesis : truncate(p.thesis, 200) }}
                  </p>
                  @if (p.thesis.length > 200) {
                    <button type="button" style="font-size:11.5px;color:var(--acc-info-fg);margin-top:4px;background:transparent;border:0;cursor:pointer;padding:0"
                      (click)="toggleExpand(p.id)">
                      {{ expanded().has(p.id) ? 'Collapse' : 'See full reasoning' }}
                    </button>
                  }
                  @if (p.keyRisks.length) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:8px 0 0">
                      Risks: {{ p.keyRisks.join('; ') }}
                    </p>
                  }
                  @if (p.intrinsicValue !== null) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:4px 0 0">
                      IV $ {{ p.intrinsicValue }}, MoS {{ p.marginOfSafety }}%
                    </p>
                  }
                </div>
              </article>
            }
          </div>
        </section>

        <!-- Dissent -->
        @if (dissent().length) {
          <section class="card" style="margin-bottom:14px;background:var(--acc-hold-soft);border-color:var(--acc-hold-soft)">
            <div class="card-bd">
              <h2 style="font-size:14px;font-weight:600;margin:0 0 8px;color:var(--acc-hold-fg)">Dissenting views</h2>
              <ul style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px;font-size:13px">
                @for (d of dissent(); track d.name) {
                  <li>
                    <span style="font-weight:500">{{ displayName(d.name) }}</span>
                    ({{ d.signal }}, {{ d.confidence }}%):
                    <span style="color:var(--text-2)">{{ d.thesis_summary }}</span>
                  </li>
                }
              </ul>
            </div>
          </section>
        }

        <!-- Risk context (P02a review) -->
        @if (riskContext(); as rc) {
          <section class="card" style="margin-bottom:14px" data-test="risk-context">
            <div class="card-hd">
              <span class="title">Risk context</span>
              <span class="pill"
                [class.warn]="rc.mode === 'stub'"
                [class.ok]="rc.mode === 'real'">
                <span class="dot"></span>{{ rc.mode || 'unknown' }}
              </span>
            </div>
            <div class="card-bd">
              <p style="font-size:13px;color:var(--text-2);margin:0">{{ rc.notes }}</p>
              @if (rc.mode === 'stub' && rc.stub_nav_usd) {
                <p style="font-size:11.5px;color:var(--text-3);margin:6px 0 0">
                  Stub NAV: <span class="mono">$ {{ rc.stub_nav_usd }}</span>
                </p>
              }
              @if (rc.mode === 'real' && rc.cash_balance_usd) {
                <p style="font-size:11.5px;color:var(--text-3);margin:6px 0 0">
                  Portfolio cash: <span class="mono">$ {{ rc.cash_balance_usd }}</span>
                </p>
              }
            </div>
          </section>
        }

        <!-- Evidence trail (P01 review) -->
        @if (evidenceItems().length || providerStateEntries().length) {
          <section class="card" style="margin-bottom:14px" data-test="evidence-trail">
            <div class="card-hd"><span class="title">Evidence &amp; sources</span></div>
            <div class="card-bd">
              @if (providerStateEntries().length) {
                <div class="eyebrow" style="margin-bottom:6px">Provider status</div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">
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
                <div class="eyebrow" style="margin-bottom:6px">Citations</div>
                <ul style="margin:0;padding:0;list-style:none;font-size:13px">
                  @for (it of evidenceItems(); track it.label + it.source + it.url) {
                    <li style="border-top:1px solid var(--border);padding:6px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center">
                      <span class="pill"><span class="dot"></span>{{ it.agent }}</span>
                      <span style="color:var(--text-2)">{{ it.label }}</span>
                      <span class="mono" style="font-size:11px;color:var(--text-3)">
                        {{ it.provider }}{{ it.as_of ? ' · ' + it.as_of : '' }}
                      </span>
                      @if (it.url) {
                        <a [href]="it.url" target="_blank" rel="noopener" style="color:var(--acc-info-fg);font-size:11.5px">open ↗</a>
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
                    <td style="color:var(--text-2)">{{ c.provider }}</td>
                    <td class="mono" style="font-size:11.5px">{{ c.model }}</td>
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
      }
    </hf-app-shell>
  `,
})
export class RunsDetailPage implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  readonly store = inject(RunsStore);
  private readonly profiles = inject(TickerProfileStore);
  readonly run = this.store.currentRun;
  readonly expanded = signal<Set<string>>(new Set());
  readonly cancelling = signal(false);
  private prefetchedTickers = new Set<string>();

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
  }

  ngOnDestroy(): void { this.store.stopPolling(); }

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
