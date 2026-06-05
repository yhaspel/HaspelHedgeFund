import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ModelsStore } from './models.store';

const API_BASE = 'http://localhost:8811/api';

describe('ModelsStore · alphabetical sort by display_name (P3-C §7.5)', () => {
  let store: ModelsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        ModelsStore,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    store = TestBed.inject(ModelsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('sorts the catalog returned by loadModels() by display_name (case-insensitive)', () => {
    store.loadModels().subscribe();
    const req = http.expectOne(`${API_BASE}/models/`);
    req.flush({
      models: [
        { id: 'zeta', display_name: 'Zeta model', provider: 'openrouter' },
        { id: 'alpha', display_name: 'alpha model', provider: 'openrouter' },
        { id: 'mid', display_name: 'Mid model', provider: 'openrouter' },
      ],
    });
    const names = store.models().map((m) => m.display_name);
    expect(names).toEqual(['alpha model', 'Mid model', 'Zeta model']);
  });

  it('sorts after fetchOpenRouterModels merge', () => {
    store.loadModels().subscribe();
    http.expectOne(`${API_BASE}/models/`).flush({
      models: [
        { id: 'one', display_name: 'B model', provider: 'openrouter' },
        { id: 'two', display_name: 'A model', provider: 'openrouter' },
      ],
    });
    expect(store.models().map((m) => m.display_name)).toEqual(['A model', 'B model']);

    store.fetchOpenRouterModels().subscribe();
    http.expectOne(`${API_BASE}/models/fetch/`).flush({
      synced: ['one'],
      created: [],
      deactivated: [],
      excluded: [],
      fetched_at: '2026-05-26T00:00:00Z',
      models: [
        { id: 'one', display_name: 'C model', provider: 'openrouter' },
      ],
    });
    expect(store.models().map((m) => m.display_name)).toEqual(['A model', 'C model']);
  });
});

describe('ModelsStore · tier config (operator)', () => {
  let store: ModelsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [ModelsStore, provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(ModelsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('loadTierConfigs() GETs /tiers/ and sets tierConfigs', () => {
    store.loadTierConfigs().subscribe();
    http.expectOne(`${API_BASE}/tiers/`).flush({
      tiers: [
        { tier_name: 'frugal', default_model: 'm1', members: [], is_free_only: false,
          price_ceiling_in: '1', price_ceiling_out: '5', allow_reasoning: false,
          is_live_synced: true },
      ],
    });
    expect(store.tierConfigs().map((t) => t.tier_name)).toEqual(['frugal']);
  });

  it('saveTierConfig() PUTs /tiers/<name>/ and replaces that row', () => {
    store.loadTierConfigs().subscribe();
    http.expectOne(`${API_BASE}/tiers/`).flush({
      tiers: [
        { tier_name: 'frugal', default_model: 'm1', members: [], is_free_only: false,
          price_ceiling_in: null, price_ceiling_out: null, allow_reasoning: false,
          is_live_synced: true },
      ],
    });
    store.saveTierConfig('frugal', { members: ['m2'], default_model: 'm2' }).subscribe();
    const req = http.expectOne(`${API_BASE}/tiers/frugal/`);
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ members: ['m2'], default_model: 'm2' });
    req.flush({
      tier_name: 'frugal', default_model: 'm2',
      members: [{ model_id: 'm2', ordering: 0, role: '', display_name: 'M2',
                  supports_reasoning: false, is_active: true }],
      is_free_only: false, price_ceiling_in: null, price_ceiling_out: null,
      allow_reasoning: false, is_live_synced: true,
    });
    expect(store.tierConfigs()[0].default_model).toBe('m2');
  });
});
