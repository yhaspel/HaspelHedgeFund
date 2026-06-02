import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, of, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  AgentGraphSummary,
  AgentGraphVersion,
  AgentGraphVersionRow,
  GraphDraft,
  GraphRegistry,
  ValidationResult,
} from '../core/models/graph.model';

@Injectable({ providedIn: 'root' })
export class GraphsStore {
  private readonly api = inject(ApiClient);

  private readonly _registry = signal<GraphRegistry | null>(null);
  private readonly _graphs = signal<AgentGraphSummary[]>([]);

  readonly registry = this._registry.asReadonly();
  readonly graphs = this._graphs.asReadonly();

  readonly templates = computed(() => this._graphs().filter((g) => g.is_template));
  readonly ownGraphs = computed(() => this._graphs().filter((g) => g.owned));

  /** Cached registry load (palette never changes within a session). */
  loadRegistry(): Observable<GraphRegistry> {
    const cached = this._registry();
    if (cached) return of(cached);
    return this.api
      .get<GraphRegistry>('/graphs/registry/')
      .pipe(tap((r) => this._registry.set(r)));
  }

  loadGraphs(): Observable<AgentGraphSummary[]> {
    return this.api
      .get<AgentGraphSummary[]>('/graphs/')
      .pipe(tap((r) => this._graphs.set(r)));
  }

  getGraph(id: number): Observable<AgentGraphSummary> {
    return this.api.get<AgentGraphSummary>(`/graphs/${id}/`);
  }

  createGraph(body: { name: string; description?: string }): Observable<AgentGraphSummary> {
    return this.api
      .post<AgentGraphSummary>('/graphs/', body)
      .pipe(tap(() => this.loadGraphs().subscribe()));
  }

  renameGraph(id: number, body: { name?: string; description?: string }): Observable<AgentGraphSummary> {
    return this.api
      .patch<AgentGraphSummary>(`/graphs/${id}/`, body)
      .pipe(tap(() => this.loadGraphs().subscribe()));
  }

  archiveGraph(id: number): Observable<void> {
    return this.api
      .delete<void>(`/graphs/${id}/`)
      .pipe(tap(() => this.loadGraphs().subscribe()));
  }

  fromTemplate(templateId: number, name: string): Observable<AgentGraphSummary> {
    return this.api
      .post<AgentGraphSummary>(`/graphs/from-template/${templateId}/`, { name })
      .pipe(tap(() => this.loadGraphs().subscribe()));
  }

  listVersions(graphId: number): Observable<AgentGraphVersionRow[]> {
    return this.api.get<AgentGraphVersionRow[]>(`/graphs/${graphId}/versions/`);
  }

  getVersion(graphId: number, version: number): Observable<AgentGraphVersion> {
    return this.api.get<AgentGraphVersion>(`/graphs/${graphId}/versions/${version}/`);
  }

  saveVersion(graphId: number, draft: GraphDraft): Observable<AgentGraphVersion> {
    return this.api.post<AgentGraphVersion>(`/graphs/${graphId}/versions/`, draft);
  }

  validate(draft: Omit<GraphDraft, 'notes'>): Observable<ValidationResult> {
    return this.api.post<ValidationResult>('/graphs/validate/', draft);
  }
}
