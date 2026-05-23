/**
 * P3-prereq-5 — investor-profile signal store.
 */
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, of, shareReplay, tap } from 'rxjs';

import { ApiClient } from '../core/api/api-client';
import {
  InvestorProfileState,
  NudgeInfo,
  ProfileBundle,
  QuestionnaireHistoryItem,
  QuestionnaireResponse,
  QuestionnaireSchema,
} from '../core/models/investor-profile.model';

interface SubmitBody {
  answers: Record<string, unknown>;
  model_id: string;
}

interface TuneBody {
  risk_band?: string;
  horizon_band?: string;
  patience_band?: string;
}

@Injectable({ providedIn: 'root' })
export class InvestorProfileStore {
  private readonly api = inject(ApiClient);

  private readonly _bundle = signal<ProfileBundle | null>(null);
  private readonly _schema = signal<QuestionnaireSchema | null>(null);
  private readonly _loading = signal(false);
  private readonly _submitting = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _history = signal<QuestionnaireHistoryItem[]>([]);
  private readonly _activeViewing = signal<QuestionnaireResponse | null>(null);

  private _bundleInFlight: Observable<ProfileBundle> | null = null;
  private _schemaInFlight: Observable<QuestionnaireSchema> | null = null;

  readonly bundle = this._bundle.asReadonly();
  readonly schema = this._schema.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly submitting = this._submitting.asReadonly();
  readonly error = this._error.asReadonly();
  readonly history = this._history.asReadonly();
  readonly activeViewing = this._activeViewing.asReadonly();

  readonly hasProfile = computed(() => this._bundle()?.has_questionnaire === true);
  readonly state = computed<InvestorProfileState | null>(
    () => this._bundle()?.state ?? null,
  );
  readonly nudge = computed<NudgeInfo>(() => {
    const n = this._bundle()?.nudge;
    return n ?? { due: false, form: null };
  });
  readonly active = computed<QuestionnaireResponse | null>(
    () => this._bundle()?.active ?? null,
  );

  setError(msg: string | null): void {
    this._error.set(msg);
  }

  load(force = false): Observable<ProfileBundle> {
    if (!force && this._bundle()) return of(this._bundle()!);
    if (this._bundleInFlight) return this._bundleInFlight;
    this._loading.set(true);
    this._bundleInFlight = this.api.get<ProfileBundle>('/profile/').pipe(
      tap({
        next: (b) => {
          this._bundle.set(b);
          this._loading.set(false);
          this._bundleInFlight = null;
        },
        error: (err) => {
          this._error.set(
            err?.error?.detail ?? err?.message ?? 'Failed to load profile.',
          );
          this._loading.set(false);
          this._bundleInFlight = null;
        },
      }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    return this._bundleInFlight;
  }

  loadSchema(): Observable<QuestionnaireSchema> {
    if (this._schema()) return of(this._schema()!);
    if (this._schemaInFlight) return this._schemaInFlight;
    this._schemaInFlight = this.api
      .get<QuestionnaireSchema>('/profile/questionnaire/schema/')
      .pipe(
        tap({
          next: (s) => {
            this._schema.set(s);
            this._schemaInFlight = null;
          },
          error: () => {
            this._schemaInFlight = null;
          },
        }),
        shareReplay({ bufferSize: 1, refCount: false }),
      );
    return this._schemaInFlight;
  }

  submit(body: SubmitBody): Observable<QuestionnaireResponse> {
    this._submitting.set(true);
    this._error.set(null);
    return this.api
      .post<QuestionnaireResponse>('/profile/questionnaire/', body)
      .pipe(
        tap({
          next: () => this._submitting.set(false),
          error: (err) => {
            this._error.set(
              err?.error?.detail ?? err?.message ?? 'Submission failed.',
            );
            this._submitting.set(false);
          },
        }),
      );
  }

  pollOne(id: number): Observable<QuestionnaireResponse> {
    return this.api.get<QuestionnaireResponse>(
      `/profile/questionnaire/${id}/`,
    );
  }

  tune(activeId: number, body: TuneBody): Observable<QuestionnaireResponse> {
    return this.api
      .post<QuestionnaireResponse>(
        `/profile/questionnaire/${activeId}/tune/`,
        body,
      )
      .pipe(
        tap({
          next: () => this.load(true).subscribe(),
        }),
      );
  }

  setApplyToRuns(value: boolean): Observable<InvestorProfileState> {
    return this.api
      .patch<InvestorProfileState>('/profile/state/', { apply_to_runs: value })
      .pipe(
        tap({
          next: (s) => {
            const b = this._bundle();
            if (b) this._bundle.set({ ...b, state: s });
          },
        }),
      );
  }

  dismissNudge(): Observable<unknown> {
    return this.api.post('/profile/nudge/dismiss/', {}).pipe(
      tap(() => {
        const b = this._bundle();
        if (b) {
          this._bundle.set({
            ...b,
            nudge: { due: false, form: null },
            state: {
              ...b.state,
              nudge_dismiss_count: (b.state.nudge_dismiss_count ?? 0) + 1,
              nudge_last_dismissed_at: new Date().toISOString(),
            },
          });
        }
      }),
    );
  }

  loadHistory(): Observable<{ items: QuestionnaireHistoryItem[] }> {
    return this.api
      .get<{ items: QuestionnaireHistoryItem[] }>(
        '/profile/questionnaire/history/',
      )
      .pipe(tap((r) => this._history.set(r.items)));
  }

  loadOne(id: number): Observable<QuestionnaireResponse> {
    return this.api
      .get<QuestionnaireResponse>(`/profile/questionnaire/${id}/`)
      .pipe(tap((r) => this._activeViewing.set(r)));
  }

  clearViewing(): void {
    this._activeViewing.set(null);
  }
}
