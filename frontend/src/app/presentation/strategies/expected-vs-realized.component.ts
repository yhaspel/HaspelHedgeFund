import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { StrategiesStore } from '../../abstraction/strategies.store';
import { EvrCycle } from '../../core/models/expected-vs-realized.model';
import { ErrorStateComponent } from '../shared/error-state.component';

/**
 * WAVE 3 item 2 — "the backtest said X ± Y; what actually happened?".
 *
 * Each realized holding interval is matched to the walk-forward fold covering
 * its `as_of_date` and placed inside that fold's own out-of-sample
 * distribution. Two honesty rules drive the whole render:
 *
 *  1. While `provisional === true` every ratio the backend returns is `null`
 *     (`inside_ratio`, `outside_ratio`, `mean_expected_pct`, `mean_gap_pp`) —
 *     they render as "—" and `provisional_reasons` is printed plainly. A
 *     fabricated "83% inside" from four cycles is worse than no number.
 *  2. `fold.covers === false` means the cycle sits OUTSIDE every out-of-sample
 *     window and was matched to the nearest fold instead. That row is labelled
 *     "nearest fold, does not cover" and is indicative only.
 */
@Component({
  selector: 'hf-expected-vs-realized',
  standalone: true,
  imports: [CommonModule, RouterLink, ErrorStateComponent],
  template: `
    <section class="card" data-test="evr">
      <div class="card-hd">
        <h2 class="title">Expected vs realized</h2>
        <div class="actions">
          @if (data(); as d) {
            @if (d.provisional) {
              <span class="pill warn" data-test="evr-provisional">
                <span class="dot"></span>provisional
              </span>
            } @else {
              <span class="pill ok" data-test="evr-validated">
                <span class="dot"></span>validated
              </span>
            }
          }
          <button type="button" class="btn sm" data-test="evr-reload" (click)="reload()">
            Refresh
          </button>
        </div>
      </div>

      <div class="card-bd">
        @if (store.evrError(); as e) {
          <hf-error-state
            [compact]="true"
            title="Couldn’t load expected vs realized"
            [detail]="e"
            (retry)="reload()"
          ></hf-error-state>
        } @else if (store.evrLoading() && !data()) {
          <p class="muted">Comparing cycles against the validation folds…</p>
        } @else if (data(); as d) {
          <p class="lede">
            Every completed cycle's realized return, next to what the linked walk-forward
            backtest's out-of-sample fold said to expect over the same number of sessions.
            Measured to <span class="mono">{{ d.end_date }}</span>.
          </p>

          @if (d.backtest; as bt) {
            <div class="bt" data-test="evr-backtest">
              <a class="bt-name" [routerLink]="['/backtests', bt.id]">#{{ bt.id }} {{ bt.name }}</a>
              <span class="chip">{{ bt.folds }} fold(s)</span>
              <span class="chip">engine v{{ bt.engine_version }} · {{ bt.engine_mode }}</span>
              <span class="chip">{{ bt.data_era }}</span>
              @if (bt.oos_start && bt.oos_end) {
                <span class="chip mono">OOS {{ bt.oos_start }} → {{ bt.oos_end }}</span>
              }
              <span
                class="chip"
                [class.good]="bt.gate_passed"
                [class.bad]="!bt.gate_passed"
                data-test="evr-gate"
                >§9 gate {{ bt.gate_passed ? 'passed' : 'not passed' }}</span
              >
            </div>
            @if (bt.gate_reasons.length) {
              <ul class="reasons" data-test="evr-gate-reasons">
                @for (r of bt.gate_reasons; track r) {
                  <li>{{ r }}</li>
                }
              </ul>
            }
            @if (bt.gate_warnings.length) {
              <ul class="reasons warn" data-test="evr-gate-warnings">
                @for (w of bt.gate_warnings; track w) {
                  <li>{{ w }}</li>
                }
              </ul>
            }
          } @else {
            <p class="muted" data-test="evr-no-backtest">
              No validation backtest is linked to this strategy, so there is nothing to compare
              against — the table below shows realized returns only.
            </p>
          }

          @if (d.provisional && d.provisional_reasons.length) {
            <div class="prov" role="status" data-test="evr-reasons">
              <div class="prov-hd">
                Provisional — the ratios below are withheld, not zero. Why:
              </div>
              <ul>
                @for (r of d.provisional_reasons; track r) {
                  <li>{{ r }}</li>
                }
              </ul>
            </div>
          }

          <!-- Summary. Ratios are null while provisional and render as "—". -->
          <dl class="summary" data-test="evr-summary">
            <div><dt>Cycles</dt><dd class="mono">{{ d.summary.cycles }}</dd></div>
            <div><dt>Scored</dt><dd class="mono">{{ d.summary.scored }}</dd></div>
            <div><dt>Unscored</dt><dd class="mono">{{ d.summary.unscored }}</dd></div>
            <div>
              <dt>Inside ±{{ d.summary.z_outside_threshold }}σ</dt>
              <dd class="mono" data-test="evr-inside-ratio">
                {{ ratio(d.summary.inside_ratio) }}
                <span class="sub">{{ d.summary.inside }} / {{ d.summary.z_scored }}</span>
              </dd>
            </div>
            <div>
              <dt>Outside</dt>
              <dd class="mono" data-test="evr-outside-ratio">
                {{ ratio(d.summary.outside_ratio) }}
                <span class="sub">{{ d.summary.outside }} / {{ d.summary.z_scored }}</span>
              </dd>
            </div>
            <div>
              <dt>Mean realized</dt>
              <dd class="mono" data-test="evr-mean-realized">
                {{ pp(d.summary.mean_realized_pct) }}
              </dd>
            </div>
            <div>
              <dt>Mean expected</dt>
              <dd class="mono" data-test="evr-mean-expected">
                {{ pp(d.summary.mean_expected_pct) }}
              </dd>
            </div>
            <div>
              <dt>Mean gap</dt>
              <dd class="mono" data-test="evr-mean-gap">{{ pp(d.summary.mean_gap_pp, 'pp') }}</dd>
            </div>
          </dl>
          @if (d.provisional) {
            <p class="muted small" data-test="evr-ratio-note">
              Ratios stay blank until at least {{ d.summary.min_cycles_for_ratios }} cycles are
              scored against a gate-passing backtest.
            </p>
          }

          @if (!d.cycles.length) {
            <p class="muted" data-test="evr-empty">
              No completed cycle has been priced yet — nothing to compare.
            </p>
          } @else {
            <div class="tbl-wrap">
              <table class="tbl" data-test="evr-table">
                <thead>
                  <tr>
                    <th>Cycle</th>
                    <th class="right">Realized</th>
                    <th class="right">Expected ± sd</th>
                    <th class="right">z</th>
                    <th class="right">Percentile</th>
                    <th>Band</th>
                    <th>Fold</th>
                  </tr>
                </thead>
                <tbody>
                  @for (c of d.cycles; track c.as_of_date + '-' + (c.target_id ?? 0)) {
                    <tr [attr.data-test]="'evr-row-' + c.as_of_date">
                      <td>
                        <div class="mono">{{ c.as_of_date }}</div>
                        <div class="sub">
                          {{ c.period_days }}d · ~{{ c.sessions_assumed }} sessions
                        </div>
                      </td>
                      <td class="num">{{ pp(c.realized_return_pct) }}</td>
                      <td class="num" [attr.data-test]="'evr-expected-' + c.as_of_date">
                        @if (c.expected_return_pct === null) {
                          <span class="dash">—</span>
                        } @else {
                          {{ pp(c.expected_return_pct) }}
                          @if (c.expected_sd_pp !== null) {
                            <span class="sub">± {{ c.expected_sd_pp | number: '1.2-2' }}pp</span>
                          }
                          <span class="sub">{{ sourceLabel(c.expected_source) }}</span>
                        }
                      </td>
                      <td class="num">
                        @if (c.z_score === null) {
                          <span class="dash">—</span>
                        } @else {
                          {{ c.z_score | number: '1.2-2' }}
                        }
                      </td>
                      <td class="num">
                        @if (c.percentile === null) {
                          <span class="dash">—</span>
                        } @else {
                          {{ c.percentile | number: '1.0-0' }}th
                          <span class="sub">{{ c.percentile_method }}</span>
                        }
                      </td>
                      <td>
                        @if (c.z_score === null) {
                          <span class="pill" [attr.data-test]="'evr-band-' + c.as_of_date">
                            <span class="dot"></span>unscored
                          </span>
                        } @else if (c.outside_distribution) {
                          <span class="pill err" [attr.data-test]="'evr-band-' + c.as_of_date">
                            <span class="dot"></span>outside
                          </span>
                        } @else {
                          <span class="pill ok" [attr.data-test]="'evr-band-' + c.as_of_date">
                            <span class="dot"></span>inside
                          </span>
                        }
                      </td>
                      <td>
                        @if (c.fold; as f) {
                          <a
                            class="fold-link"
                            [routerLink]="['/backtests', d.backtest?.id]"
                            [attr.data-test]="'evr-fold-' + c.as_of_date"
                            >fold #{{ f.index }}</a
                          >
                          <div class="sub mono">{{ f.oos_start }} → {{ f.oos_end }}</div>
                          @if (!f.covers) {
                            <div
                              class="not-covered"
                              [attr.data-test]="'evr-nearest-' + c.as_of_date"
                            >
                              ⚠ nearest fold, does not cover — {{ f.gap_days }}d away. Indicative
                              only.
                            </div>
                          }
                        } @else {
                          <span class="dash">—</span>
                        }
                      </td>
                    </tr>
                    <tr class="note-row">
                      <td colspan="7" [attr.data-test]="'evr-note-' + c.as_of_date">
                        {{ c.note }}
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        } @else {
          <p class="muted">No expected-vs-realized data for this strategy.</p>
        }
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .card-bd {
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .lede,
      .muted {
        margin: 0;
        font-size: var(--fs-12);
        color: var(--text-3);
      }
      .muted.small {
        font-size: var(--fs-11);
      }
      .mono {
        font-family: var(--font-mono);
        letter-spacing: var(--tracking-mono);
      }
      .bt {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
      }
      .bt-name {
        font-size: var(--fs-12);
        color: var(--acc-info-fg);
        text-decoration: underline;
      }
      .chip {
        font-size: var(--fs-11);
        padding: 1px 6px;
        border: 1px solid var(--border-2);
        border-radius: var(--r-full);
        color: var(--text-3);
      }
      .chip.good {
        border-color: var(--acc-long);
        background: var(--acc-long-soft);
        color: var(--acc-long-fg);
      }
      .chip.bad {
        border-color: var(--acc-hold);
        background: var(--acc-hold-soft);
        color: var(--acc-hold-fg);
      }
      .reasons {
        margin: 0;
        padding-left: 18px;
        font-size: var(--fs-11);
        color: var(--text-2);
      }
      .reasons.warn {
        color: var(--acc-hold-fg);
      }
      .prov {
        border: 1px solid var(--acc-hold);
        background: var(--acc-hold-soft);
        border-radius: var(--r-6);
        padding: 8px 12px;
      }
      .prov-hd {
        font-size: var(--fs-12);
        font-weight: 600;
        color: var(--acc-hold-fg);
        margin-bottom: 4px;
      }
      .prov ul {
        margin: 0;
        padding-left: 18px;
        font-size: var(--fs-11);
        color: var(--text-2);
      }
      .summary {
        margin: 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
        gap: 8px 14px;
      }
      .summary dt {
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-3);
      }
      .summary dd {
        margin: 0;
        font-size: var(--fs-14);
        color: var(--text);
      }
      .sub {
        display: inline-block;
        margin-left: 4px;
        font-size: var(--fs-11);
        color: var(--text-3);
        font-family: var(--font-sans);
        letter-spacing: normal;
      }
      .dash {
        color: var(--text-3);
      }
      .tbl-wrap {
        overflow-x: auto;
      }
      .not-covered {
        font-size: var(--fs-11);
        color: var(--acc-hold-fg);
        max-width: 30ch;
      }
      .fold-link {
        font-size: var(--fs-12);
        color: var(--acc-info-fg);
        text-decoration: underline;
      }
      .note-row td {
        font-size: var(--fs-11);
        color: var(--text-3);
        padding-top: 0;
        border-top: 0;
      }
    `,
  ],
})
export class ExpectedVsRealizedComponent implements OnChanges {
  readonly store = inject(StrategiesStore);

  @Input({ required: true }) strategyId!: number;

  readonly data = computed(() => {
    const d = this.store.expectedVsRealized();
    return d && d.strategy_id === this.strategyId ? d : null;
  });

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['strategyId'] && this.strategyId) this.reload();
  }

  reload(): void {
    if (!this.strategyId) return;
    this.store.loadExpectedVsRealized(this.strategyId).subscribe();
  }

  /** A withheld ratio is "—", never 0 and never a made-up number. */
  ratio(v: number | null): string {
    return v === null || v === undefined ? '—' : `${(v * 100).toFixed(0)}%`;
  }

  pp(v: number | null | undefined, unit = '%'): string {
    if (v === null || v === undefined) return '—';
    const sign = v > 0 ? '+' : '';
    return `${sign}${v.toFixed(2)}${unit}`;
  }

  sourceLabel(src: EvrCycle['expected_source']): string {
    if (src === 'fold_daily') return 'from the fold’s daily series';
    if (src === 'fold_total') return 'pro-rated from the fold total (no distribution)';
    return '';
  }
}
