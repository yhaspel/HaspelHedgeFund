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
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent, SettingsTabsComponent],
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
                  <option [value]="m.id">
                    {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok
                  </option>
                }
              </select>
              <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                {{ showAllModels()
                    ? 'Full catalogue — 🧠 marks reasoning models.'
                    : 'Frugal Llama/Qwen models for cost discipline.' }}
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
                  <option [value]="m.id">
                    {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok
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
                  <option [value]="m.id">
                    {{ m.supports_reasoning ? '🧠 ' : '' }}{{ m.display_name }} ·
                    {{ m.price_in_per_mtok ?? 0 }} / {{ m.price_out_per_mtok ?? 0 }} $/Mtok
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
            @if (newsMsg()) {
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
                       data-test="offline-simulate-toggle" />
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
      error: (err) => {
        this.savingNews.set(false);
        this.newsMsg.set(err?.error?.detail || 'Failed to save News settings.');
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
