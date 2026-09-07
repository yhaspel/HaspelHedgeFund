/**
 * Review (feresearch) — settings (BYOK) + news CTA proof tests.
 *
 *   `it.fails('F: …')`  = confirmed defect; the assertion states the CORRECT
 *                         behaviour and currently fails.
 *   `it('evidence: …')` = passes today and documents the behaviour.
 */
import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { CUSTOM_ELEMENTS_SCHEMA, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink, provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { SettingsProvidersPage } from './settings-providers.page';
import { NewsPage } from '../news/news.page';
import { ModelsStore } from '../../abstraction/models.store';
import { NewsStore } from '../../abstraction/news.store';
import { routes } from '../../app.routes';

function providersPage() {
  const bodies: Record<string, string>[] = [];
  const store = {
    keys: signal({
      anthropic: 'unset', openrouter: 'unset', openai: 'unset', ollama_host: '',
      fmp: 'set', tiingo: 'unset', fred: 'unset', resend: 'unset',
    }).asReadonly(),
    loadAll: () => of({}),
    loadModels: () => of({ models: [] }),
    saveKeys: (body: Record<string, string>) => {
      bodies.push(body);
      return of({});
    },
  } as unknown as ModelsStore;
  TestBed.configureTestingModule({ providers: [{ provide: ModelsStore, useValue: store }] });
  TestBed.overrideComponent(SettingsProvidersPage, {
    set: { imports: [CommonModule, FormsModule], schemas: [CUSTOM_ELEMENTS_SCHEMA] },
  });
  const f = TestBed.createComponent(SettingsProvidersPage);
  f.detectChanges();
  return { f, bodies };
}

describe('F1 — BYOK: keys are masked and never echoed, but a stored key cannot be removed', () => {
  it('evidence: every key input is type=password with autocomplete=new-password and the status pill shows only set/unset', () => {
    const { f } = providersPage();
    const inputs: HTMLInputElement[] = Array.from(
      f.nativeElement.querySelectorAll('input[data-test^="llm-key-"], input[data-test^="data-key-"], input[data-test^="email-key-"]'),
    );
    expect(inputs.length).toBe(7);
    for (const i of inputs) {
      expect(i.type).toBe('password');
      expect(i.getAttribute('autocomplete')).toBe('new-password');
      expect(i.value).toBe('');
    }
    expect(f.nativeElement.textContent).toContain('set');
    expect(f.nativeElement.textContent).not.toMatch(/sk-|PK[A-Z0-9]{8}/);
  });

  it('evidence: leaving the FMP field blank sends nothing for it (blank = keep) — there is no path that sends "" to clear it', () => {
    const { f, bodies } = providersPage();
    const page = f.componentInstance;
    page.keyEdits['fmp'] = ''; // user "clears the field to remove it" (byok.md §Security)
    page.saveKeys();
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({ ollama_host: '' });
    expect('fmp_api_key' in bodies[0]).toBe(false);
    // No remove/clear control exists in the template either.
    expect(f.nativeElement.textContent).not.toMatch(/remove|clear|revoke/i);
  });

  it.fails('F: the user must be able to remove a stored key (backend PUT accepts "" → set_key clears it)', () => {
    const { f } = providersPage();
    expect(f.nativeElement.textContent).toMatch(/remove|clear|revoke/i);
  });
});

describe('F7 — News empty-state CTA "Set your data keys" points at the Models tab, not Providers', () => {
  function newsPage() {
    const store = {
      items: signal([]).asReadonly(),
      meta: signal({
        items: [], page: 1, page_size: 12, total_available: 0, has_more: false,
        generated_at: new Date().toISOString(), sentiment_enabled: false,
        sentiment_model: null, providers_used: [], warnings: [], needs_keys: true,
        chyron_enabled: false, chyron_item_count: 0, ranking_basis: '',
      }).asReadonly(),
      loading: signal(false).asReadonly(),
      loadingMore: signal(false).asReadonly(),
      refreshing: signal(false).asReadonly(),
      hasMore: signal(false).asReadonly(),
      totalAvailable: signal(0).asReadonly(),
      toast: signal(null).asReadonly(),
      sentimentEnabled: signal(false).asReadonly(),
      itemById: () => null,
      loadPreferences: () => of(null),
      loadFeed: () => of(null),
    } as unknown as NewsStore;
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: NewsStore, useValue: store },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: { get: () => null } } } },
      ],
    });
    TestBed.overrideComponent(NewsPage, {
      set: { imports: [RouterLink], schemas: [CUSTOM_ELEMENTS_SCHEMA] },
    });
    const f = TestBed.createComponent(NewsPage);
    f.detectChanges();
    return f;
  }

  it('evidence: the CTA href is /settings/models while the FMP/Tiingo inputs live on /settings/providers', () => {
    const f = newsPage();
    const a: HTMLAnchorElement | null = f.nativeElement.querySelector('a.btn.primary');
    expect(a?.textContent?.trim()).toBe('Set your data keys');
    expect(a?.getAttribute('href')).toBe('/settings/models');
    // Both routes exist; only the providers page renders key inputs
    // (data-key-fmp / data-key-tiingo — see the BYOK test above).
    expect(routes.some((r) => r.path === 'settings/providers')).toBe(true);
  });

  it.fails('F: the data-keys CTA must deep-link to the Providers tab', () => {
    const f = newsPage();
    const a: HTMLAnchorElement | null = f.nativeElement.querySelector('a.btn.primary');
    expect(a?.getAttribute('href')).toBe('/settings/providers');
  });
});
