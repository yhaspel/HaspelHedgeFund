import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MacroSnapshot } from '../../abstraction/macro.store';
import { MarkovConsensus } from '../../core/models/regime.model';
import { PopoverComponent } from '../shared/popover.component';

type PillKind = 'ok' | 'warn' | 'err' | 'info' | '';

/**
 * Compact macro-regime strip: investment-clock quadrant (growth phase) +
 * the four regime dimension chips (with the existing hover/focus popovers,
 * moved here verbatim) + the Markov consensus bar. All values are real
 * MacroSnapshot fields; no history "trail" is drawn (no macro history API).
 */
@Component({
  selector: 'hf-regime-strip',
  standalone: true,
  imports: [CommonModule, PopoverComponent],
  template: `
    <section class="card regime-strip">
      @if (snapshot; as s) {
        <!-- cell 1: investment-clock quadrant (growth phase) -->
        <div class="rs quad-cell">
          <div
            class="quad"
            role="img"
            [attr.aria-label]="
              'Investment clock — growth ' + s.growth_quadrant + ', inflation ' + s.inflation_regime
            "
          >
            <span class="qlbl tl" [class.active]="s.growth_quadrant === 'slowdown'">Slowdown</span>
            <span class="qlbl tr" [class.active]="s.growth_quadrant === 'expansion'">Expansion</span>
            <span class="qlbl bl" [class.active]="s.growth_quadrant === 'recession'">Recession</span>
            <span class="qlbl br" [class.active]="s.growth_quadrant === 'recovery'">Recovery</span>
            <span class="axis-x" aria-hidden="true">growth →</span>
            <span class="now" [style.left.%]="dotLeft(s)" [style.top.%]="dotTop(s)"></span>
          </div>
        </div>

        <!-- cell 2: state + dimension chips + narrative -->
        <div class="rs">
          <div class="rs-head">
            <span class="title">Macro regime</span>
            <span class="big" [class]="'k-' + chipKind('growth', s.growth_quadrant)">
              <span class="dot"></span>{{ s.growth_quadrant }}
            </span>
            <span class="as-of">as of {{ s.as_of_date }}</span>
          </div>
          <div class="chips">
            <span class="pill help"
              [class.ok]="chipKind('growth', s.growth_quadrant)==='ok'"
              [class.warn]="chipKind('growth', s.growth_quadrant)==='warn'"
              [class.err]="chipKind('growth', s.growth_quadrant)==='err'"
              tabindex="0"
              [attr.aria-describedby]="popGrowth.open() ? popGrowth.popoverId : null"
              (mouseenter)="popGrowth.show()" (mouseleave)="popGrowth.maybeHide()"
              (focus)="popGrowth.show()" (blur)="popGrowth.maybeHide()">
              <span class="dot"></span>growth · {{ s.growth_quadrant }}
              <hf-popover #popGrowth placement="top" align="start">{{ chipTooltip('growth', s.growth_quadrant) }}</hf-popover>
            </span>
            <span class="pill help"
              [class.ok]="chipKind('inflation', s.inflation_regime)==='ok'"
              [class.warn]="chipKind('inflation', s.inflation_regime)==='warn'"
              [class.err]="chipKind('inflation', s.inflation_regime)==='err'"
              tabindex="0"
              [attr.aria-describedby]="popInfl.open() ? popInfl.popoverId : null"
              (mouseenter)="popInfl.show()" (mouseleave)="popInfl.maybeHide()"
              (focus)="popInfl.show()" (blur)="popInfl.maybeHide()">
              <span class="dot"></span>inflation · {{ s.inflation_regime }}
              <hf-popover #popInfl placement="top" align="start">{{ chipTooltip('inflation', s.inflation_regime) }}</hf-popover>
            </span>
            <span class="pill help"
              [class.ok]="chipKind('curve', s.yield_curve_state)==='ok'"
              [class.warn]="chipKind('curve', s.yield_curve_state)==='warn'"
              [class.err]="chipKind('curve', s.yield_curve_state)==='err'"
              tabindex="0"
              [attr.aria-describedby]="popCurve.open() ? popCurve.popoverId : null"
              (mouseenter)="popCurve.show()" (mouseleave)="popCurve.maybeHide()"
              (focus)="popCurve.show()" (blur)="popCurve.maybeHide()">
              <span class="dot"></span>curve · {{ s.yield_curve_state }}
              <hf-popover #popCurve placement="top" align="start">{{ chipTooltip('curve', s.yield_curve_state) }}</hf-popover>
            </span>
            <span class="pill help"
              [class.ok]="chipKind('policy', s.policy_stance)==='ok'"
              [class.warn]="chipKind('policy', s.policy_stance)==='warn'"
              [class.info]="chipKind('policy', s.policy_stance)==='info'"
              tabindex="0"
              [attr.aria-describedby]="popPolicy.open() ? popPolicy.popoverId : null"
              (mouseenter)="popPolicy.show()" (mouseleave)="popPolicy.maybeHide()"
              (focus)="popPolicy.show()" (blur)="popPolicy.maybeHide()">
              <span class="dot"></span>policy · {{ s.policy_stance }}
              <hf-popover #popPolicy placement="top" align="start">{{ chipTooltip('policy', s.policy_stance) }}</hf-popover>
            </span>
          </div>
          <p class="narrative">{{ s.narrative }}</p>
        </div>

        <!-- cell 3: markov consensus -->
        <div class="rs markov-cell">
          @if (s.markov_consensus; as mc) {
            @if (mc.consensus_state !== 'unavailable' && mc.available_count > 0) {
              <div class="markov-hd">
                <span class="eyebrow">Markov consensus</span>
                <span class="conf help"
                  [class.ok]="mc.consensus_state==='bull'"
                  [class.warn]="mc.consensus_state==='sideways'"
                  [class.err]="mc.consensus_state==='bear'"
                  tabindex="0"
                  [attr.aria-describedby]="popMarkov.open() ? popMarkov.popoverId : null"
                  (mouseenter)="popMarkov.show()" (mouseleave)="popMarkov.maybeHide()"
                  (focus)="popMarkov.show()" (blur)="popMarkov.maybeHide()">
                  <span class="dot"></span>{{ mc.consensus_state }} · {{ (mc.consensus_strength * 100).toFixed(0) }}%
                  <hf-popover #popMarkov placement="top" align="end">{{ markovTooltip(mc) }}</hf-popover>
                </span>
              </div>
              <div class="markov-bar" role="img"
                   [attr.aria-label]="
                     'Markov vote: ' + (mc.vote.bull || 0) + ' bull, ' +
                     (mc.vote.sideways || 0) + ' sideways, ' + (mc.vote.bear || 0) + ' bear'
                   ">
                <span class="b bull" [style.flex]="mc.vote.bull || 0"></span>
                <span class="b side" [style.flex]="mc.vote.sideways || 0"></span>
                <span class="b bear" [style.flex]="mc.vote.bear || 0"></span>
              </div>
              <div class="markov-legend">
                <span><span class="sw bull"></span>{{ mc.vote.bull || 0 }} bull</span>
                <span><span class="sw side"></span>{{ mc.vote.sideways || 0 }} side</span>
                <span><span class="sw bear"></span>{{ mc.vote.bear || 0 }} bear</span>
              </div>
              <span class="muted num">
                {{ mc.available_count }} always-modelled ETFs@if (mc.stale_count > 0) { · {{ mc.stale_count }} stale }
              </span>
            } @else {
              <span class="eyebrow">Markov consensus</span>
              <p class="muted">
                Unavailable — no fresh regime snapshots. Snapshots refit at
                each pod cycle (weekly, with your data key).
              </p>
            }
          } @else {
            <span class="eyebrow">Markov consensus</span>
            <p class="muted">
              Unavailable — refits at the next pod cycle.
            </p>
          }
        </div>
      } @else {
        <div class="rs" aria-busy="true" aria-label="Loading macro regime">
          <div class="skel h-[120px] w-full"></div>
        </div>
        <div class="rs">
          <div class="skel h-3.5 w-[40%]"></div>
          <div class="flex gap-1.5 flex-wrap mt-2">
            @for (_ of [1,2,3,4]; track $index) { <div class="skel h-[18px] w-[88px] rounded-full"></div> }
          </div>
          <div class="skel h-3.5 w-full mt-3"></div>
        </div>
        <div class="rs"><div class="skel h-[80px] w-full"></div></div>
      }
    </section>
  `,
  styles: [
    `
      .regime-strip {
        display: grid;
        grid-template-columns: 200px 1fr 220px;
        gap: 20px;
        overflow: visible;
      }
      @media (max-width: 1180px) {
        .regime-strip { grid-template-columns: 1fr; }
      }
      .rs { min-width: 0; }
      /* quadrant */
      .quad {
        position: relative;
        aspect-ratio: 1;
        max-width: 168px;
        border: 1px solid var(--border);
        border-radius: var(--r-8);
        background:
          linear-gradient(to right, transparent calc(50% - 0.5px), var(--border) calc(50% - 0.5px), var(--border) calc(50% + 0.5px), transparent calc(50% + 0.5px)),
          linear-gradient(to bottom, transparent calc(50% - 0.5px), var(--border) calc(50% - 0.5px), var(--border) calc(50% + 0.5px), transparent calc(50% + 0.5px));
      }
      .qlbl {
        position: absolute;
        font-size: 9.5px;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        color: var(--text-3);
        padding: 4px 5px;
      }
      .qlbl.tl { top: 0; left: 0; }
      .qlbl.tr { top: 0; right: 0; }
      .qlbl.bl { bottom: 0; left: 0; }
      .qlbl.br { bottom: 0; right: 0; }
      .qlbl.active { color: var(--text); font-weight: 600; }
      .axis-x {
        position: absolute;
        bottom: -16px;
        left: 50%;
        transform: translateX(-50%);
        font-size: 9px;
        color: var(--text-3);
        font-family: var(--font-mono);
      }
      .now {
        position: absolute;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--acc-info);
        box-shadow: 0 0 0 4px var(--acc-info-soft);
        transform: translate(-50%, -50%);
        transition: left var(--dur-base) var(--ease-out-ui), top var(--dur-base) var(--ease-out-ui);
      }
      /* state head */
      .rs-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
      .rs-head .title { font-size: 13px; }
      .big {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 12px;
        text-transform: capitalize;
        color: var(--text);
      }
      .big .dot { background: var(--text-3); }
      .big.k-ok .dot { background: var(--acc-long); }
      .big.k-warn .dot { background: var(--acc-hold); }
      .big.k-err .dot { background: var(--acc-short); }
      .big.k-info .dot { background: var(--acc-info); }
      .as-of { font-size: 11px; color: var(--text-3); margin-left: auto; font-family: var(--font-mono); }
      .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; position: relative; }
      .chips .pill.help {
        cursor: help;
        text-decoration: underline dotted;
        text-decoration-color: var(--text-3);
        text-underline-offset: 3px;
        position: relative;
      }
      .narrative { font-size: 13px; color: var(--text-2); line-height: 20px; margin: 0; }
      /* markov */
      .markov-cell { display: flex; flex-direction: column; gap: 8px; }
      .markov-hd { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
      .eyebrow { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-3); }
      .conf { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; position: relative; }
      .conf.help {
        cursor: help;
        text-decoration: underline dotted;
        text-decoration-color: var(--text-3);
        text-underline-offset: 3px;
      }
      .markov-bar {
        display: flex;
        height: 10px;
        border-radius: var(--r-full);
        overflow: hidden;
        background: var(--surface-2);
      }
      .markov-bar .b.bull { background: var(--acc-long); }
      .markov-bar .b.side { background: var(--acc-hold); }
      .markov-bar .b.bear { background: var(--acc-short); }
      .markov-legend { display: flex; gap: 12px; font-size: 11px; color: var(--text-2); }
      .markov-legend .sw {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 2px;
        margin-right: 4px;
      }
      .markov-legend .sw.bull { background: var(--acc-long); }
      .markov-legend .sw.side { background: var(--acc-hold); }
      .markov-legend .sw.bear { background: var(--acc-short); }
      .muted { font-size: 11px; color: var(--text-3); margin: 0; }
      .num { font-family: var(--font-mono); }
    `,
  ],
})
export class RegimeStripComponent {
  @Input() snapshot: MacroSnapshot | null = null;

  /** Centre of the active growth quadrant (no history trail — see plan §0). */
  dotLeft(s: MacroSnapshot): number {
    return s.growth_quadrant === 'expansion' || s.growth_quadrant === 'recovery' ? 72 : 28;
  }
  dotTop(s: MacroSnapshot): number {
    return s.growth_quadrant === 'expansion' || s.growth_quadrant === 'slowdown' ? 28 : 72;
  }

  chipKind(kind: string, value: string): PillKind {
    const map: Record<string, Record<string, PillKind>> = {
      growth: { expansion: 'ok', recovery: 'ok', slowdown: 'warn', recession: 'err' },
      inflation: { low: 'ok', moderate: '', high: 'warn', accelerating: 'err' },
      curve: { normal: 'ok', flat: 'warn', inverted: 'err' },
      policy: { easing: 'info', neutral: '', tightening: 'warn' },
    };
    return map[kind]?.[value] ?? '';
  }

  /**
   * Hover tooltip for each macro chip. Values + thresholds mirror
   * `hedgefund_agents.macro.macro_agent.classify_regime` (unchanged from the
   * original dashboard implementation).
   */
  chipTooltip(kind: string, value: string): string {
    const HEADERS: Record<string, string> = {
      growth:
        'Growth quadrant — derived from unemployment (UNRATE) and industrial production (INDPRO).',
      inflation: 'Inflation regime — based on CPI level (CPIAUCSL).',
      curve: 'Yield curve — 10-year minus 2-year Treasury spread (T10Y2Y).',
      policy: 'Policy stance — Federal Funds effective rate (FEDFUNDS).',
    };
    const VALUES: Record<string, Record<string, string>> = {
      growth: {
        expansion: 'Expansion: unemployment ≤4% and INDPRO ≥100. Healthy growth — risk-on tilt favoured.',
        recovery: 'Recovery: improving labour market, INDPRO climbing back. Early-cycle conditions.',
        slowdown: 'Slowdown: unemployment ≥4.5%. Late-cycle deceleration — trim cyclicals, watch credit.',
        recession: 'Recession: unemployment ≥5.5% and INDPRO ≤100. Defensive tilt; growth contracting.',
      },
      inflation: {
        low: 'Low: CPI <290. Disinflationary backdrop — supports long duration / growth equities.',
        moderate: 'Moderate: CPI 290–320. Mid-range price pressures — broad-market neutral.',
        high: 'High: CPI ≥320. Persistent inflation — favours commodities, value, short duration.',
        accelerating: 'Accelerating: inflation rising fast — defensive tilt, reduce long duration.',
      },
      curve: {
        normal: 'Normal: 10y−2y ≥0.5pp. Healthy term premium; no recession signal from the curve.',
        flat: 'Flat: 10y−2y in (0, 0.5pp). Late-cycle warning — curve is compressing.',
        inverted: 'Inverted: 10y−2y <0. Historically a recession leading indicator (12–18mo lag).',
      },
      policy: {
        easing: 'Easing: Fed Funds ≤2%. Stimulative monetary policy — supports risk assets and duration.',
        neutral: 'Neutral: Fed Funds 2–4.5%. Neither restrictive nor stimulative.',
        tightening: 'Tightening: Fed Funds ≥4.5%. Restrictive policy — headwind for duration and growth.',
      },
    };
    const header = HEADERS[kind] ?? '';
    const explanation = VALUES[kind]?.[value] ?? `Current value: ${value}.`;
    return header ? `${header}\n\n${explanation}` : explanation;
  }

  markovTooltip(mc: MarkovConsensus): string {
    const header =
      'Markov regime consensus — deterministic, price-based regime detector ' +
      'on SPY, QQQ, the 11 SPDR sector ETFs, and TLT/GLD/UUP. Snapshots ' +
      'refit at each pod cycle using your provider key ' +
      '(BYOK — there is no platform-key prewarm).';
    const strength = (mc.consensus_strength * 100).toFixed(0);
    const tally =
      `${mc.vote['bull'] ?? 0} bull · ${mc.vote['sideways'] ?? 0} sideways · ` +
      `${mc.vote['bear'] ?? 0} bear (out of ${mc.available_count} fresh snapshots` +
      (mc.stale_count > 0 ? `, ${mc.stale_count} stale` : '') +
      ').';
    return (
      `${header}\n\nCurrent: ${mc.consensus_state} at ${strength}% agreement.\n${tally}\n\n` +
      'Disagreement with the LLM macro classification is itself a signal — see the strategy detail page widget.'
    );
  }
}
