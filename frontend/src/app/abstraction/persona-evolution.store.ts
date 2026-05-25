/** P3-D — persona-evolution signal store. */
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { ApiClient } from '../core/api/api-client';
import {
  PersonaEvolutionProfile,
  PersonaEvolutionRevision,
  PersonaEvolutionRunResult,
  PersonaEvolutionSettings,
} from '../core/models/persona-evolution.model';

interface ProfilesResponse {
  items: PersonaEvolutionProfile[];
}

interface RevisionsResponse {
  persona: PersonaEvolutionProfile;
  items: PersonaEvolutionRevision[];
}

@Injectable({ providedIn: 'root' })
export class PersonaEvolutionStore {
  private readonly api = inject(ApiClient);

  private readonly _settings = signal<PersonaEvolutionSettings | null>(null);
  private readonly _profiles = signal<PersonaEvolutionProfile[]>([]);
  private readonly _revisions = signal<RevisionsResponse | null>(null);
  private readonly _loading = signal(false);
  private readonly _running = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _runMsg = signal<string | null>(null);

  readonly settings = this._settings.asReadonly();
  readonly profiles = this._profiles.asReadonly();
  readonly revisions = this._revisions.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly running = this._running.asReadonly();
  readonly error = this._error.asReadonly();
  readonly runMsg = this._runMsg.asReadonly();

  loadSettings(): Observable<PersonaEvolutionSettings> {
    this._loading.set(true);
    return this.api
      .get<PersonaEvolutionSettings>('/persona-evolution/settings/')
      .pipe(
        tap({
          next: (s) => {
            this._settings.set(s);
            this._loading.set(false);
          },
          error: (err) => {
            this._loading.set(false);
            this._error.set(
              err?.error?.detail ?? err?.message ?? 'Failed to load settings.',
            );
          },
        }),
      );
  }

  patchSettings(
    body: Partial<PersonaEvolutionSettings>,
  ): Observable<PersonaEvolutionSettings> {
    return this.api
      .patch<PersonaEvolutionSettings>('/persona-evolution/settings/', body)
      .pipe(tap((s) => this._settings.set(s)));
  }

  loadProfiles(): Observable<ProfilesResponse> {
    return this.api
      .get<ProfilesResponse>('/persona-evolution/profiles/')
      .pipe(tap((r) => this._profiles.set(r.items)));
  }

  loadRevisions(personaName: string): Observable<RevisionsResponse> {
    return this.api
      .get<RevisionsResponse>(
        `/persona-evolution/profiles/${personaName}/revisions/`,
      )
      .pipe(tap((r) => this._revisions.set(r)));
  }

  clearRevisions(): void {
    this._revisions.set(null);
  }

  runNow(persona?: string): Observable<PersonaEvolutionRunResult> {
    this._running.set(true);
    this._runMsg.set(null);
    const body = persona ? { persona } : {};
    return this.api
      .post<PersonaEvolutionRunResult>('/persona-evolution/run/', body)
      .pipe(
        tap({
          next: (r) => {
            this._running.set(false);
            const ranTxt = r.ran.length ? `ran: ${r.ran.join(', ')}` : '';
            const failedTxt = r.failed.length ? `failed: ${r.failed.join(', ')}` : '';
            const skippedTxt = r.skipped.length ? `skipped: ${r.skipped.length}` : '';
            const reason = r.reason ? `reason: ${r.reason}` : '';
            this._runMsg.set([ranTxt, failedTxt, skippedTxt, reason]
              .filter(Boolean).join(' · '));
            this.loadProfiles().subscribe();
          },
          error: (err) => {
            this._running.set(false);
            this._runMsg.set(
              err?.error?.detail ?? err?.message ?? 'Run failed.',
            );
          },
        }),
      );
  }
}
