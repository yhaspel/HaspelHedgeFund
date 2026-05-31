import { TestBed } from '@angular/core/testing';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { describe, beforeEach, afterEach, it, expect } from 'vitest';

import { NewsStore } from './news.store';
import { NewsPreferencesResponse } from '../core/models/news.model';

const PREFS_RESPONSE: NewsPreferencesResponse = {
  preferences: {
    sentiment_enabled: true,
    sentiment_model: 'openrouter:qwen/qwen3.6-27b',
    chyron_enabled: true,
    chyron_item_count: 8,
    feed_item_count: 20,
    translation_enabled: true,
    translation_model: 'openrouter:qwen/qwen3-235b-a22b-2507',
    translation_fallback_model: 'openrouter:meta-llama/llama-3.3-70b-instruct',
  },
  sentiment_model_choices: [],
  translation_model_choices: [
    {
      id: 'openrouter:qwen/qwen3-235b-a22b-2507',
      display_name: 'Qwen3 235B',
      price_in_per_mtok: 0.071,
      price_out_per_mtok: 0.1,
    },
  ],
};

describe('NewsStore translation choices', () => {
  let store: NewsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(NewsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('populates translationChoices from loadPreferences', () => {
    store.loadPreferences().subscribe();
    http.expectOne((r) => r.url.endsWith('/news/preferences/')).flush(
      PREFS_RESPONSE,
    );
    expect(store.translationChoices().length).toBe(1);
    expect(store.translationChoices()[0].id).toBe(
      'openrouter:qwen/qwen3-235b-a22b-2507',
    );
  });

  it('refreshes translationChoices and round-trips the three fields on save', () => {
    store
      .savePreferences({
        translation_enabled: false,
        translation_model: 'openrouter:meta-llama/llama-3.3-70b-instruct',
      })
      .subscribe();
    const req = http.expectOne((r) => r.url.endsWith('/news/preferences/'));
    expect(req.request.method).toBe('PUT');
    req.flush(PREFS_RESPONSE);
    expect(store.translationChoices().length).toBe(1);
    expect(store.preferences()?.translation_model).toBe(
      'openrouter:qwen/qwen3-235b-a22b-2507',
    );
  });
});
