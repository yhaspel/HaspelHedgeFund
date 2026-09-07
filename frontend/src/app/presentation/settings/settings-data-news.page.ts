import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';
import { NewsStore } from '../../abstraction/news.store';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import { OfflineState } from '../../core/offline/offline-state.service';
import { NewsPreferences } from '../../core/models/news.model';
import { ProvenancePanelComponent } from '../shared/provenance-panel.component';
import { MarkCadence } from '../../core/models/portfolio.model';

/**
 * Settings › Data & News — news feed preferences + portfolio mark cadence.
 *
 * Split out of the former monolithic settings-models page (sections "D3"
 * and "D2"). Per-section dirty tracking, clamping and save handlers are
 * preserved exactly, along with every data-test hook.
 */
@Component({
  selector: 'hf-settings-data-news',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    AppShellComponent,
    SettingsTabsComponent,
    ProvenancePanelComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Settings', link: '/settings/models' }, { label: 'Data & News' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">Data &amp; News</h1>
        </div>
      </div>

      <hf-settings-tabs />

      <div role="tabpanel" aria-label="Data and News settings"
           class="grid grid-cols-2 gap-[18px] max-w-[1100px] items-start">
        <!-- News -->
        <section class="card" data-test="news-settings-card">
          <div class="card-hd"><h2 class="title">News</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              Controls the
              <a routerLink="/news" class="text-[var(--acc-info-fg)] underline">/news</a>
              tab and the always-visible Chyron banner. The
              <a routerLink="/news" class="text-[var(--acc-info-fg)] underline">News</a>
              page itself paginates 12 stories at a time, up to 48 total.
            </p>

            <!-- WAVE 3: the news LLM features are BYOK-gated with a daily USD
                 cap. Without this block a user whose sentiment silently stopped
                 running has no way to find out why. -->
            @if (llm(); as st) {
              <div class="llm-status" data-test="news-llm-status"
                   [class.blocked]="!st.allowed">
                <div class="llm-row">
                  <span class="llm-k">AI features</span>
                  <span class="pill" [class.ok]="st.allowed" [class.err]="!st.allowed"
                        data-test="news-llm-allowed">
                    <span class="dot"></span>{{ st.allowed ? 'running' : 'skipped' }}
                  </span>
                </div>
                <div class="llm-row">
                  <span class="llm-k">Your OpenRouter key</span>
                  <span class="pill" [class.ok]="st.has_user_key" [class.warn]="!st.has_user_key"
                        data-test="news-llm-key">
                    <span class="dot"></span>{{ st.has_user_key ? 'present' : 'not set' }}
                  </span>
                  @if (!st.has_user_key) {
                    <a routerLink="/settings/providers"
                       class="text-[var(--acc-info-fg)] underline text-[11.5px]"
                       data-test="news-llm-add-key">Add one</a>
                  }
                </div>
                <div class="llm-row">
                  <span class="llm-k">Today's spend</span>
                  <span class="mono text-[11.5px]" data-test="news-llm-spend">
                    \${{ st.spent_today_usd | number: '1.2-2' }} /
                    \${{ st.daily_cap_usd | number: '1.2-2' }} cap
                  </span>
                </div>
                @if (st.byok_required) {
                  <p class="llm-note" data-test="news-llm-byok">
                    This deployment does not lend its own key for News AI — sentiment and
                    translation need your own OpenRouter key.
                  </p>
                }
                @if (st.reason) {
                  <p class="llm-note warn" role="status" data-test="news-llm-reason">{{ st.reason }}</p>
                }
                @if (st.model_choices_restricted) {
                  <p class="llm-note" data-test="news-llm-restricted">
                    Without your own key the pickers below are limited to the frugal preset
                    models; the rest are disabled.
                  </p>
                }
              </div>
            }

            <div class="field">
              <label class="lbl flex items-center justify-between" for="news-show-all-models">
                <span>Show all models</span>
                <input id="news-show-all-models" type="checkbox"
                       [ngModel]="showAllModels()"
                       (ngModelChange)="showAllModels.set($event)"
                       name="news_show_all_models"
                       data-test="news-show-all-models" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Off = the frugal Llama/Qwen default. On = pick any catalogued
                model below, including frontier and 🧠 reasoning models (higher
                per-headline cost).
              </p>
            </div>

            <div class="field">
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

            <div class="field">
              <label class="lbl" for="news-sentiment-model">Sentiment model</label>
              <select id="news-sentiment-model" class="input sans"
                      [(ngModel)]="newsForm.sentiment_model"
                      name="news_sentiment_model"
                      [disabled]="!newsForm.sentiment_enabled"
                      data-test="news-sentiment-model">
                @for (m of sentimentOptions(); track m.id) {
                  <option [value]="m.id" [disabled]="m.selectable === false"
                          [attr.data-test]="'news-sentiment-option-' + m.id">
                    {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok{{
                      m.selectable === false ? ' · needs your own key' : ''
                    }}
                  </option>
                }
              </select>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                {{ showAllModels()
                    ? 'Full catalogue — 🧠 marks reasoning models.'
                    : 'Frugal Llama/Qwen models for cost discipline.' }}
                @if (lockedCount() > 0) {
                  <span data-test="news-locked-count">
                    {{ lockedCount() }} model(s) are disabled — they need your own OpenRouter key.
                  </span>
                }
              </p>
            </div>

            <div class="field">
              <label class="lbl flex items-center justify-between" for="news-translation-enabled">
                <span>Translation</span>
                <input id="news-translation-enabled" type="checkbox"
                       [(ngModel)]="newsForm.translation_enabled"
                       name="news_translation_enabled"
                       data-test="news-translation-toggle" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Auto-translate non-English headlines to English and tag them.
                Off = foreign articles show in their original language.
              </p>
            </div>

            <div class="field">
              <label class="lbl" for="news-translation-model">Translation model</label>
              <select id="news-translation-model" class="input sans"
                      [(ngModel)]="newsForm.translation_model"
                      name="news_translation_model"
                      [disabled]="!newsForm.translation_enabled"
                      data-test="news-translation-model">
                @for (m of translationOptions(); track m.id) {
                  <option [value]="m.id" [disabled]="m.selectable === false"
                          [attr.data-test]="'news-translation-option-' + m.id">
                    {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok{{
                      m.selectable === false ? ' · needs your own key' : ''
                    }}
                  </option>
                }
              </select>
            </div>

            <div class="field">
              <label class="lbl" for="news-translation-fallback">Fallback model</label>
              <select id="news-translation-fallback" class="input sans"
                      [(ngModel)]="newsForm.translation_fallback_model"
                      name="news_translation_fallback"
                      [disabled]="!newsForm.translation_enabled"
                      data-test="news-translation-fallback">
                @for (m of translationOptions(); track m.id) {
                  <option [value]="m.id" [disabled]="m.selectable === false"
                          [attr.data-test]="'news-translation-option-' + m.id">
                    {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok{{
                      m.selectable === false ? ' · needs your own key' : ''
                    }}
                  </option>
                }
              </select>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Used if the primary model fails. If this also fails, the
                article is left untranslated.
              </p>
            </div>

            <div class="field">
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

            @if (newsForm.chyron_enabled) {
              <div class="field">
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
            }

            <button type="button" class="btn primary save-btn save-btn--portfolio"
                    (click)="saveNewsPrefs()"
                    [disabled]="savingNews() || !isNewsDirty()"
                    data-test="save-news-prefs">
              {{ savingNews()
                  ? 'Saving…'
                  : (isNewsDirty() ? 'Save News settings' : 'No changes') }}
            </button>
            @if (newsSaveError(); as e) {
              <p role="alert"
                 class="text-[11.5px] text-[var(--acc-short-fg)] m-0"
                 data-test="news-prefs-error">{{ e }}</p>
            } @else if (newsMsg()) {
              <p role="status" aria-live="polite"
                 class="text-[11.5px] text-[var(--acc-long-fg)] m-0"
                 data-test="news-prefs-msg">{{ newsMsg() }}</p>
            }
          </div>
        </section>

        <!-- Portfolio mark cadence -->
        <section class="card" data-test="portfolio-settings-card">
          <div class="card-hd"><h2 class="title">Portfolio · mark cadence</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0">
              Controls how the Manual Book's positions are marked to market on
              <a routerLink="/portfolio" class="text-[var(--acc-info-fg)] underline">/portfolio</a>.
              Intraday cadences require a premium FMP plan.
            </p>

            <div role="radiogroup" aria-label="Mark cadence" class="flex flex-col gap-2.5">
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

        <!-- WAVE 3: the global data-provenance block — macro series vintages and
             every provider's key + last-success age. This is the operator view
             that makes a silently-dead provider visible. -->
        <hf-provenance
          class="col-span-2 block"
          heading="Data provenance · macro &amp; providers"
          [showGlobal]="true"
        ></hf-provenance>

        <!-- Offline (P4-OFF WS-4.4) -->
        <section class="card" data-test="offline-settings-card">
          <div class="card-hd"><h2 class="title">Offline</h2></div>
          <div class="card-bd flex flex-col gap-3.5">
            <div class="field">
              <label class="lbl flex items-center justify-between" for="offline-simulate">
                <span>Simulate offline (this browser only)</span>
                <input id="offline-simulate" type="checkbox"
                       [ngModel]="offline.forced()"
                       (ngModelChange)="offline.setForced($event)"
                       name="offline_simulate"
                       data-test="offline-simulate-toggle"
                       data-testid="offline-simulate-toggle" />
              </label>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                Treats every API call as failed (serving last-synced data) without
                touching the network — the read-only L2 experience. Writes are
                blocked. Turn off to reconnect. Current state:
                <strong>{{ offline.mode() }}</strong>.
              </p>
            </div>
          </div>
        </section>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .llm-status {
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 10px 12px;
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        background: var(--surface-2);
      }
      .llm-status.blocked {
        border-color: var(--acc-hold);
        background: var(--acc-hold-soft);
      }
      .llm-row {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .llm-k {
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-3);
        min-width: 130px;
      }
      .llm-note {
        margin: 0;
        font-size: var(--fs-11);
        color: var(--text-2);
      }
      .llm-note.warn {
        color: var(--acc-hold-fg);
      }
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
      @media (max-width: 900px) {
        :host ::ng-deep .grid-cols-2 { grid-template-columns: 1fr; }
      }
    `,
  ],
})
export class SettingsDataNewsPage implements OnInit {
  readonly newsStore = inject(NewsStore);
  readonly portfolio = inject(PortfolioStore);
  readonly offline = inject(OfflineState);

  // ---- News settings --------------------------------------------------
  newsForm: NewsPreferences = {
    sentiment_enabled: true,
    sentiment_model: 'openrouter:qwen/qwen3.6-27b',
    chyron_enabled: true,
    chyron_item_count: 8,
    feed_item_count: 20,
    translation_enabled: true,
    translation_model: 'openrouter:qwen/qwen3-235b-a22b-2507',
    translation_fallback_model: 'openrouter:meta-llama/llama-3.3-70b-instruct',
  };
  private savedNews: NewsPreferences = { ...this.newsForm };
  savingNews = signal(false);
  newsMsg = signal<string | null>(null);

  /** When off, the pickers show only the frugal default subset. */
  showAllModels = signal(false);

  readonly sentimentOptions = computed(() => {
    const all = this.newsStore.sentimentChoices();
    return this.showAllModels() ? all : all.filter((m) => m.frugal);
  });
  readonly translationOptions = computed(() => {
    const all = this.newsStore.translationChoices();
    return this.showAllModels() ? all : all.filter((m) => m.frugal);
  });

  /** WAVE 3 — BYOK / daily-cap state, straight from the store. */
  readonly llm = this.newsStore.llmStatus;
  /** The 400 `detail` from a rejected save (e.g. a non-selectable model). */
  readonly newsSaveError = this.newsStore.saveError;
  /** How many catalogued models this user may NOT pick. */
  readonly lockedCount = computed(
    () =>
      [...this.newsStore.sentimentChoices(), ...this.newsStore.translationChoices()].filter(
        (m) => m.selectable === false,
      ).length,
  );

  // ---- Portfolio mark cadence -----------------------------------------
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
        // If any saved pick is outside the frugal subset, reveal the full list
        // so its <select> shows the current value instead of rendering blank.
        const frugalIds = new Set(
          [...r.sentiment_model_choices, ...r.translation_model_choices]
            .filter((c) => c.frugal)
            .map((c) => c.id),
        );
        const picked = [
          r.preferences.sentiment_model,
          r.preferences.translation_model,
          r.preferences.translation_fallback_model,
        ];
        if (picked.some((id) => id && !frugalIds.has(id))) {
          this.showAllModels.set(true);
        }
      },
      error: () => { /* ignore — defaults are fine */ },
    });
  }

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
      error: () => {
        this.savingNews.set(false);
        this.newsMsg.set(null);
        // The store already flattened the 400 `detail` into `saveError`, which
        // the template renders inline with role="alert" — a non-selectable
        // model names itself in that message.
      },
    });
  }

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
}
