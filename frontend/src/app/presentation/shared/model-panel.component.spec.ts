import { Component, ViewChild, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, of } from 'rxjs';
import { ModelsStore } from '../../abstraction/models.store';
import { ModelPanelComponent } from './model-panel.component';

const COUNCIL = [
  'buffett', 'munger', 'graham', 'wood', 'druckenmiller',
  'fundamentals', 'technicals', 'valuation', 'sentiment',
  'macro', 'news_digest', 'risk_manager', 'portfolio_manager',
];

const DEV_MENU = [
  'openrouter:openai/gpt-oss-120b:free',
  'openrouter:nvidia/nemotron-3-super-120b-a12b:free',
  'openrouter:deepseek/deepseek-v4-flash:free',
  'openrouter:google/gemma-4-31b-it:free',
];

function _row(id: string, opts: Record<string, unknown> = {}): any {
  return {
    id,
    provider: id.split(':')[0],
    display_name: opts['display_name'] ?? id,
    tier: opts['tier'] ?? 'hosted_open',
    context_window: 131072,
    supports_caching: false,
    supports_structured_output: true,
    supports_long_context: true,
    price_in_per_mtok: opts['price_in_per_mtok'] ?? '0',
    price_out_per_mtok: opts['price_out_per_mtok'] ?? '0',
    notes: '',
    available: opts['available'] ?? true,
    ...opts,
  };
}

class StubModelsStore {
  prefs = signal<any>(null);  // start null so the prefs effect doesn't auto-apply
  models = signal<any[]>([]);
  agents = signal<any[]>([]);
  presets = signal(['dev', 'research', 'quality', 'frugal', 'hybrid']);
  keys = signal(null);
  defaultsMap = signal({});
  fetchPresetCalls: string[] = [];
  fetchPresetMenu: Record<string, string[]> = {
    frugal: ['openrouter:qwen/qwen3.6-27b'],
    dev: DEV_MENU,
  };

  fetchPreset(name: string): Observable<{ preset: string; overrides: Record<string, string>; menu?: string[] }> {
    this.fetchPresetCalls.push(name);
    const overrides: Record<string, string> = {};
    for (const a of COUNCIL) {
      overrides[a] = name === 'frugal' ? 'openrouter:qwen/qwen3.6-27b' : `mock:${name}`;
    }
    if (name === 'frugal') {
      overrides['portfolio_manager'] = 'openrouter:meta-llama/llama-3.3-70b-instruct';
    }
    return of({ preset: name, overrides, menu: this.fetchPresetMenu[name] });
  }
}

@Component({
  standalone: true,
  imports: [ModelPanelComponent],
  template: `<hf-model-panel #p [agents]="agents" [(overrides)]="overrides" />`,
})
class HarnessComponent {
  @ViewChild('p', { static: true }) panel!: ModelPanelComponent;
  agents = COUNCIL;
  overrides: Record<string, string> = {};
}

describe('hf-model-panel · prefs → overrides sync', () => {
  let store: StubModelsStore;

  beforeEach(async () => {
    store = new StubModelsStore();
    store.prefs.set({ preset: 'frugal' } as any);
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
      providers: [{ provide: ModelsStore, useValue: store }],
    }).compileComponents();
  });

  it('populates `overrides` from the saved preset on first render', () => {
    const fixture: ComponentFixture<HarnessComponent> = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();
    fixture.detectChanges();

    expect(store.fetchPresetCalls).toEqual(['frugal']);
    const overrides = fixture.componentInstance.panel.overrides();
    expect(Object.keys(overrides).length).toBe(COUNCIL.length);
    expect(overrides['portfolio_manager']).toBe('openrouter:meta-llama/llama-3.3-70b-instruct');
    expect(overrides['buffett']).toBe('openrouter:qwen/qwen3.6-27b');
    expect(fixture.componentInstance.panel.activePreset()).toBe('frugal');
  });

  it('does not overwrite overrides the user has already set locally', () => {
    const fixture: ComponentFixture<HarnessComponent> = TestBed.createComponent(HarnessComponent);
    fixture.componentInstance.overrides = { buffett: 'user:choice' };
    fixture.detectChanges();
    fixture.detectChanges();

    expect(store.fetchPresetCalls).toEqual([]);
    expect(fixture.componentInstance.panel.overrides()['buffett']).toBe('user:choice');
  });
});

describe('hf-model-panel · tier-scoped dropdowns (P3-C §7.1)', () => {
  let store: StubModelsStore;
  const FULL_CATALOG = [
    _row('anthropic:claude-opus-4-7'),
    _row('anthropic:claude-sonnet-4-6'),
    _row('openrouter:openai/gpt-oss-120b:free'),
    _row('openrouter:nvidia/nemotron-3-super-120b-a12b:free'),
    _row('openrouter:deepseek/deepseek-v4-flash:free'),
    _row('openrouter:google/gemma-4-31b-it:free'),
    _row('openrouter:meta-llama/llama-3.3-70b-instruct'),
    _row('ollama:llama3.3:8b'),
  ];

  beforeEach(async () => {
    store = new StubModelsStore();
    store.models.set(FULL_CATALOG);
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
      providers: [{ provide: ModelsStore, useValue: store }],
    }).compileComponents();
  });

  it('scopes the per-agent dropdown to the active tier menu', () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();
    fixture.componentInstance.panel.applyPreset('dev');
    fixture.detectChanges();
    const visible = fixture.componentInstance.panel.visibleModels('buffett').map((m) => m.id);
    // Every dev-menu id should appear.
    for (const m of DEV_MENU) expect(visible).toContain(m);
    // Off-tier Anthropic should be hidden.
    expect(visible).not.toContain('anthropic:claude-opus-4-7');
    expect(visible).not.toContain('openrouter:meta-llama/llama-3.3-70b-instruct');
  });

  it('always includes discovered local (Ollama) models even when off-tier', () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();
    fixture.componentInstance.panel.applyPreset('dev');
    fixture.detectChanges();
    const visible = fixture.componentInstance.panel.visibleModels('buffett').map((m) => m.id);
    expect(visible).toContain('ollama:llama3.3:8b');
  });

  it('Show all models toggle restores the full catalog', () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();
    fixture.componentInstance.panel.applyPreset('dev');
    fixture.detectChanges();
    fixture.componentInstance.panel.showAll.set(true);
    fixture.detectChanges();
    const visible = fixture.componentInstance.panel.visibleModels('buffett').map((m) => m.id);
    expect(visible).toEqual(FULL_CATALOG.map((m) => m.id));
  });

  it('keeps an outside-menu selected value visible (stale override case)', () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.componentInstance.overrides = {
      buffett: 'anthropic:claude-opus-4-7',
    };
    fixture.detectChanges();
    fixture.componentInstance.panel.applyPreset('dev');
    fixture.detectChanges();
    // applyPreset overwrites `overrides` with the preset's resolved map,
    // but the user might still navigate back. Re-set the override to
    // simulate a stale per-agent choice.
    fixture.componentInstance.panel.overrides.set({ buffett: 'anthropic:claude-opus-4-7' });
    fixture.detectChanges();
    const visible = fixture.componentInstance.panel.visibleModels('buffett').map((m) => m.id);
    expect(visible).toContain('anthropic:claude-opus-4-7');
  });

  it('empty menu falls back to the full catalog', () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();
    // No applyPreset call → tierMenu() is [].
    const visible = fixture.componentInstance.panel.visibleModels('buffett').map((m) => m.id);
    expect(visible).toEqual(FULL_CATALOG.map((m) => m.id));
  });
});
