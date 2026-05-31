import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';
import { NewsStore } from '../../abstraction/news.store';
import { PortfolioStore } from '../../abstraction/portfolio.store';
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

  // ---- News settings --------------------------------------------------
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
