import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, map, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  CreateRunRequest,
  ModelOption,
  RunDetail,
  RunSummary,
} from '../core/models/run.model';

@Injectable({ providedIn: 'root' })
export class RunsStore {
  private readonly api = inject(ApiClient);

  private readonly _runs = signal<RunSummary[]>([]);
  private readonly _runsCount = signal(0);
  private readonly _current = signal<RunDetail | null>(null);
  private readonly _models = signal<ModelOption[]>([]);
  private pollHandle: ReturnType<typeof setTimeout> | null = null;

  readonly runs = this._runs.asReadonly();
  /** P10 §D4: total rows server-side (the list is paginated at 50). */
  readonly runsCount = this._runsCount.asReadonly();
  readonly currentRun = this._current.asReadonly();
  readonly models = this._models.asReadonly();
  readonly isPolling = computed(() => this.pollHandle !== null);

  loadModels(): Observable<{ models: ModelOption[] }> {
    return this.api.get<{ models: ModelOption[] }>('/models/').pipe(
      tap((r) => this._models.set(r.models)),
    );
  }

  // P10 §D4: the endpoint is paginated ({count, results}, page size 50) and
  // defaults to the last 30 days. `days: 'all'` widens to everything.
  listRuns(opts?: {
    source?: 'all' | 'adhoc' | 'strategy';
    portfolioTarget?: number;
    search?: string;
    days?: number | 'all';
    page?: number;
  }): Observable<RunSummary[]> {
    const params: string[] = [];
    if (opts?.source && opts.source !== 'all') {
      params.push(`source=${encodeURIComponent(opts.source)}`);
    }
    if (opts?.portfolioTarget !== undefined && opts?.portfolioTarget !== null) {
      params.push(`portfolio_target=${opts.portfolioTarget}`);
    }
    if (opts?.search) {
      params.push(`search=${encodeURIComponent(opts.search)}`);
    }
    if (opts?.days !== undefined) {
      params.push(`days=${opts.days}`);
    }
    if (opts?.page && opts.page > 1) {
      params.push(`page=${opts.page}`);
    }
    const qs = params.length ? `?${params.join('&')}` : '';
    return this.api
      .get<RunSummary[] | { count: number; results: RunSummary[] }>(`/runs/${qs}`)
      .pipe(
        map((r) => {
          const rows = Array.isArray(r) ? r : (r?.results ?? []);
          this._runsCount.set(Array.isArray(r) ? rows.length : (r?.count ?? rows.length));
          this._runs.set(rows);
          return rows;
        }),
      );
  }

  submitRun(body: CreateRunRequest): Observable<RunSummary> {
    return this.api.post<RunSummary>('/runs/', body);
  }

  // P4 WS-A: rerun a terminal run. The backend copies the original's payload
  // and returns the new run's id + rerun_of link.
  rerunRun(runId: number): Observable<{ id: number; status: string; rerun_of: number }> {
    return this.api.post<{ id: number; status: string; rerun_of: number }>(
      `/runs/${runId}/rerun/`,
      {},
    );
  }

  cancelRun(runId: number): Observable<{ id: number; status: string }> {
    return this.api.post<{ id: number; status: string }>(`/runs/${runId}/cancel/`, {});
  }

  pollRun(runId: number, intervalMs = 2000): void {
    this.stopPolling();
    const tick = () => {
      this.api.get<RunDetail>(`/runs/${runId}/`).subscribe({
        next: (run) => {
          this._current.set(run);
          if (run.status === 'queued' || run.status === 'running') {
            this.pollHandle = setTimeout(tick, intervalMs);
          } else {
            this.pollHandle = null;
          }
        },
        error: () => {
          this.pollHandle = null;
        },
      });
    };
    tick();
  }

  stopPolling(): void {
    if (this.pollHandle) {
      clearTimeout(this.pollHandle);
      this.pollHandle = null;
    }
  }
}
