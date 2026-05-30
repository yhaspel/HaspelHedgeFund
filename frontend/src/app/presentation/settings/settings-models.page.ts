import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';
import { TradeStationAppCardComponent } from './tradestation-app-card.component';
import { ModelsStore } from '../../abstraction/models.store';
import { NewsStore } from '../../abstraction/news.store';
import { PersonaEvolutionStore } from '../../abstraction/persona-evolution.store';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import {
  AGENT_DISPLAY,
  GROUP_LABEL,
  ModelEntry,
  PRESET_NAMES,
} from '../../core/models/model.types';
import { MarkCadence } from '../../core/models/portfolio.model';
import { NewsPreferences } from '../../core/models/news.model';
import {
  EvolutionCadence,
  PersonaEvolutionSettings as EvolutionSettings,
} from '../../core/models/persona-evolution.model';

@Component({
  selector: 'hf-settings-models',
  standalone: true,
  imports: [
    CommonModule, FormsModule, RouterLink, AppShellComponent,
    TradeStationAppCardComponent, SettingsTabsComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{label:'Settings'}, {label:'Models'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">Models</h1>
        </div>
      </div>

      <hf-settings-tabs />

      <div class="grid grid-cols-2 gap-[18px] max-w-[1100px]">
        <!-- A: Provider keys (BYO) -->
        <section class="card">
          <div class="card-hd"><h2 class="title">Provider keys (BYO)</h2></div>
          <div class="card-bd flex flex-col gap-3">
            <p class="text-[11.5px] text-text-3 m-0">
              Keys are stored Fernet-encrypted on your user row. P4a will replace this with a multi-tenant vault.
            </p>

            <div class="eyebrow border-b border-solid border-border pb-1.5">
              LLM providers
            </div>
            @for (p of providers; track p.field) {
              <div class="field">
                <label class="lbl flex justify-between" [attr.for]="'llm-key-' + p.field">
                  <span>{{ p.label }}</span>
                  <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                    <span class="dot"></span>{{ statusOf(p.field) }}
                  </span>
                </label>
                <input [id]="'llm-key-' + p.field" class="input mono" type="password" autocomplete="new-password"
                  [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                  [attr.data-test]="'llm-key-' + p.field"
                  placeholder="•••• paste to replace, blank to keep" />
              </div>
            }
            <div class="field">
              <label class="lbl" for="ollama-host">Ollama host (for local models)</label>
              <input id="ollama-host" class="input mono" type="text" [(ngModel)]="ollamaHost" name="ollama"
                placeholder="http://localhost:11434" data-test="ollama-host" />
            </div>

            <div class="eyebrow border-b border-solid border-border pb-1.5 mt-1.5">
              Data providers
            </div>
            <p class="text-[11.5px] text-text-3 m-0">
              P2n BYOK: FMP and Tiingo require user-supplied keys (no platform fallback in prod). FRED is optional — free public data falls back to a shared platform key.
            </p>
            @for (p of dataProviders; track p.field) {
              <div class="field">
                <label class="lbl flex justify-between" [attr.for]="'data-key-' + p.field">
                  <span>{{ p.label }}</span>
                  <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                    <span class="dot"></span>{{ statusOf(p.field) }}
                  </span>
                </label>
                <input [id]="'data-key-' + p.field" class="input mono" type="password" autocomplete="new-password"
                  [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                  [attr.aria-describedby]="'data-key-note-' + p.field"
                  [attr.data-test]="'data-key-' + p.field"
                  placeholder="•••• paste to replace, blank to keep" />
                <p [id]="'data-key-note-' + p.field" class="text-[11px] text-text-3 m-0 mt-0.5">{{ p.note }}</p>
              </div>
            }

            <button type="button" class="btn primary save-btn" (click)="saveKeys()" [disabled]="savingKeys()"
              data-test="save-keys">
              {{ savingKeys() ? 'Saving…' : 'Save provider keys' }}
            </button>
            @if (keysMsg()) {
              <p role="status" aria-live="polite" class="text-[11.5px] text-[var(--acc-long-fg)] m-0" data-test="keys-msg">{{ keysMsg() }}</p>
            }
          </div>
        </section>

        <!-- A2: TradeStation developer app (BYO) — P3a-3 -->
        <hf-tradestation-app-card />

        <!-- B: Default model + preset + ceiling -->
        <section class="card">
          <div class="card-hd"><h2 class="title">Defaults &amp; cost ceiling</h2></div>
          <div class="card-bd flex flex-col gap-3">
            <div class="field">
              <label class="lbl" for="global-default">Default model (applies to every agent)</label>
              <select id="global-default" class="input sans" [(ngModel)]="globalDefault" name="globalDefault"
                aria-describedby="global-default-note"
                (ngModelChange)="applyGlobalDefault($event)" data-test="global-default-select">
                <option value="">— use preset per-agent rules —</option>
                @for (m of store.models(); track m.id) {
                  <option [value]="m.id" [disabled]="!m.available">
                    {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                  </option>
                }
              </select>
              <p id="global-default-note" class="text-[11.5px] text-text-3 m-0 mt-1">
                Sets the same model for every agent. Clear to fall back to the preset rules below.
              </p>
            </div>

            <div class="field">
              <label class="lbl" for="preset-select">Preset (used when no global default is set)</label>
              <select id="preset-select" class="input sans" [(ngModel)]="preset" name="preset"
                (ngModelChange)="loadPresetOverrides()" data-test="preset-select">
                @for (p of presets; track p) {
                  <option [value]="p">{{ p }}</option>
                }
              </select>
            </div>

            <div class="field">
              <label class="lbl" for="cost-ceiling">Cost ceiling per scheduled run (USD)</label>
              <input id="cost-ceiling" class="input" type="number" step="0.5" min="0" [(ngModel)]="ceiling" name="ceil" />
            </div>

            <!-- P02d review: disable Save when no dirty change so the
                 button reflects whether there is anything to commit. -->
            <button type="button" class="btn primary save-btn"
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

        <!-- C: per-agent defaults -->
        <section class="card col-span-2">
          <div class="card-hd"><h2 class="title">Per-agent defaults</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              "Current default" = what the active preset (<b>{{ preset }}</b>) resolves to. Pick an explicit model to override it for this agent.
              Selects are scoped to the preset's curated menu; toggle "Show all models" to reach the full catalog.
            </p>
            <p class="text-[11.5px] text-text-3 m-0 italic" data-test="per-agent-autosave-note">
              Per-agent selections save automatically. Preset and cost ceiling save via the button above.
            </p>
            <label class="show-all-row">
              <input type="checkbox" id="per-agent-show-all"
                     [checked]="showAllPerAgent()"
                     (change)="showAllPerAgent.set($any($event.target).checked)"
                     data-test="per-agent-show-all" />
              <span class="text-[11.5px] text-text-3">Show all models</span>
            </label>
            @for (g of groupedAgents(); track g.group) {
              <div>
                <div class="eyebrow border-b border-solid border-border pb-1.5 mb-2">
                  {{ groupLabel(g.group) }} ({{ g.agents.length }})
                </div>
                <div class="agent-grid">
                  @for (a of g.agents; track a) {
                    <div class="agent-row">
                      <div>{{ display(a) }}</div>
                      <div class="mono text-[11.5px]"
                        [style.color]="agentDefault(a) ? 'var(--text-3)' : 'var(--text-2)'">
                        {{ resolvedDefault(a) }}
                      </div>
                      <select class="input sans agent-select"
                        [ngModel]="agentDefault(a)"
                        (ngModelChange)="setAgentDefault(a, $event)"
                        [attr.aria-label]="'Model for ' + a">
                        <option value="">— use preset default —</option>
                        @for (m of visiblePerAgentModels(a); track m.id) {
                          <option [value]="m.id" [disabled]="!m.available">
                            {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
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

        <!-- D2 (P3.1): Portfolio settings -->
        <section class="card col-span-2" data-test="portfolio-settings-card">
          <div class="card-hd"><h2 class="title">Portfolio</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              Controls how the Manual Book's positions are marked to market on
              <a routerLink="/portfolio" class="text-[var(--acc-info-fg)] underline">/portfolio</a>.
              Intraday cadences require a premium FMP plan.
            </p>

            <div role="radiogroup" aria-label="Mark cadence"
                 class="flex flex-col gap-2.5">
              @for (opt of cadenceOptions; track opt.value) {
                <label class="cadence-row" [class.selected]="markCadence === opt.value">
                  <input type="radio" name="mark_cadence"
                         [value]="opt.value" [(ngModel)]="markCadence"
                         [attr.data-test]="'cadence-' + opt.value" />
                  <div>
                    <div class="font-medium text-xs">{{ opt.label }}</div>
                    <div class="text-[11.5px] text-text-3">{{ opt.help }}</div>
                  </div>
                </label>
              }
            </div>

            @if (markCadence === 'delayed') {
              <div class="field max-w-[280px]">
                <label class="lbl" for="cadence-interval-input">Auto-refresh interval (minutes)</label>
                <input id="cadence-interval-input" class="input mono" type="number"
                       min="5" max="1440" step="1"
                       [(ngModel)]="intervalMinutes" name="interval_minutes"
                       aria-describedby="cadence-interval-note"
                       data-test="cadence-interval" />
                <p id="cadence-interval-note" class="text-[11.5px] text-text-3 m-0 mt-1">
                  Minimum 5 minutes, maximum 1440 (24 hours). Polling stops while the tab is hidden.
                </p>
              </div>
            }

            <button type="button" class="btn primary save-btn save-btn--portfolio"
                    (click)="savePortfolioPrefs()"
                    [disabled]="savingPortfolio() || !isPortfolioDirty()"
                    data-test="save-portfolio-prefs">
              {{ savingPortfolio()
                  ? 'Saving…'
                  : (isPortfolioDirty() ? 'Save portfolio settings' : 'No changes') }}
            </button>
            @if (portfolioMsg()) {
              <p role="status" aria-live="polite" class="text-[11.5px] text-[var(--acc-long-fg)] m-0" data-test="portfolio-msg">{{ portfolioMsg() }}</p>
            }
          </div>
        </section>

        <!-- D3 (P3-prereq-4): News settings -->
        <section class="card col-span-2" data-test="news-settings-card">
          <div class="card-hd"><h2 class="title">News</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              Controls the
              <a routerLink="/news" class="text-[var(--acc-info-fg)] underline">/news</a>
              tab and the always-visible Chyron banner. The
              <a routerLink="/news" class="text-[var(--acc-info-fg)] underline">News</a>
              page itself paginates 12 stories at a time, up to 48 total.
            </p>

            <div class="field max-w-[380px]">
              <label class="lbl flex items-center justify-between" for="news-sentiment-enabled">
                <span>Sentiment analysis</span>
                <input id="news-sentiment-enabled" type="checkbox"
                       [(ngModel)]="newsForm.sentiment_enabled"
                       name="news_sentiment_enabled"
                       data-test="news-sentiment-toggle" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                One frugal LLM pass labels each headline bullish / bearish /
                neutral. Off = no LLM cost; tiles render with no sentiment colour.
              </p>
            </div>

            <div class="field max-w-[380px]">
              <label class="lbl" for="news-sentiment-model">Sentiment model</label>
              <select id="news-sentiment-model" class="input sans"
                      [(ngModel)]="newsForm.sentiment_model"
                      name="news_sentiment_model"
                      [disabled]="!newsForm.sentiment_enabled"
                      data-test="news-sentiment-model">
                @for (m of newsStore.sentimentChoices(); track m.id) {
                  <option [value]="m.id">
                    {{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok
                  </option>
                }
              </select>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Restricted to the Llama and Qwen families for cost discipline.
              </p>
            </div>

            <div class="field max-w-[380px]">
              <label class="lbl flex items-center justify-between" for="news-chyron-enabled">
                <span>Chyron banner</span>
                <input id="news-chyron-enabled" type="checkbox"
                       [(ngModel)]="newsForm.chyron_enabled"
                       name="news_chyron_enabled"
                       data-test="news-chyron-toggle" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Continuous-scroll TV-style headline ticker, shown as a header
                line across every page when enabled.
              </p>
            </div>

            <div class="field max-w-[380px]" *ngIf="newsForm.chyron_enabled">
              <label class="lbl" for="news-chyron-count">Headlines in the chyron (5–10)</label>
              <input id="news-chyron-count" class="input mono" type="number"
                     min="5" max="10" step="1"
                     [(ngModel)]="newsForm.chyron_item_count"
                     name="news_chyron_count"
                     data-test="news-chyron-count" />
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                The chyron appears as a header line across every page when enabled.
              </p>
            </div>

            <button type="button" class="btn primary save-btn save-btn--portfolio"
                    (click)="saveNewsPrefs()"
                    [disabled]="savingNews() || !isNewsDirty()"
                    data-test="save-news-prefs">
              {{ savingNews()
                  ? 'Saving…'
                  : (isNewsDirty() ? 'Save News settings' : 'No changes') }}
            </button>
            @if (newsMsg()) {
              <p role="status" aria-live="polite"
                 class="text-[11.5px] text-[var(--acc-long-fg)] m-0"
                 data-test="news-prefs-msg">{{ newsMsg() }}</p>
            }
          </div>
        </section>

        <!-- D4 (P3-D): Persona Evolution -->
        <section class="card col-span-2" data-test="persona-evolution-card">
          <div class="card-hd flex items-center justify-between">
            <h2 class="title">Persona Evolution</h2>
            @if (evoSettings()?.cost_cap_reached_at) {
              <span class="pill" style="background: var(--acc-short-soft); color: var(--acc-short-fg);" data-test="evo-cap-reached">
                <span class="dot"></span>monthly cost cap reached
              </span>
            }
          </div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              Periodically refresh a short, dated note on what each living-investor
              persona has done and said in the real world, and inject it into the
              persona's LLM call at run time. Backtests are never touched, and the
              core persona prompt is never modified.
              <a routerLink="/runs" class="text-[var(--acc-info-fg)] underline">Run detail</a>
              shows an "Evolved" badge when a note was applied.
            </p>

            <div class="field max-w-[380px]">
              <label class="lbl flex items-center justify-between" for="evo-enabled">
                <span>Enable persona evolution</span>
                <input id="evo-enabled" type="checkbox"
                       [(ngModel)]="evoForm.enabled" name="evo_enabled"
                       data-test="evo-enabled" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Default off. Six personas evolve (Buffett, Wood, Druckenmiller,
                Burry, Damodaran, Lynch); Graham and Munger stay canon.
              </p>
            </div>

            <div class="field max-w-[380px]">
              <label class="lbl" for="evo-cadence">Cadence</label>
              <select id="evo-cadence" class="input sans"
                      [(ngModel)]="evoForm.cadence" name="evo_cadence"
                      [disabled]="!evoForm.enabled"
                      data-test="evo-cadence">
                <option value="off">Off</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>

            <div class="field max-w-[380px]">
              <label class="lbl" for="evo-model">Evolution model</label>
              <select id="evo-model" class="input sans"
                      [(ngModel)]="evoForm.model_id" name="evo_model"
                      [disabled]="!evoForm.enabled"
                      data-test="evo-model">
                <option value="">— Llama 3.3 70B (recommended) —</option>
                @for (m of evoModelChoices(); track m.id) {
                  <option [value]="m.id" [disabled]="!m.available">
                    {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                  </option>
                }
              </select>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Anthropic models are excluded in dev. Frugal OpenRouter models only.
              </p>
            </div>

            <div class="field max-w-[380px]">
              <label class="lbl flex items-center justify-between" for="evo-web">
                <span>Web search (OpenRouter :online)</span>
                <input id="evo-web" type="checkbox"
                       [(ngModel)]="evoForm.web_search_enabled" name="evo_web"
                       [disabled]="!evoForm.enabled"
                       data-test="evo-web" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                When off, only the FMP/Tiingo news filter feeds the merge call.
              </p>
            </div>

            <div class="field max-w-[380px]">
              <label class="lbl" for="evo-cap">Monthly cost cap (USD)</label>
              <input id="evo-cap" class="input mono" type="number" step="0.5" min="0"
                     [(ngModel)]="evoForm.monthly_cost_cap_usd" name="evo_cap"
                     [disabled]="!evoForm.enabled"
                     data-test="evo-cap" />
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Hard kill-switch. When reached, the task skips all cycles until next month.
                Month-to-date: {{ evoMtdCostLabel() }}.
              </p>
            </div>

            <div class="flex gap-2">
              <button type="button" class="btn primary save-btn save-btn--portfolio"
                      (click)="saveEvoSettings()"
                      [disabled]="savingEvo() || !isEvoDirty()"
                      data-test="save-evo">
                {{ savingEvo() ? 'Saving…' : (isEvoDirty() ? 'Save Persona Evolution' : 'No changes') }}
              </button>
              <button type="button" class="btn"
                      (click)="runEvoNow()"
                      [disabled]="evoStore.running() || !evoSettings()?.enabled"
                      data-test="evo-run-now">
                {{ evoStore.running() ? 'Running…' : 'Run evolution now' }}
              </button>
            </div>
            @if (evoStore.running() && evoStore.currentPersonaLabel(); as label) {
              <p role="status" aria-live="polite"
                 class="text-[11.5px] text-text-2 m-0"
                 data-test="evo-current-persona">
                Analysing: <b>{{ label }}</b>
              </p>
            }
            @if (evoMsg()) {
              <p role="status" aria-live="polite"
                 class="text-[11.5px] text-[var(--acc-long-fg)] m-0"
                 data-test="evo-msg">{{ evoMsg() }}</p>
            }
            @if (evoStore.runMsg()) {
              <p role="status" aria-live="polite"
                 class="text-[11.5px] text-text-2 m-0"
                 data-test="evo-run-msg">{{ evoStore.runMsg() }}</p>
            }

            <div class="eyebrow border-b border-solid border-border pb-1.5 mt-1.5">
              Personas
            </div>
            <table class="tbl" data-test="evo-persona-table">
              <thead><tr>
                <th>Persona</th><th>Status</th><th>Last cycle</th>
                <th>Latest revision</th><th></th>
              </tr></thead>
              <tbody>
                @for (p of evoStore.profiles(); track p.persona_name) {
                  <tr [attr.data-test-evo-row]="p.persona_name"
                      [class.evo-frozen]="!p.is_evolvable">
                    <td>
                      <div>{{ p.display_name }}</div>
                      @if (p.firm_name) {
                        <div class="text-[11px] text-text-3">{{ p.firm_name }}</div>
                      }
                      @if (!p.is_evolvable) {
                        <div class="text-[11px] text-text-3 italic">
                          Frozen — philosophy is canon{{ p.lifecycle_note ? ' (' + p.lifecycle_note + ')' : '' }}
                        </div>
                      }
                    </td>
                    <td>
                      @if (p.current_cycle_started_at) {
                        <span class="pill"
                              style="background: var(--acc-info-soft); color: var(--acc-info-fg);"
                              [attr.data-test-status]="p.persona_name">
                          <span class="dot"></span>running…
                        </span>
                      } @else {
                        <span class="pill"
                              [class.ok]="p.last_cycle_status === 'ok'"
                              [attr.data-test-status]="p.persona_name">
                          <span class="dot"></span>{{ p.last_cycle_status }}
                        </span>
                      }
                    </td>
                    <td class="text-[11.5px] text-text-3">
                      {{ p.last_cycle_at ? relTime(p.last_cycle_at) : '—' }}
                    </td>
                    <td class="text-[11.5px]">
                      @if (p.current_revision; as rev) {
                        seq {{ rev.seq }} · {{ rev.as_of_date }} ·
                        {{ rev.char_count }} chars{{ rev.over_budget ? ' (over budget)' : '' }}
                      } @else {
                        <span class="text-text-3">no revisions</span>
                      }
                    </td>
                    <td>
                      @if (p.is_evolvable) {
                        <button type="button" class="btn btn-xs"
                                (click)="toggleViewRevisions(p.persona_name)"
                                [attr.data-test]="'evo-view-' + p.persona_name">
                          {{ revisionsOpenFor() === p.persona_name ? 'Hide' : 'View' }}
                        </button>
                      }
                    </td>
                  </tr>
                  @if (revisionsOpenFor() === p.persona_name && evoStore.revisions(); as rv) {
                    <tr>
                      <td colspan="5">
                        <div class="rev-block" [attr.data-test]="'evo-revisions-' + p.persona_name">
                          @if (rv.items.length === 0) {
                            <p class="text-[11.5px] text-text-3 m-0">No revisions yet.</p>
                          }
                          @for (r of rv.items; track r.id) {
                            <details class="rev-item">
                              <summary>
                                <b>seq {{ r.seq }}</b> · {{ r.as_of_date }} ·
                                {{ r.char_count }} chars
                                @if (r.over_budget) {
                                  <span class="badge-free" style="background: var(--acc-short-soft); color: var(--acc-short-fg);">truncated</span>
                                }
                                @if (!r.material_change) {
                                  <span class="badge-free">no-change</span>
                                }
                              </summary>
                              <div class="rev-body">
                                <pre class="rev-md">{{ r.composite_markdown }}</pre>
                                @if (r.source_urls?.length) {
                                  <div class="text-[11px] text-text-3 mt-1">
                                    Sources:
                                    @for (u of r.source_urls; track u) {
                                      <a [href]="u" target="_blank" rel="noopener noreferrer"
                                         class="text-[var(--acc-info-fg)] underline mr-2">{{ u }}</a>
                                    }
                                  </div>
                                }
                                @if (r.dropped_facts?.length) {
                                  <div class="text-[11px] text-text-3 mt-1">
                                    Dropped: {{ r.dropped_facts.join('; ') }}
                                  </div>
                                }
                              </div>
                            </details>
                          }
                        </div>
                      </td>
                    </tr>
                  }
                }
              </tbody>
            </table>
          </div>
        </section>

        <!-- D: Available models -->
        <section class="card col-span-2">
          <div class="card-hd flex items-center justify-between">
            <h2 class="title">Available models ({{ store.models().length }})</h2>
            <div class="flex gap-2">
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
          <table class="tbl">
            <thead><tr>
              <th>Model</th><th>Provider</th><th>Tier</th>
              <th class="right">$/Mtok in</th><th class="right">$/Mtok out</th>
              <th>Status</th><th>Verified</th><th></th>
            </tr></thead>
            <tbody>
              @for (m of store.models(); track m.id) {
                <tr [attr.data-test-row]="m.id">
                  <td class="mono text-text">
                    {{ m.display_name }}
                    @if (isFree(m)) {
                      <span class="badge-free" data-test="badge-free">FREE</span>
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
                      <span class="text-[var(--acc-short-fg)]"
                        [title]="m.last_verified_note">
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
            </tbody>
          </table>
        </section>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      /* P4 WS-DA-2: de-emphasize frozen persona rows via a subtle background
         tint instead of reduced opacity — so text never drops below 4.5:1.
         The "Frozen — philosophy is canon" label carries the state. */
      tr.evo-frozen { background: var(--surface-2); }
      .cadence-row {
        display: grid;
        grid-template-columns: 24px 1fr;
        gap: 8px;
        padding: 10px 12px;
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        cursor: pointer;
        background: var(--surface);
        transition: border-color 80ms;
      }
      .cadence-row:hover { border-color: var(--text-3); }
      .cadence-row.selected { border-color: var(--acc-info); background: var(--acc-info-soft, var(--surface)); }
      .cadence-row input[type="radio"] { margin-top: 2px; }
      .save-btn { height: 32px; justify-content: center; }
      .save-btn--portfolio { align-self: flex-start; min-width: 200px; }
      .agent-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px 24px;
      }
      .show-all-row {
        display: flex;
        align-items: center;
        gap: 6px;
        padding-bottom: 4px;
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
      .btn-xs {
        height: 22px;
        padding: 0 8px;
        font-size: 11px;
      }
      .rev-block {
        padding: 8px 4px 8px 4px;
        background: var(--surface);
        border-top: 1px solid var(--border);
      }
      .rev-item {
        padding: 4px 0;
        border-bottom: 1px dashed var(--border);
      }
      .rev-item summary {
        cursor: pointer;
        font-size: 12px;
      }
      .rev-body {
        padding: 6px 0 6px 12px;
      }
      .rev-md {
        white-space: pre-wrap;
        font-family: var(--font-mono, monospace);
        font-size: 11.5px;
        background: var(--bg);
        border: 1px solid var(--border);
        border-radius: var(--r-6, 6px);
        padding: 8px;
        margin: 0;
      }
    `,
  ],
})
export class SettingsModelsPage implements OnInit {
  readonly store = inject(ModelsStore);
  readonly portfolio = inject(PortfolioStore);
  readonly newsStore = inject(NewsStore);
  readonly evoStore = inject(PersonaEvolutionStore);

  // Persona Evolution form state.
  evoForm: EvolutionSettings = {
    enabled: false,
    cadence: 'off',
    model_id: '',
    web_search_enabled: true,
    monthly_cost_cap_usd: '2.00',
    cost_cap_reached_at: null,
    updated_at: '',
  };
  private savedEvo: EvolutionSettings = { ...this.evoForm };
  savingEvo = signal(false);
  evoMsg = signal<string | null>(null);
  revisionsOpenFor = signal<string | null>(null);

  evoSettings = computed(() => this.evoStore.settings());
  evoMtdCostLabel = computed(
    () => `$${this.evoSettings()?.month_to_date_cost_usd ?? '0.00'}`,
  );

  evoModelChoices = computed(() =>
    this.store
      .models()
      .filter((m) => m.provider !== 'anthropic')
      .filter((m) => m.tier === 'fast_cheap' || m.tier === 'hosted_open'),
  );

  isEvoDirty(): boolean {
    return JSON.stringify(this.evoForm) !== JSON.stringify(this.savedEvo);
  }

  saveEvoSettings(): void {
    this.savingEvo.set(true);
    this.evoMsg.set(null);
    const body = { ...this.evoForm };
    this.evoStore.patchSettings(body).subscribe({
      next: (s) => {
        this.evoForm = { ...s };
        this.savedEvo = { ...s };
        this.savingEvo.set(false);
        this.evoMsg.set('Saved.');
        setTimeout(() => this.evoMsg.set(null), 2500);
      },
      error: (err) => {
        this.savingEvo.set(false);
        this.evoMsg.set(err?.error?.detail || 'Failed to save.');
      },
    });
  }

  runEvoNow(): void {
    this.evoStore.runNow().subscribe();
  }

  toggleViewRevisions(persona: string): void {
    if (this.revisionsOpenFor() === persona) {
      this.revisionsOpenFor.set(null);
      this.evoStore.clearRevisions();
      return;
    }
    this.revisionsOpenFor.set(persona);
    this.evoStore.loadRevisions(persona).subscribe();
  }

  // News settings form state.
  newsForm: NewsPreferences = {
    sentiment_enabled: true,
    sentiment_model: 'openrouter:qwen/qwen3.6-27b',
    chyron_enabled: true,
    chyron_item_count: 8,
    feed_item_count: 20,
  };
  private savedNews: NewsPreferences = { ...this.newsForm };
  savingNews = signal(false);
  newsMsg = signal<string | null>(null);

  isNewsDirty(): boolean {
    return JSON.stringify(this.newsForm) !== JSON.stringify(this.savedNews);
  }

  saveNewsPrefs(): void {
    this.savingNews.set(true);
    this.newsMsg.set(null);
    const patch = { ...this.newsForm };
    // Clamp client-side so UX matches the backend validators.
    patch.chyron_item_count = Math.min(10, Math.max(5, patch.chyron_item_count));
    // feed_item_count is legacy/unused now that the feed paginates; keep it
    // on the model but never expose to the user.
    delete (patch as Partial<typeof patch>).feed_item_count;
    this.newsStore.savePreferences(patch).subscribe({
      next: (r) => {
        this.newsForm = { ...r.preferences };
        this.savedNews = { ...r.preferences };
        this.savingNews.set(false);
        this.newsMsg.set('Saved.');
        setTimeout(() => this.newsMsg.set(null), 2500);
      },
      error: (err) => {
        this.savingNews.set(false);
        this.newsMsg.set(err?.error?.detail || 'Failed to save News settings.');
      },
    });
  }

  readonly providers = [
    { field: 'anthropic', label: 'Anthropic API key' },
    { field: 'openrouter', label: 'OpenRouter API key' },
    { field: 'openai', label: 'OpenAI API key' },
  ] as const;
  readonly dataProviders = [
    {
      field: 'fmp',
      label: 'FMP API key',
      note: 'Required for backtests and live agent runs. Sign up at financialmodelingprep.com.',
    },
    {
      field: 'tiingo',
      label: 'Tiingo API key',
      note: 'Required for news features. Free tier available at tiingo.com.',
    },
    {
      field: 'fred',
      label: 'FRED API key',
      note: 'Optional — defaults to a shared platform key. Set your own for isolation or higher rate limits.',
    },
  ] as const;
  readonly presets = PRESET_NAMES;

  keyEdits: Record<string, string> = {
    anthropic: '', openrouter: '', openai: '',
    fmp: '', tiingo: '', fred: '',
  };
  ollamaHost = '';
  preset = 'research';
  ceiling: number | null = 5;
  globalDefault = 'openrouter:meta-llama/llama-3.3-70b-instruct';

  savingKeys = signal(false);
  savingPrefs = signal(false);
  keysMsg = signal<string | null>(null);
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

  isPrefsDirty(): boolean {
    return this.preset !== this.savedPreset || this.ceiling !== this.savedCeiling;
  }

  ngOnInit(): void {
    this.portfolio.loadPreferences().subscribe({
      next: (p) => {
        this.markCadence = p.mark_cadence;
        this.intervalMinutes = p.interval_minutes;
        this.savedCadence = p.mark_cadence;
        this.savedIntervalMinutes = p.interval_minutes;
      },
      error: () => { /* ignore — defaults are fine */ },
    });
    this.newsStore.loadPreferences().subscribe({
      next: (r) => {
        this.newsForm = { ...r.preferences };
        this.savedNews = { ...r.preferences };
      },
      error: () => { /* ignore — defaults are fine */ },
    });
    this.store.loadAll().subscribe(() => {
      const keys = this.store.keys();
      if (keys) this.ollamaHost = keys.ollama_host ?? '';
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
      this.loadPresetOverrides();
    });
    this.evoStore.loadSettings().subscribe({
      next: (s) => {
        this.evoForm = { ...s };
        this.savedEvo = { ...s };
      },
      error: () => { /* ignore — defaults are fine */ },
    });
    this.evoStore.loadProfiles().subscribe({
      next: () => {
        // If a cycle was already running before the page loaded, kick the
        // polling loop so the badge advances without the user clicking again.
        if (this.evoStore.runningPersonas().length > 0) {
          this.evoStore.startPolling();
        }
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
    const all = this.store.models();
    if (this.showAllPerAgent()) return all;
    const menu = this.presetMenu();
    if (!menu.length) return all;
    const menuSet = new Set(menu);
    const out = all.filter(
      (m) => menuSet.has(m.id) || m.provider === 'ollama',
    );
    const saved = this.agentDefault(a);
    if (saved && !out.some((m) => m.id === saved)) {
      const stale = all.find((m) => m.id === saved);
      if (stale) out.push(stale);
    }
    return out;
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
    if (override) {
      const m = this.store.models().find((x) => x.id === override);
      return (m?.display_name ?? override) + ' (override)';
    }
    const fromPreset = this.presetOverrides()[a];
    if (fromPreset) {
      const m = this.store.models().find((x) => x.id === fromPreset);
      return m?.display_name ?? fromPreset;
    }
    const sysDefault = this.store.agents().find((x) => x.id === a)?.default_model;
    if (sysDefault) {
      const m = this.store.models().find((x) => x.id === sysDefault);
      return (m?.display_name ?? sysDefault) + ' (system)';
    }
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
  statusOf(field: string): string {
    return (this.store.keys() as Record<string, string> | null)?.[field] ?? 'unset';
  }
  saveKeys(): void {
    this.savingKeys.set(true);
    const body: Record<string, string> = { ollama_host: this.ollamaHost };
    const allFields = [
      'anthropic', 'openrouter', 'openai',
      'fmp', 'tiingo', 'fred',
    ];
    for (const f of allFields) {
      if (this.keyEdits[f]) body[`${f}_api_key`] = this.keyEdits[f];
    }
    this.store.saveKeys(body).subscribe({
      next: () => {
        this.savingKeys.set(false);
        this.keysMsg.set('Saved. Reloading models…');
        this.keyEdits = {
          anthropic: '', openrouter: '', openai: '',
          fmp: '', tiingo: '', fred: '',
        };
        this.store.loadModels().subscribe();
      },
      error: () => { this.savingKeys.set(false); this.keysMsg.set('Failed to save'); },
    });
  }
  savePrefs(): void {
    this.savingPrefs.set(true);
    this.store.savePrefs({
      preset: this.preset,
      cost_ceiling_per_run_usd: this.ceiling,
    }).subscribe({
      next: () => {
        this.savingPrefs.set(false);
        this.prefsMsg.set('Saved.');
        this.savedPreset = this.preset;
        this.savedCeiling = this.ceiling;
      },
      error: () => { this.savingPrefs.set(false); this.prefsMsg.set('Failed to save'); },
    });
  }

  // ---- P3.1: Portfolio settings (mark cadence + interval) -----------
  readonly cadenceOptions: { value: MarkCadence; label: string; help: string }[] = [
    {
      value: 'daily',
      label: 'Daily',
      help: 'Mark from the latest daily close. Works with the FMP free tier. No auto-polling.',
    },
    {
      value: 'delayed',
      label: 'Delayed (auto-refresh)',
      help: 'Use FMP intraday quotes and refresh on an interval you set (minimum 5 minutes). Requires the FMP premium plan.',
    },
    {
      value: 'manual',
      label: 'Pull only (manual refresh)',
      help: 'Use FMP intraday quotes but never auto-poll — use the "Refresh marks" button on the Portfolio tab.',
    },
  ];

  markCadence: MarkCadence = 'daily';
  intervalMinutes = 20;
  savingPortfolio = signal(false);
  portfolioMsg = signal<string | null>(null);
  private savedCadence: MarkCadence = 'daily';
  private savedIntervalMinutes = 20;

  isPortfolioDirty(): boolean {
    if (this.markCadence !== this.savedCadence) return true;
    if (this.markCadence === 'delayed' && this.intervalMinutes !== this.savedIntervalMinutes) {
      return true;
    }
    return false;
  }

  savePortfolioPrefs(): void {
    if (!this.isPortfolioDirty()) return;
    this.savingPortfolio.set(true);
    const interval = Math.min(1440, Math.max(5, Math.floor(this.intervalMinutes || 20)));
    this.portfolio.savePreferences({
      mark_cadence: this.markCadence,
      interval_minutes: interval,
    }).subscribe({
      next: (r) => {
        this.savingPortfolio.set(false);
        this.portfolioMsg.set('Saved.');
        this.savedCadence = r.mark_cadence;
        this.savedIntervalMinutes = r.interval_minutes;
        this.intervalMinutes = r.interval_minutes;
      },
      error: (err) => {
        this.savingPortfolio.set(false);
        this.portfolioMsg.set(err?.error?.detail || 'Failed to save');
      },
    });
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
