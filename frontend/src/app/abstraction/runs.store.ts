import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
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
  private readonly _current = signal<RunDetail | null>(null);
  private readonly _models = signal<ModelOption[]>([]);
  private pollHandle: ReturnType<typeof setTimeout> | null = null;

  readonly runs = this._runs.asReadonly();
  readonly currentRun = this._current.asReadonly();
  readonly models = this._models.asReadonly();
  readonly isPolling = computed(() => this.pollHandle !== null);

  loadModels(): Observable<{ models: ModelOption[] }> {
    return this.api.get<{ models: ModelOption[] }>('/models/').pipe(
      tap((r) => this._models.set(r.models)),
    );
  }

  listRuns(): Observable<RunSummary[]> {
    return this.api.get<RunSummary[]>('/runs/').pipe(
      tap((r) => this._runs.set(Array.isArray(r) ? r : [])),
    );
  }

  submitRun(body: CreateRunRequest): Observable<RunSummary> {
    return this.api.post<RunSummary>('/runs/', body);
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
