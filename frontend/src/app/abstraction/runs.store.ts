import { Injectable, inject, signal } from '@angular/core';
import { Observable, Subscription, map, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import { apiErrorMessage } from '../core/api/api-error';
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
  private pollSub: Subscription | null = null;
  /** Bumped by stopPolling() and by every new pollRun(): a response tagged with
   *  an older generation is stale and must be ignored. */
  private pollGen = 0;
  private polledId: number | null = null;
  private readonly _polling = signal(false);
  private readonly _pollError = signal<string | null>(null);

  readonly runs = this._runs.asReadonly();
  /** P10 §D4: total rows server-side (the list is paginated at 50). */
  readonly runsCount = this._runsCount.asReadonly();
  readonly currentRun = this._current.asReadonly();
  readonly models = this._models.asReadonly();
  /** A real signal — the old `computed(() => this.pollHandle !== null)` read a
   *  plain field, so it cached its first value and never updated. */
  readonly isPolling = this._polling.asReadonly();
  /** Set when a poll tick fails; cleared when polling (re)starts. */
  readonly pollError = this._pollError.asReadonly();

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

  /**
   * Poll one run until it reaches a terminal state.
   *
   * Teardown is generation-based: `stopPolling()` (and every new `pollRun`)
   * bumps `pollGen`, so a response that was already in flight can neither write
   * to `_current` nor re-arm the timer. The in-flight request is also
   * unsubscribed, so navigating away really does cancel the HTTP call rather
   * than leaving a zombie poller running for the rest of the session.
   */
  pollRun(runId: number, intervalMs = 2000): void {
    this.stopPolling();
    // Never let run A stay on screen under run B's URL while B loads.
    if (this.polledId !== runId) {
      this._current.set(null);
      this.polledId = runId;
    }
    this._pollError.set(null);
    const gen = ++this.pollGen;
    this._polling.set(true);
    const tick = () => {
      if (gen !== this.pollGen) return;
      this.pollSub = this.api.get<RunDetail>(`/runs/${runId}/`).subscribe({
        next: (run) => {
          if (gen !== this.pollGen) return; // superseded: a stale response
          this._current.set(run);
          if (run.status === 'queued' || run.status === 'running') {
            this.pollHandle = setTimeout(tick, intervalMs);
          } else {
            this.pollHandle = null;
            this._polling.set(false);
          }
        },
        error: (err: unknown) => {
          if (gen !== this.pollGen) return;
          this.pollHandle = null;
          this._polling.set(false);
          this._pollError.set(apiErrorMessage(err, 'Could not load this run.'));
        },
      });
    };
    tick();
  }

  stopPolling(): void {
    // Invalidate any in-flight tick BEFORE cancelling, so a response that is
    // already queued as a microtask cannot re-arm the timer.
    this.pollGen++;
    if (this.pollHandle) {
      clearTimeout(this.pollHandle);
      this.pollHandle = null;
    }
    this.pollSub?.unsubscribe();
    this.pollSub = null;
    this._polling.set(false);
  }
}
