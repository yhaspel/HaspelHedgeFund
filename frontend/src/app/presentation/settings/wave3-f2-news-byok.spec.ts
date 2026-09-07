import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { NewsStore } from '../../abstraction/news.store';
import { NewsPreferencesResponse } from '../../core/models/news.model';
import { SettingsDataNewsPage } from './settings-data-news.page';

/**
 * WAVE 3 F2 item 6 — News LLM features are BYOK-gated with a daily cap.
 *
 * Without this UI a user whose sentiment quietly stopped running has no way to
 * find out why: the feed still works, the toggle still says "on", and nothing
 * on screen mentions a key or a budget. `llm_status` is that explanation, and
 * a non-`selectable` model has to be visibly unpickable rather than a save
 * that fails with a shrug.
 */

const LLM_BLOCKED = {
  byok_required: true,
  has_user_key: false,
  platform_key_allowed: false,
  allowed: false,
  reason:
    'Sentiment analysis needs your own OpenRouter key — add one at /settings/providers. The news feed still works without it.',
  daily_cap_usd: 1,
  spent_today_usd: 0.42,
  model_choices_restricted: true,
};

function prefs(patch: Partial<NewsPreferencesResponse> = {}): NewsPreferencesResponse {
  return {
    preferences: {
      sentiment_enabled: true,
      sentiment_model: 'openrouter:cheap',
      chyron_enabled: true,
      chyron_item_count: 8,
      feed_item_count: 12,
      translation_enabled: true,
      translation_model: 'openrouter:cheap',
      translation_fallback_model: 'openrouter:cheap',
    },
    sentiment_model_choices: [
      {
        id: 'openrouter:cheap',
        display_name: 'Qwen 3 (frugal)',
        price_in_per_mtok: 0.1,
        price_out_per_mtok: 0.2,
        supports_reasoning: false,
        frugal: true,
        selectable: true,
      },
      {
        id: 'openrouter:frontier',
        display_name: 'Frontier XL',
        price_in_per_mtok: 12,
        price_out_per_mtok: 40,
        supports_reasoning: true,
        frugal: false,
        selectable: false,
      },
    ],
    translation_model_choices: [
      {
        id: 'openrouter:cheap',
        display_name: 'Qwen 3 (frugal)',
        price_in_per_mtok: 0.1,
        price_out_per_mtok: 0.2,
        supports_reasoning: false,
        frugal: true,
        selectable: true,
      },
      {
        id: 'openrouter:frontier',
        display_name: 'Frontier XL',
        price_in_per_mtok: 12,
        price_out_per_mtok: 40,
        supports_reasoning: true,
        frugal: false,
        selectable: false,
      },
    ],
    llm_status: LLM_BLOCKED,
    ...patch,
  };
}

describe('wave3-f2 · Settings › Data & News — News BYOK', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SettingsDataNewsPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  function mount(body: NewsPreferencesResponse = prefs()) {
    const fixture = TestBed.createComponent(SettingsDataNewsPage);
    fixture.detectChanges();
    http.expectOne((r) => r.url.includes('/portfolio/preferences/')).flush({
      mark_cadence: 'daily',
      interval_minutes: 20,
    });
    http.expectOne((r) => r.url.includes('/news/preferences/')).flush(body);
    // The page also mounts the global provenance card.
    http.match((r) => r.url.includes('/data/provenance/')).forEach((r) =>
      r.flush({
        as_of: '2026-06-06T17:30:00Z',
        tickers: {},
        global: {
          macro: { series: [], classifier_version: null, snapshot_as_of: null },
          providers: {},
          provider_last_success: {},
          policy: {},
        },
      }),
    );
    fixture.detectChanges();
    return fixture;
  }

  it('says whether the AI features are running, and why they are not', () => {
    const el: HTMLElement = mount().nativeElement;
    const block = el.querySelector('[data-test="news-llm-status"]')!;
    expect(block.classList.contains('blocked')).toBe(true);
    expect(el.querySelector('[data-test="news-llm-allowed"]')!.textContent).toContain('skipped');
    expect(el.querySelector('[data-test="news-llm-reason"]')!.textContent).toContain(
      'needs your own OpenRouter key',
    );
    expect(el.querySelector('[data-test="news-llm-byok"]')).not.toBeNull();
  });

  it('shows whether a user key is present, with a route to add one', () => {
    const el: HTMLElement = mount().nativeElement;
    expect(el.querySelector('[data-test="news-llm-key"]')!.textContent).toContain('not set');
    expect(el.querySelector('[data-test="news-llm-add-key"]')!.getAttribute('href')).toBe(
      '/settings/providers',
    );
  });

  it('shows today’s spend against the daily cap', () => {
    const el: HTMLElement = mount().nativeElement;
    const spend = el.querySelector('[data-test="news-llm-spend"]')!.textContent!;
    expect(spend).toContain('0.42');
    expect(spend).toContain('1.00');
    expect(spend).toContain('cap');
  });

  it('disables every non-selectable model in BOTH pickers and says why', () => {
    const fixture = mount();
    fixture.componentInstance.showAllModels.set(true);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;

    const sentFrontier = el.querySelector(
      '[data-test="news-sentiment-option-openrouter:frontier"]',
    ) as HTMLOptionElement;
    expect(sentFrontier.disabled).toBe(true);
    expect(sentFrontier.textContent).toContain('needs your own key');

    const transFrontier = el.querySelector(
      '[data-test="news-translation-option-openrouter:frontier"]',
    ) as HTMLOptionElement;
    expect(transFrontier.disabled).toBe(true);

    const cheap = el.querySelector(
      '[data-test="news-sentiment-option-openrouter:cheap"]',
    ) as HTMLOptionElement;
    expect(cheap.disabled).toBe(false);

    expect(el.querySelector('[data-test="news-locked-count"]')!.textContent).toContain('2 model');
    expect(el.querySelector('[data-test="news-llm-restricted"]')).not.toBeNull();
  });

  it('surfaces the 400 `detail` verbatim when a save is rejected', () => {
    const fixture = mount();
    const el: HTMLElement = fixture.nativeElement;
    fixture.componentInstance.newsForm.chyron_item_count = 9;
    fixture.componentInstance.saveNewsPrefs();
    http.expectOne((r) => r.method === 'PUT' && r.url.includes('/news/preferences/')).flush(
      {
        detail:
          'That sentiment model is only available with your own OpenRouter key. Add one at /settings/providers, or pick a model marked “frugal” in sentiment_model_choices.',
      },
      { status: 400, statusText: 'Bad Request' },
    );
    fixture.detectChanges();
    const err = el.querySelector('[data-test="news-prefs-error"]')!;
    expect(err.getAttribute('role')).toBe('alert');
    expect(err.textContent).toContain('only available with your own OpenRouter key');
    // …and no "Saved." message alongside it.
    expect(el.querySelector('[data-test="news-prefs-msg"]')).toBeNull();
  });

  it('drops the status block entirely when the server does not send llm_status', () => {
    const el: HTMLElement = mount(prefs({ llm_status: undefined })).nativeElement;
    expect(el.querySelector('[data-test="news-llm-status"]')).toBeNull();
  });

  it('reports the features as running once a key is present', () => {
    const el: HTMLElement = mount(
      prefs({
        llm_status: {
          ...LLM_BLOCKED,
          has_user_key: true,
          allowed: true,
          reason: null,
          model_choices_restricted: false,
        },
      }),
    ).nativeElement;
    expect(el.querySelector('[data-test="news-llm-allowed"]')!.textContent).toContain('running');
    expect(el.querySelector('[data-test="news-llm-reason"]')).toBeNull();
    expect(el.querySelector('[data-test="news-llm-key"]')!.textContent).toContain('present');
  });
});

describe('wave3-f2 · NewsStore llm_status plumbing', () => {
  let store: NewsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    store = TestBed.inject(NewsStore);
    http = TestBed.inject(HttpTestingController);
  });

  it('exposes one skip reason, and null once the features are allowed', () => {
    store.loadPreferences().subscribe();
    http.expectOne((r) => r.url.includes('/news/preferences/')).flush(prefs());
    expect(store.llmStatus()?.allowed).toBe(false);
    expect(store.llmSkipReason()).toContain('needs your own OpenRouter key');

    store
      .savePreferences({ sentiment_enabled: false })
      .subscribe({ error: () => undefined });
    http
      .expectOne((r) => r.method === 'PUT')
      .flush(prefs({ llm_status: { ...LLM_BLOCKED, allowed: true, reason: null } }));
    expect(store.llmSkipReason()).toBeNull();
    expect(store.saveError()).toBeNull();
  });

  it('records the rejected-save message on `saveError` and restores the old preferences', () => {
    store.loadPreferences().subscribe();
    http.expectOne((r) => r.url.includes('/news/preferences/')).flush(prefs());
    const before = store.preferences();

    store
      .savePreferences({ sentiment_model: 'openrouter:frontier' })
      .subscribe({ error: () => undefined });
    http
      .expectOne((r) => r.method === 'PUT')
      .flush({ detail: 'only available with your own OpenRouter key' }, { status: 400, statusText: 'Bad Request' });

    expect(store.saveError()).toContain('only available with your own OpenRouter key');
    expect(store.preferences()).toEqual(before);
  });
});
