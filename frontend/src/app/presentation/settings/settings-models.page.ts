import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';
import { ModelsStore } from '../../abstraction/models.store';
import {
  AGENT_DISPLAY,
  GROUP_LABEL,
  ModelEntry,
  ModelTier,
  PRESET_NAMES,
  TierConfig,
  USER_TIER_DEFAULT_PRESETS,
  estimateAgentCost,
  estimateRunCost,
} from '../../core/models/model.types';

type TierFilter = 'all' | ModelTier;

/**
 * Settings › Models — preset, defaults, per-agent matrix, model catalog.
 *
 * This is the slimmed-down successor to the former monolithic settings page;
 * provider keys, portfolio, news and persona evolution now live on their own
 * /settings/* tabs. Behavior preserved: preset + cost-ceiling save via the
 * button (dirty-gated), per-agent selections autosave, global-default applies
 * to all agents, OpenRouter fetch/verify.
 *
 * Two readouts reuse the existing (previously unused) estimateRunCost /
 * estimateAgentCost helpers to honor the mockup's cost-visualization intent
 * with real pricing data: an estimated-cost-per-run gauge measured against the
 * cost ceiling, and a per-agent $ estimate in the matrix.
 */
@Component({
  selector: 'hf-settings-models',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent, SettingsTabsComponent],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Settings' }, { label: 'Models' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">Models</h1>
        </div>
      </div>

      <hf-settings-tabs />

      <div role="tabpanel" aria-label="Models settings" class="flex flex-col gap-[18px] max-w-[1100px]">
        <!-- Defaults & cost ceiling -->
        <section class="card">
          <div class="card-hd"><h2 class="title">Defaults &amp; cost ceiling</h2></div>
          <div class="card-bd flex flex-col gap-4">
            <p class="text-[11.5px] text-text-3 m-0">
              These apply to manual runs &amp; the questionnaire. <b>Autonomous Fund</b> runs use a
              per-account preset set on each strategy’s
              <a class="underline" routerLink="/fund">Autopilot page</a> — they don’t inherit the settings here.
            </p>
            <!-- Preset picker (card-based; bound to the same preset model) -->
            <div class="field">
              <span class="lbl">Preset · used when no global default is set</span>
              <div class="preset-grid" role="radiogroup" aria-label="Preset" data-test="preset-select">
                @for (p of presets; track p) {
                  <label class="preset-card" [class.selected]="preset === p">
                    <input type="radio" name="preset" [value]="p"
                           [(ngModel)]="preset" (ngModelChange)="loadPresetOverrides()"
                           [attr.data-test]="'preset-' + p" />
                    <span class="nm">{{ p }}</span>
                  </label>
                }
              </div>
            </div>

            <div class="two-col">
              <div class="field">
                <label class="lbl" for="global-default">Default model · applies to every agent</label>
                <select id="global-default" class="input sans" [(ngModel)]="globalDefault" name="globalDefault"
                  aria-describedby="global-default-note"
                  (ngModelChange)="applyGlobalDefault($event)" data-test="global-default-select">
                  <option value="">— use preset per-agent rules —</option>
                  @for (m of store.models(); track m.id) {
                    <option [value]="m.id" [disabled]="!m.available">
                      {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                    </option>
                  }
                </select>
                <p id="global-default-note" class="text-[11.5px] text-text-3 m-0 mt-1">
                  Sets the same model for every agent. Clear to fall back to the preset rules.
                </p>
              </div>

              <div class="field">
                <label class="lbl" for="cost-ceiling">Cost ceiling · per scheduled run (USD)</label>
                <input id="cost-ceiling" class="input" type="number" step="0.5" min="0" [(ngModel)]="ceiling" name="ceil" />
                <p class="text-[11.5px] text-text-3 m-0 mt-1">
                  New runs queue once the projected run cost would exceed this ceiling.
                </p>
              </div>
            </div>

            <!-- Estimated cost / run vs ceiling — real data via estimateRunCost() -->
            <div>
              <div class="flex items-center justify-between mb-1.5">
                <span class="eyebrow">Estimated cost / run</span>
                <span class="num text-[12px]" data-test="est-run-cost"
                      [style.color]="overCeiling() ? 'var(--acc-short-fg)' : 'var(--text-2)'">
                  {{ fmtUsd(estRunCost()) }}@if (ceilingNum() > 0) {<span class="text-text-3"> / {{ fmtUsd(ceilingNum()) }} · {{ gaugePctLabel() }}</span>}
                </span>
              </div>
              <div class="gauge" role="img" [attr.aria-label]="gaugeAria()">
                <div class="fill" [class.over]="overCeiling()" [style.width.%]="gaugePct()"></div>
              </div>
              <p class="text-[11px] text-text-3 m-0 mt-1.5">
                Estimated from per-agent token sizes × current model pricing across all
                {{ store.agents().length }} agents. A single-ticker planning aid, not a billed amount.
              </p>
            </div>

            <button type="button" class="btn primary save-btn save-btn--wide"
              (click)="savePrefs()"
              [disabled]="savingPrefs() || !isPrefsDirty()"
              data-test="save-prefs">
              {{ savingPrefs() ? 'Saving…' : (isPrefsDirty() ? 'Save preferences' : 'No changes') }}
            </button>
            @if (prefsMsg()) {
              <p role="status" aria-live="polite" class="text-[11.5px] text-[var(--acc-long-fg)] m-0">{{ prefsMsg() }}</p>
            }
          </div>
        </section>

        <!-- Per-agent defaults -->
        <section class="card">
          <div class="card-hd flex items-center justify-between gap-3">
            <h2 class="title">Per-agent defaults</h2>
            <label class="show-all-row">
              <input type="checkbox" id="per-agent-show-all"
                     [checked]="showAllPerAgent()"
                     (change)="showAllPerAgent.set($any($event.target).checked)"
                     data-test="per-agent-show-all" />
              <span class="text-[11.5px] text-text-3">Show all models</span>
            </label>
          </div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              "Current default" = what the active preset (<b>{{ preset }}</b>) resolves to. Pick an explicit model to override it for this agent.
              Selects are scoped to the preset's curated menu; toggle "Show all models" to reach the full catalog.
            </p>
            <p class="text-[11.5px] text-text-3 m-0 italic" data-test="per-agent-autosave-note">
              Per-agent selections save automatically. Preset and cost ceiling save via the button above.
            </p>
            @for (g of groupedAgents(); track g.group) {
              <div>
                <div class="eyebrow border-b border-solid border-border pb-1.5 mb-2">
                  {{ groupLabel(g.group) }} ({{ g.agents.length }})
                </div>
                <div class="agent-grid">
                  @for (a of g.agents; track a) {
                    <div class="agent-row">
                      <div class="flex items-center gap-1.5 min-w-0">
                        <span class="truncate">{{ display(a) }}</span>
                        @if (tierForAgent(a)) {
                          <span class="tier-pill" [ngClass]="tierForAgent(a)">{{ tierLabel(tierForAgent(a)) }}</span>
                        }
                      </div>
                      <div class="mono text-[11.5px] truncate"
                        [style.color]="agentDefault(a) ? 'var(--text-2)' : 'var(--text-3)'">
                        {{ resolvedDefault(a) }} · {{ fmtUsd(estForAgent(a)) }}
                      </div>
                      <select class="input sans agent-select"
                        [ngModel]="agentDefault(a)"
                        (ngModelChange)="setAgentDefault(a, $event)"
                        [attr.aria-label]="'Model for ' + a">
                        <option value="">— use preset default —</option>
                        @for (m of visiblePerAgentModels(a); track m.id) {
                          <option [value]="m.id" [disabled]="!m.available">
                            {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                          </option>
                        }
                      </select>
                    </div>
                  }
                </div>
              </div>
            }
          </div>
        </section>

        <!-- Default model per tier -->
        <section class="card">
          <div class="card-hd"><h2 class="title">Default model per tier</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              Pick a default per price tier. It anchors the analytical &amp; orchestration
              agents for that tier and is the resilience fallback when a model is retired —
              personas still spread across the tier's menu. Saved with the button above.
            </p>
            <div class="agent-grid">
              @for (t of tierDefaultPresets; track t) {
                <div class="agent-row">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <span class="tier-pill">{{ t }}</span>
                  </div>
                  <div class="mono text-[11.5px] truncate"
                       [style.color]="tierDefault(t) ? 'var(--text-2)' : 'var(--text-3)'">
                    {{ tierDefaultLabel(t) }}@if (tierDefault(t)) { · {{ fmtUsd(tierDefaultEst(t)) }}}
                  </div>
                  <select class="input sans agent-select"
                    [ngModel]="tierDefault(t)"
                    (ngModelChange)="setTierDefault(t, $event)"
                    [attr.data-test]="'tier-default-' + t">
                    <option value="">— use system tier default —</option>
                    @for (m of visibleTierModels(t); track m.id) {
                      <option [value]="m.id" [disabled]="!m.available">
                        {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                      </option>
                    }
                  </select>
                </div>
              }
            </div>
          </div>
        </section>

        <!-- Available models -->
        <section class="card">
          <div class="card-hd flex items-center justify-between flex-wrap gap-2">
            <h2 class="title">Available models ({{ store.models().length }})</h2>
            <div class="flex items-center gap-2 flex-wrap">
              <div class="seg models-filter" role="group" aria-label="Filter available models by tier">
                @for (f of tierFilters; track f.value) {
                  <button type="button" class="opt" [class.on]="modelFilter() === f.value"
                    [attr.aria-pressed]="modelFilter() === f.value"
                    (click)="modelFilter.set(f.value)"
                    [attr.data-test]="'models-filter-' + f.value">{{ f.label }}</button>
                }
              </div>
              <button type="button" class="btn"
                (click)="fetchOpenRouter()"
                [disabled]="fetchingOpenRouter()"
                data-test="fetch-openrouter">
                {{ fetchingOpenRouter() ? 'Fetching…' : 'Fetch latest OpenRouter models' }}
              </button>
              <button type="button" class="btn"
                (click)="verifyAllOpenRouter()"
                [disabled]="verifyingAll() || openRouterCount() === 0"
                data-test="verify-all-openrouter">
                {{ verifyingAll() ? 'Verifying…' : 'Verify all OpenRouter pricing' }}
              </button>
            </div>
          </div>
          @if (fetchMsg()) {
            <p role="status" aria-live="polite"
              class="text-[11.5px] m-0 px-3 pt-2"
              [style.color]="fetchHasIssues() ? 'var(--acc-short-fg)' : 'var(--acc-long-fg)'"
              data-test="fetch-msg">{{ fetchMsg() }}</p>
          }
          @if (verifyMsg()) {
            <p role="status" aria-live="polite"
              class="text-[11.5px] m-0 px-3 pt-2"
              [style.color]="verifyHasFailures() ? 'var(--acc-short-fg)' : 'var(--acc-long-fg)'"
              data-test="verify-msg">{{ verifyMsg() }}</p>
          }
          <div class="tbl-scroll">
            <table class="tbl">
              <thead><tr>
                <th>Model</th><th>Provider</th><th>Tier</th>
                <th class="right">$/Mtok in</th><th class="right">$/Mtok out</th>
                <th>Status</th><th>Verified</th><th></th>
              </tr></thead>
              <tbody>
                @for (m of filteredModels(); track m.id) {
                  <tr [attr.data-test-row]="m.id">
                    <td class="mono text-text">
                      {{ m.display_name }}
                      @if (isFree(m)) {
                        <span class="badge-free" data-test="badge-free">FREE</span>
                      }
                      @if (m.supports_reasoning) {
                        <span class="badge-reasoning" data-test="badge-reasoning"
                              title="Reasoning model — spends tokens on hidden chain-of-thought">🧠 reasoning</span>
                      }
                    </td>
                    <td>{{ m.provider }}</td>
                    <td>{{ m.tier }}</td>
                    <td class="num">{{ m.price_in_per_mtok ?? '0' }}</td>
                    <td class="num">{{ m.price_out_per_mtok ?? '0' }}</td>
                    <td>
                      <span class="pill" [class.ok]="m.available">
                        <span class="dot"></span>{{ m.available ? 'available' : 'no key' }}
                      </span>
                    </td>
                    <td class="text-[11.5px]" [attr.data-test-verified]="m.id">
                      @if (m.provider !== 'openrouter') {
                        <span class="text-text-3">—</span>
                      } @else if (m.last_verified_note) {
                        <span class="text-[var(--acc-short-fg)]" [title]="m.last_verified_note">
                          drift · {{ relTime(m.last_verified_at) }}
                        </span>
                      } @else if (m.last_verified_at) {
                        <span class="text-[var(--acc-long-fg)]">
                          ✓ {{ relTime(m.last_verified_at) }}
                        </span>
                      } @else {
                        <span class="text-text-3">never</span>
                      }
                    </td>
                    <td>
                      @if (m.provider === 'openrouter') {
                        <button type="button" class="btn btn-xs"
                          (click)="verifyOne(m.id)"
                          [disabled]="isVerifying(m.id) || verifyingAll()"
                          [attr.data-test]="'verify-' + m.id">
                          {{ isVerifying(m.id) ? '…' : 'Verify' }}
                        </button>
                      }
                    </td>
                  </tr>
                }
                @if (filteredModels().length === 0) {
                  <tr><td colspan="8" class="text-text-3 text-[12px] p-3">No models match this filter.</td></tr>
                }
              </tbody>
            </table>
          </div>
        </section>

        <!-- Tier membership (operator-only; hidden unless GET /tiers/ succeeds) -->
        @if (tierEditorEnabled()) {
          <details class="card">
            <summary class="card-hd op-summary">
              <h2 class="title">Tier membership <span class="op-badge">operator</span></h2>
            </summary>
            <div class="card-bd flex flex-col gap-4">
              <p class="text-[11.5px] text-text-3 m-0">
                Global configuration — affects all users. Curate which models populate each
                tier's menu and set the tier's default. Guards (no reasoning on cheap tiers,
                dev free-only, frugal ceiling) are enforced server-side.
              </p>
              @for (tc of store.tierConfigs(); track tc.tier_name) {
                <div class="op-tier">
                  <div class="flex items-center gap-2 mb-2">
                    <span class="tier-pill">{{ tc.tier_name }}</span>
                    @if (tc.allow_reasoning) { <span class="badge-reasoning">🧠 allowed</span> }
                    @if (tc.is_free_only) { <span class="badge-free">FREE only</span> }
                  </div>
                  <div class="op-members">
                    @for (mid of tierMembers(tc.tier_name); track mid) {
                      <span class="op-chip">
                        {{ memberLabel(mid) }}
                        <button type="button" class="op-x"
                          (click)="removeMember(tc.tier_name, mid)"
                          [attr.aria-label]="'Remove ' + mid">×</button>
                      </span>
                    }
                    @if (tierMembers(tc.tier_name).length === 0) {
                      <span class="text-text-3 text-[11.5px]">No members.</span>
                    }
                  </div>
                  <div class="op-controls">
                    <select class="input sans agent-select" [ngModel]="''"
                      (ngModelChange)="addMember(tc.tier_name, $event)"
                      [attr.data-test]="'op-add-' + tc.tier_name">
                      <option value="">+ add model…</option>
                      @for (m of addableModels(tc.tier_name); track m.id) {
                        <option [value]="m.id">{{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} · {{ m.tier }}</option>
                      }
                    </select>
                    <select class="input sans agent-select"
                      [ngModel]="tierDefaultModel(tc.tier_name)"
                      (ngModelChange)="setTierDefaultModel(tc.tier_name, $event)"
                      [attr.data-test]="'op-default-' + tc.tier_name">
                      <option value="">— no default —</option>
                      @for (mid of tierMembers(tc.tier_name); track mid) {
                        <option [value]="mid">default: {{ memberLabel(mid) }}</option>
                      }
                    </select>
                    <button type="button" class="btn sm primary"
                      (click)="saveTier(tc.tier_name)"
                      [disabled]="!isTierDirty(tc.tier_name) || tierSaving() === tc.tier_name"
                      [attr.data-test]="'op-save-' + tc.tier_name">
                      {{ tierSaving() === tc.tier_name ? 'Saving…' : (isTierDirty(tc.tier_name) ? 'Save' : 'Saved') }}
                    </button>
                  </div>
                  @if (tierMsg()[tc.tier_name]) {
                    <p class="text-[11px] m-0 mt-1"
                       [style.color]="tierErr()[tc.tier_name] ? 'var(--acc-short-fg)' : 'var(--acc-long-fg)'">
                      {{ tierMsg()[tc.tier_name] }}
                    </p>
                  }
                </div>
              }
            </div>
          </details>
        }
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .two-col {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 18px;
      }
      .preset-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
        gap: 8px;
      }
      .preset-card {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 10px 12px;
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        cursor: pointer;
        background: var(--surface);
        transition: border-color 80ms, background 80ms;
      }
      .preset-card:hover { border-color: var(--text-3); }
      .preset-card.selected { border-color: var(--acc-info); background: var(--acc-info-soft, var(--surface)); }
      /* Visually-hidden radio; the card carries the selected state + focus ring. */
      .preset-card input {
        position: absolute;
        width: 1px; height: 1px;
        opacity: 0;
        margin: 0;
      }
      .preset-card:focus-within { box-shadow: var(--focus-ring); outline: none; }
      .preset-card .nm {
        font-size: 13px;
        font-weight: 600;
        text-transform: capitalize;
        color: var(--text);
      }
      .preset-card.selected .nm { color: var(--acc-info-fg); }

      .gauge {
        position: relative;
        height: 8px;
        border-radius: var(--r-full);
        background: var(--surface-2);
        border: 1px solid var(--border);
        overflow: hidden;
      }
      .gauge .fill {
        position: absolute;
        inset: 0 auto 0 0;
        min-width: 2px;
        background: var(--acc-long);
        border-radius: inherit;
        transition: width 180ms var(--ease-out-ui, ease);
      }
      .gauge .fill.over { background: var(--acc-short); }

      .show-all-row {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .agent-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px 24px;
      }
      .agent-row {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 8px;
        align-items: center;
        font-size: 13px;
      }
      .agent-select {
        height: 26px;
        font-size: 11.5px;
        padding: 0 6px;
      }
      .tier-pill {
        flex: none;
        font-size: 9.5px;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        padding: 1px 6px;
        border-radius: 4px;
        border: 1px solid var(--border-2);
        color: var(--text-3);
        background: var(--surface-2);
      }
      .tier-pill.frontier { color: var(--acc-info-fg); background: var(--acc-info-soft); border-color: var(--acc-info-soft); }
      .tier-pill.fast_cheap { color: var(--acc-long-fg); background: var(--acc-long-soft); border-color: var(--acc-long-soft); }
      .tier-pill.hosted_open { color: var(--acc-hold-fg); background: var(--acc-hold-soft); border-color: var(--acc-hold-soft); }
      .tier-pill.local { color: var(--text-2); }

      .models-filter { width: auto; }
      .models-filter .opt { padding: 0 10px; }

      .tbl-scroll {
        max-height: 380px;
        overflow: auto;
      }
      .tbl-scroll thead th {
        position: sticky;
        top: 0;
        background: var(--surface);
        z-index: 1;
      }

      .badge-free {
        display: inline-block;
        margin-left: 6px;
        padding: 1px 6px;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.04em;
        border-radius: 4px;
        background: var(--acc-long-soft, #e6f4ea);
        color: var(--acc-long-fg, #1b5e20);
        vertical-align: middle;
      }
      .badge-reasoning {
        display: inline-block;
        margin-left: 6px;
        padding: 1px 6px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.02em;
        border-radius: 4px;
        background: var(--acc-info-soft, #e7eefc);
        color: var(--acc-info-fg, #1a3e8c);
        vertical-align: middle;
      }
      .btn-xs { height: 22px; padding: 0 8px; font-size: 11px; }
      .save-btn { height: 32px; justify-content: center; }
      .save-btn--wide { align-self: flex-start; min-width: 200px; }

      .op-summary { cursor: pointer; list-style: none; }
      .op-summary::-webkit-details-marker { display: none; }
      .op-badge {
        margin-left: 8px;
        padding: 1px 6px;
        font-size: 9.5px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        border-radius: 4px;
        color: var(--acc-short-fg);
        background: var(--acc-short-soft, #fdecea);
        vertical-align: middle;
      }
      .op-tier { border-top: 1px solid var(--border); padding-top: 12px; }
      .op-members { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
      .op-chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 11.5px;
        padding: 2px 6px;
        border: 1px solid var(--border-2);
        border-radius: 4px;
        background: var(--surface-2);
      }
      .op-x {
        border: none;
        background: none;
        color: var(--text-3);
        cursor: pointer;
        font-size: 14px;
        line-height: 1;
        padding: 0 2px;
      }
      .op-x:hover { color: var(--acc-short-fg); }
      .op-controls {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
      }

      @media (max-width: 860px) {
        .two-col { grid-template-columns: 1fr; }
        .agent-grid { grid-template-columns: 1fr; }
      }
    `,
  ],
})
export class SettingsModelsPage implements OnInit {
  readonly store = inject(ModelsStore);

  readonly presets = PRESET_NAMES;

  preset = 'research';
  ceiling: number | null = 5;
  globalDefault = 'openrouter:meta-llama/llama-3.3-70b-instruct';

  savingPrefs = signal(false);
  prefsMsg = signal<string | null>(null);
  presetOverrides = signal<Record<string, string>>({});
  presetMenu = signal<string[]>([]);
  showAllPerAgent = signal(false);
  fetchingOpenRouter = signal(false);
  fetchMsg = signal<string | null>(null);
  fetchHasIssues = signal(false);
  // P02d review: track which preset/ceiling values are persisted so the
  // Save button can be disabled when nothing is dirty (consistent save UX).
  private savedPreset = 'research';
  private savedCeiling: number | null = 5;

  // Per-tier USER default (saved with the prefs Save button).
  readonly tierDefaultPresets = USER_TIER_DEFAULT_PRESETS;
  tierMenus = signal<Record<string, string[]>>({});
  tierDefaults = signal<Record<string, string>>({});
  private savedTierDefaults = '{}';

  // Operator tier-membership editor (lazy; hidden unless GET /tiers/ succeeds).
  tierEditorEnabled = signal(false);
  private tierEdits = signal<Record<string, { members: string[]; default_model: string }>>({});
  private savedTierEdits: Record<string, string> = {};
  tierSaving = signal<string | null>(null);
  tierMsg = signal<Record<string, string>>({});
  tierErr = signal<Record<string, boolean>>({});

  isPrefsDirty(): boolean {
    return this.preset !== this.savedPreset
      || this.ceiling !== this.savedCeiling
      || JSON.stringify(this.tierDefaults()) !== this.savedTierDefaults;
  }

  ngOnInit(): void {
    this.store.loadAll().subscribe(() => {
      const prefs = this.store.prefs();
      if (prefs) {
        this.preset = prefs.preset ?? 'research';
        this.ceiling = prefs.cost_ceiling_per_run_usd
          ? Number(prefs.cost_ceiling_per_run_usd) : null;
        // Snapshot persisted values so isPrefsDirty() works.
        this.savedPreset = this.preset;
        this.savedCeiling = this.ceiling;
        const vals = Object.values(prefs.per_agent_defaults ?? {});
        const allSame = vals.length > 0 && vals.every((v) => v === vals[0]);
        this.globalDefault = allSame ? (vals[0] as string) : '';
      }
      this.tierDefaults.set({ ...(this.store.prefs()?.per_tier_defaults ?? {}) });
      this.savedTierDefaults = JSON.stringify(this.tierDefaults());
      this.loadPresetOverrides();
      this.loadTierMenus();
      this.loadTierEditor();
    });
  }

  // ---- Per-tier USER default --------------------------------------------
  private loadTierMenus(): void {
    const reqs = Object.fromEntries(
      this.tierDefaultPresets.map((t) => [t, this.store.fetchPreset(t)]),
    );
    forkJoin(reqs).subscribe((res: Record<string, { menu?: string[] }>) => {
      const menus: Record<string, string[]> = {};
      for (const t of this.tierDefaultPresets) menus[t] = res[t].menu ?? [];
      this.tierMenus.set(menus);
    });
  }

  tierDefault(tier: string): string { return this.tierDefaults()[tier] ?? ''; }
  setTierDefault(tier: string, modelId: string): void {
    const next = { ...this.tierDefaults() };
    if (modelId) next[tier] = modelId; else delete next[tier];
    this.tierDefaults.set(next);
  }
  /** Scope the catalog to a tier/preset menu (∪ discovered Ollama), always
   *  re-including `currentId` if it falls outside the menu (stale selection).
   *  Shared by the per-agent and per-tier selects. Empty menu → full catalog. */
  private _scopeToMenu(menu: string[], currentId: string): ModelEntry[] {
    const all = this.store.models();
    if (!menu.length) return all;
    const menuSet = new Set(menu);
    const out = all.filter((m) => menuSet.has(m.id) || m.provider === 'ollama');
    if (currentId && !out.some((m) => m.id === currentId)) {
      const stale = all.find((m) => m.id === currentId);
      if (stale) out.push(stale);
    }
    return out;
  }
  /** Catalog display name for a model id, falling back to the id. */
  private _modelName(id: string): string {
    return this.store.models().find((m) => m.id === id)?.display_name ?? id;
  }

  visibleTierModels(tier: string): ModelEntry[] {
    return this._scopeToMenu(this.tierMenus()[tier] ?? [], this.tierDefault(tier));
  }
  tierDefaultLabel(tier: string): string {
    const id = this.tierDefault(tier);
    return id ? this._modelName(id) : '— system default —';
  }
  tierDefaultEst(tier: string): number {
    const id = this.tierDefault(tier);
    return id ? estimateAgentCost('portfolio_manager', id, this.store.models()) : 0;
  }

  // ---- Operator tier-membership editor ----------------------------------
  private loadTierEditor(): void {
    this.store.loadTierConfigs().subscribe({
      next: (r) => {
        const edits: Record<string, { members: string[]; default_model: string }> = {};
        const saved: Record<string, string> = {};
        for (const tc of r.tiers) {
          const e = {
            members: tc.members.map((m) => m.model_id),
            default_model: tc.default_model ?? '',
          };
          edits[tc.tier_name] = e;
          saved[tc.tier_name] = JSON.stringify(e);
        }
        this.tierEdits.set(edits);
        this.savedTierEdits = saved;
        this.tierEditorEnabled.set(true);
      },
      error: () => this.tierEditorEnabled.set(false),  // 403 for non-staff → hide
    });
  }
  tierMembers(tier: string): string[] { return this.tierEdits()[tier]?.members ?? []; }
  tierDefaultModel(tier: string): string { return this.tierEdits()[tier]?.default_model ?? ''; }
  memberLabel(mid: string): string {
    return this._modelName(mid);
  }
  addableModels(tier: string): ModelEntry[] {
    const members = new Set(this.tierMembers(tier));
    return this.store.models().filter((m) => !members.has(m.id));
  }
  private mutateTier(
    tier: string,
    fn: (e: { members: string[]; default_model: string }) => void,
  ): void {
    const cur = this.tierEdits()[tier] ?? { members: [], default_model: '' };
    const next = { members: [...cur.members], default_model: cur.default_model };
    fn(next);
    this.tierEdits.set({ ...this.tierEdits(), [tier]: next });
  }
  addMember(tier: string, mid: string): void {
    if (!mid) return;
    this.mutateTier(tier, (e) => { if (!e.members.includes(mid)) e.members.push(mid); });
  }
  removeMember(tier: string, mid: string): void {
    this.mutateTier(tier, (e) => {
      e.members = e.members.filter((x) => x !== mid);
      if (e.default_model === mid) e.default_model = '';
    });
  }
  setTierDefaultModel(tier: string, mid: string): void {
    this.mutateTier(tier, (e) => { e.default_model = mid; });
  }
  isTierDirty(tier: string): boolean {
    return JSON.stringify(this.tierEdits()[tier]) !== (this.savedTierEdits[tier] ?? '');
  }
  saveTier(tier: string): void {
    const e = this.tierEdits()[tier];
    if (!e) return;
    this.tierSaving.set(tier);
    this.store.saveTierConfig(tier, {
      members: e.members,
      default_model: e.default_model || null,
    }).subscribe({
      next: (updated) => {
        this.tierSaving.set(null);
        const saved = {
          members: updated.members.map((m) => m.model_id),
          default_model: updated.default_model ?? '',
        };
        this.tierEdits.set({ ...this.tierEdits(), [tier]: saved });
        this.savedTierEdits = { ...this.savedTierEdits, [tier]: JSON.stringify(saved) };
        this.tierMsg.set({ ...this.tierMsg(), [tier]: 'Saved.' });
        this.tierErr.set({ ...this.tierErr(), [tier]: false });
      },
      error: (err) => {
        this.tierSaving.set(null);
        this.tierMsg.set({ ...this.tierMsg(), [tier]: err?.error?.detail || 'Save failed.' });
        this.tierErr.set({ ...this.tierErr(), [tier]: true });
      },
    });
  }

  applyGlobalDefault(modelId: string): void {
    const next: Record<string, string> = {};
    if (modelId) {
      for (const a of this.store.agents()) next[a.id] = modelId;
    }
    this.store.savePrefs({ per_agent_defaults: next }).subscribe(() => {
      this.prefsMsg.set(modelId ? 'Default model applied to all agents.' : 'Cleared.');
    });
  }

  loadPresetOverrides(): void {
    this.store.fetchPreset(this.preset).subscribe((r) => {
      this.presetOverrides.set(r.overrides);
      this.presetMenu.set(r.menu ?? []);
    });
  }

  /**
   * The list of <option> models for a given per-agent default select.
   *
   * - default: tier menu ∪ all discovered local models; always include
   *   the saved per-agent default even if it falls outside the current
   *   menu (e.g. chosen under a different preset)
   * - "Show all": full catalog
   * - empty menu: fall back to the full catalog so the dropdown never
   *   renders empty before loadPresetOverrides() resolves
   */
  visiblePerAgentModels(a: string): ModelEntry[] {
    if (this.showAllPerAgent()) return this.store.models();
    return this._scopeToMenu(this.presetMenu(), this.agentDefault(a));
  }

  fetchOpenRouter(): void {
    this.fetchingOpenRouter.set(true);
    this.fetchMsg.set(null);
    this.fetchHasIssues.set(false);
    this.store.fetchOpenRouterModels().subscribe({
      next: (r) => {
        this.fetchingOpenRouter.set(false);
        const parts: string[] = [];
        if (r.synced?.length) parts.push(`synced ${r.synced.length}`);
        if (r.created?.length) parts.push(`created ${r.created.length}`);
        if (r.deactivated?.length) {
          parts.push(`deactivated ${r.deactivated.length} (${r.deactivated.join(', ')})`);
        }
        if (r.excluded?.length) {
          parts.push(
            `excluded ${r.excluded.length} (${r.excluded.map((e) => `${e.slug}: ${e.reason}`).join('; ')})`,
          );
        }
        if (r.swept?.length) {
          parts.push(`retired ${r.swept.length} (${r.swept.join(', ')})`);
        }
        // deactivated/excluded = curation problems (alert); swept = routine cleanup.
        this.fetchHasIssues.set(
          (r.deactivated?.length ?? 0) + (r.excluded?.length ?? 0) > 0,
        );
        this.fetchMsg.set(parts.length ? parts.join(' · ') : 'No changes.');
        // Refresh availability flags by reloading the catalog (the fetch
        // response merges in pricing but availability is recomputed by
        // /models/ from the user's keys + LLM_FREE_ONLY/BLOCK_ANTHROPIC).
        this.store.loadModels().subscribe();
      },
      error: (err) => {
        this.fetchingOpenRouter.set(false);
        this.fetchHasIssues.set(true);
        this.fetchMsg.set(err?.error?.detail || 'Fetch failed.');
      },
    });
  }

  resolvedDefault(a: string): string {
    const override = this.agentDefault(a);
    if (override) return this._modelName(override) + ' (override)';
    const fromPreset = this.presetOverrides()[a];
    if (fromPreset) return this._modelName(fromPreset);
    const sysDefault = this.store.agents().find((x) => x.id === a)?.default_model;
    if (sysDefault) return this._modelName(sysDefault) + ' (system)';
    return '—';
  }

  display(a: string): string { return AGENT_DISPLAY[a] ?? a; }
  agentIds(): string[] { return this.store.agents().map((a) => a.id); }
  groupLabel(g: string): string { return GROUP_LABEL[g] ?? g; }
  groupedAgents(): { group: string; agents: string[] }[] {
    const order = ['persona', 'analyst', 'context', 'orchestration', 'other'];
    const buckets: Record<string, string[]> = {};
    for (const a of this.store.agents()) {
      (buckets[a.group ?? 'other'] ??= []).push(a.id);
    }
    return order.filter((g) => buckets[g]?.length).map((g) => ({ group: g, agents: buckets[g] }));
  }
  agentDefault(a: string): string {
    return this.store.prefs()?.per_agent_defaults?.[a] ?? '';
  }
  setAgentDefault(a: string, v: string): void {
    const prefs = this.store.prefs();
    const next = { ...(prefs?.per_agent_defaults ?? {}) };
    if (v) next[a] = v; else delete next[a];
    this.store.savePrefs({ per_agent_defaults: next }).subscribe();
  }

  savePrefs(): void {
    this.savingPrefs.set(true);
    this.store.savePrefs({
      preset: this.preset,
      cost_ceiling_per_run_usd: this.ceiling,
      per_tier_defaults: this.tierDefaults(),
    }).subscribe({
      next: () => {
        this.savingPrefs.set(false);
        this.prefsMsg.set('Saved.');
        this.savedPreset = this.preset;
        this.savedCeiling = this.ceiling;
        this.savedTierDefaults = JSON.stringify(this.tierDefaults());
      },
      error: () => { this.savingPrefs.set(false); this.prefsMsg.set('Failed to save'); },
    });
  }

  // ---- Estimated cost readouts (real: estimateRunCost/estimateAgentCost) ----
  /** Per-agent effective model when no explicit override is set: the active
   *  preset's resolution, falling back to the agent's system default. */
  private effectiveDefaults(): Record<string, string> {
    const overrides = this.presetOverrides();
    const out: Record<string, string> = {};
    for (const a of this.store.agents()) {
      out[a.id] = overrides[a.id] ?? a.default_model;
    }
    return out;
  }
  private effectiveModel(a: string): string | undefined {
    const perAgent = this.store.prefs()?.per_agent_defaults ?? {};
    if (perAgent[a]) return perAgent[a];
    // The per-tier user default anchors non-persona roles (mirrors the backend
    // resolution order: per-agent > per-tier > preset recipe > system default).
    const group = this.store.agents().find((x) => x.id === a)?.group;
    const tierDef = this.tierDefaults()[this.preset];
    if (tierDef && group && group !== 'persona') return tierDef;
    return this.presetOverrides()[a]
      ?? this.store.agents().find((x) => x.id === a)?.default_model;
  }
  estRunCost(): number {
    const agents = this.store.agents().map((a) => a.id);
    const perAgent = this.store.prefs()?.per_agent_defaults ?? {};
    return estimateRunCost(agents, perAgent, this.effectiveDefaults(), this.store.models());
  }
  estForAgent(a: string): number {
    return estimateAgentCost(a, this.effectiveModel(a), this.store.models());
  }
  ceilingNum(): number { return Number(this.ceiling ?? 0) || 0; }
  gaugePct(): number {
    const c = this.ceilingNum();
    if (c <= 0) return 0;
    return Math.min(100, (this.estRunCost() / c) * 100);
  }
  gaugePctLabel(): string { return `${Math.round(this.gaugePct())}%`; }
  overCeiling(): boolean {
    const c = this.ceilingNum();
    return c > 0 && this.estRunCost() > c;
  }
  gaugeAria(): string {
    const c = this.ceilingNum();
    const base = `Estimated cost per run ${this.fmtUsd(this.estRunCost())}`;
    return c > 0 ? `${base} of ${this.fmtUsd(c)} ceiling, ${this.gaugePctLabel()}` : base;
  }
  fmtUsd(n: number): string {
    if (n > 0 && n < 0.01) return '<$0.01';
    return `$${n.toFixed(2)}`;
  }
  tierForAgent(a: string): string {
    const mid = this.effectiveModel(a);
    return this.store.models().find((m) => m.id === mid)?.tier ?? '';
  }
  tierLabel(tier: string): string {
    if (tier === 'fast_cheap') return 'fast';
    if (tier === 'hosted_open') return 'open';
    return tier;
  }

  // ---- Available-models client-side tier filter (pure UI) ------------------
  readonly tierFilters: { value: TierFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'frontier', label: 'Frontier' },
    { value: 'fast_cheap', label: 'Fast' },
    { value: 'hosted_open', label: 'Open' },
    { value: 'local', label: 'Local' },
  ];
  modelFilter = signal<TierFilter>('all');
  filteredModels(): ModelEntry[] {
    const f = this.modelFilter();
    const all = this.store.models();
    return f === 'all' ? all : all.filter((m) => m.tier === f);
  }

  // ---- OpenRouter pricing verification --------------------------------
  private verifyingIds = signal<Set<string>>(new Set());
  verifyingAll = signal(false);
  verifyMsg = signal<string | null>(null);
  verifyHasFailures = signal(false);

  readonly openRouterCount = computed(
    () => this.store.models().filter((m) => m.provider === 'openrouter').length,
  );

  isFree(m: ModelEntry): boolean {
    if (m.is_free !== undefined) return m.is_free;
    const pin = Number(m.price_in_per_mtok ?? 0);
    const pout = Number(m.price_out_per_mtok ?? 0);
    return pin === 0 && pout === 0;
  }

  isVerifying(id: string): boolean {
    return this.verifyingIds().has(id);
  }

  relTime(iso: string | null | undefined): string {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '';
    const diff = Date.now() - t;
    const min = Math.round(diff / 60_000);
    if (min < 1) return 'just now';
    if (min < 60) return `${min}m ago`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.round(hr / 24);
    return `${day}d ago`;
  }

  verifyOne(id: string): void {
    const next = new Set(this.verifyingIds());
    next.add(id);
    this.verifyingIds.set(next);
    this.verifyMsg.set(null);
    this.store.verifyPricing([id]).subscribe({
      next: (r) => {
        const after = new Set(this.verifyingIds());
        after.delete(id);
        this.verifyingIds.set(after);
        const result = r.results[0];
        const failed = result && !result.ok;
        this.verifyHasFailures.set(!!failed);
        this.verifyMsg.set(
          failed
            ? `${id}: ${result.note}`
            : `${id}: pricing matches OpenRouter ($${result?.upstream_price_in_per_mtok ?? '0'} / $${result?.upstream_price_out_per_mtok ?? '0'} per Mtok).`,
        );
      },
      error: (err) => {
        const after = new Set(this.verifyingIds());
        after.delete(id);
        this.verifyingIds.set(after);
        this.verifyHasFailures.set(true);
        this.verifyMsg.set(err?.error?.detail || `Verification failed for ${id}.`);
      },
    });
  }

  verifyAllOpenRouter(): void {
    this.verifyingAll.set(true);
    this.verifyMsg.set(null);
    this.store.verifyPricing().subscribe({
      next: (r) => {
        this.verifyingAll.set(false);
        const total = r.results.length;
        const failed = r.results.filter((x) => !x.ok);
        this.verifyHasFailures.set(failed.length > 0);
        this.verifyMsg.set(
          failed.length === 0
            ? `Verified ${total} OpenRouter model(s) — all pricing matches.`
            : `Verified ${total} OpenRouter model(s) — ${failed.length} failed: ${failed.map((f) => f.model_id).join(', ')}.`,
        );
      },
      error: (err) => {
        this.verifyingAll.set(false);
        this.verifyHasFailures.set(true);
        this.verifyMsg.set(err?.error?.detail || 'Verification failed.');
      },
    });
  }
}
