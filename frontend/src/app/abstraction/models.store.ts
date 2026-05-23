import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, forkJoin, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  AgentInfo,
  AgentsResponse,
  ModelEntry,
  ModelPreferences,
  ProviderKeyStatus,
  VerifyPricingResponse,
} from '../core/models/model.types';

@Injectable({ providedIn: 'root' })
export class ModelsStore {
  private readonly api = inject(ApiClient);

  private readonly _models = signal<ModelEntry[]>([]);
  private readonly _agents = signal<AgentInfo[]>([]);
  private readonly _presets = signal<string[]>([]);
  private readonly _prefs = signal<ModelPreferences | null>(null);
  private readonly _keys = signal<ProviderKeyStatus | null>(null);

  readonly models = this._models.asReadonly();
  readonly agents = this._agents.asReadonly();
  readonly presets = this._presets.asReadonly();
  readonly prefs = this._prefs.asReadonly();
  readonly keys = this._keys.asReadonly();

  readonly defaultsMap = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const a of this._agents()) out[a.id] = a.default_model;
    const prefs = this._prefs();
    if (prefs?.per_agent_defaults) Object.assign(out, prefs.per_agent_defaults);
    return out;
  });

  loadAll(): Observable<unknown> {
    return forkJoin({
      models: this.api.get<{ models: ModelEntry[] }>('/models/').pipe(
        tap((r) => this._models.set(r.models)),
      ),
      agents: this.api.get<AgentsResponse>('/agents/').pipe(
        tap((r) => {
          this._agents.set(r.agents);
          this._presets.set(r.presets);
        }),
      ),
      prefs: this.api.get<ModelPreferences>('/me/model-preferences/').pipe(
        tap((r) => this._prefs.set(r)),
      ),
      keys: this.api.get<ProviderKeyStatus>('/me/provider-keys/').pipe(
        tap((r) => this._keys.set(r)),
      ),
    });
  }

  loadModels(): Observable<{ models: ModelEntry[] }> {
    return this.api.get<{ models: ModelEntry[] }>('/models/').pipe(
      tap((r) => this._models.set(r.models)),
    );
  }

  fetchPreset(name: string): Observable<{ preset: string; overrides: Record<string, string> }> {
    return this.api.get(`/presets/${name}/`);
  }

  savePrefs(p: Partial<ModelPreferences>): Observable<ModelPreferences> {
    return this.api.put<ModelPreferences>('/me/model-preferences/', p).pipe(
      tap((r) => this._prefs.set(r)),
    );
  }

  saveKeys(body: Record<string, string>): Observable<ProviderKeyStatus> {
    return this.api.put<ProviderKeyStatus>('/me/provider-keys/', body).pipe(
      tap((r) => this._keys.set(r)),
    );
  }

  verifyPricing(modelIds?: string[]): Observable<VerifyPricingResponse> {
    return this.api.post<VerifyPricingResponse>(
      '/models/verify-pricing/',
      modelIds && modelIds.length ? { model_ids: modelIds } : {},
    ).pipe(
      tap((r) => {
        if (!r.models?.length) return;
        const byId = new Map(r.models.map((m) => [m.id, m]));
        this._models.update((rows) =>
          rows.map((row) => {
            const fresh = byId.get(row.id);
            if (!fresh) return row;
            return { ...row, ...fresh, available: row.available };
          }),
        );
      }),
    );
  }
}
