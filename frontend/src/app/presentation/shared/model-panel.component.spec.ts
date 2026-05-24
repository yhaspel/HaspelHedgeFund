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

class StubModelsStore {
  prefs = signal({ preset: 'frugal' } as any);
  models = signal([] as any[]);
  agents = signal([] as any[]);
  presets = signal(['dev', 'research', 'quality', 'frugal', 'hybrid']);
  keys = signal(null);
  defaultsMap = signal({});
  fetchPresetCalls: string[] = [];

  fetchPreset(name: string): Observable<{ preset: string; overrides: Record<string, string> }> {
    this.fetchPresetCalls.push(name);
    const overrides: Record<string, string> = {};
    for (const a of COUNCIL) {
      overrides[a] = name === 'frugal' ? 'openrouter:qwen/qwen3.6-27b' : `mock:${name}`;
    }
    if (name === 'frugal') {
      overrides['portfolio_manager'] = 'openrouter:meta-llama/llama-3.3-70b-instruct';
    }
    return of({ preset: name, overrides });
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
