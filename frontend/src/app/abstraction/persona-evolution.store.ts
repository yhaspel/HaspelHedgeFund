/** P3-D — persona-evolution signal store. */
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { ApiClient } from '../core/api/api-client';
import {
  PersonaEvolutionProfile,
  PersonaEvolutionRevision,
  PersonaEvolutionRunAck,
  PersonaEvolutionSettings,
} from '../core/models/persona-evolution.model';

interface ProfilesResponse {
  items: PersonaEvolutionProfile[];
}

interface RevisionsResponse {
  persona: PersonaEvolutionProfile;
  items: PersonaEvolutionRevision[];
}

const POLL_INTERVAL_MS = 2500;
// Grace period: Celery dispatch + worker pickup can take a beat. Don't
// declare "done" before the worker has had a chance to set the first
// in-flight marker.
const POLL_GRACE_MS = 4000;

@Injectable({ providedIn: 'root' })
export class PersonaEvolutionStore {
  private readonly api = inject(ApiClient);

  private readonly _settings = signal<PersonaEvolutionSettings | null>(null);
  private readonly _profiles = signal<PersonaEvolutionProfile[]>([]);
  private readonly _revisions = signal<RevisionsResponse | null>(null);
  private readonly _loading = signal(false);
  private readonly _runQueuedAt = signal<number | null>(null);
  private readonly _error = signal<string | null>(null);
  private readonly _runMsg = signal<string | null>(null);
  private _pollHandle: ReturnType<typeof setTimeout> | null = null;

  readonly settings = this._settings.asReadonly();
  readonly profiles = this._profiles.asReadonly();
  readonly revisions = this._revisions.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();
  readonly runMsg = this._runMsg.asReadonly();

  /** Names of personas whose cycle is currently in flight on the worker. */
  readonly runningPersonas = computed(() =>
    this._profiles()
      .filter((p) => !!p.current_cycle_started_at)
      .map((p) => p.persona_name),
  );

  /** True while a queued run hasn't yet shown a profile marker AND for
   *  as long as at least one profile reports an in-flight cycle. Survives
   *  a page reload because the profile flag lives in the DB. */
  readonly running = computed(() => {
    if (this.runningPersonas().length > 0) return true;
    const queued = this._runQueuedAt();
    if (queued && Date.now() - queued < POLL_GRACE_MS) return true;
    return false;
  });

  /** Best-effort label for the persona being analysed right now. */
  readonly currentPersonaLabel = computed(() => {
    const names = this.runningPersonas();
    if (names.length === 0) return null;
    const byName: Record<string, string> = {};
    for (const p of this._profiles()) byName[p.persona_name] = p.display_name;
    return names.map((n) => byName[n] ?? n).join(', ');
  });

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

  runNow(persona?: string): Observable<PersonaEvolutionRunAck> {
    this._runMsg.set('Queued. Watching for progress…');
    this._runQueuedAt.set(Date.now());
    const body = persona ? { persona } : {};
    return this.api
      .post<PersonaEvolutionRunAck>('/persona-evolution/run/', body)
      .pipe(
        tap({
          next: (r) => {
            if (r.ran_inline) {
              this._runMsg.set('Ran inline (broker unavailable). Refreshing…');
            } else {
              this._runMsg.set('Queued — watching for progress…');
            }
            this.startPolling();
          },
          error: (err) => {
            this._runQueuedAt.set(null);
            this._runMsg.set(
              err?.error?.detail ?? err?.message ?? 'Run failed.',
            );
          },
        }),
      );
  }

  /** Begin polling profiles every POLL_INTERVAL_MS until no profile has
   *  ``current_cycle_started_at`` AND the grace period has elapsed. Idempotent
   *  — safe to call repeatedly (e.g. on page load if a run is already in
   *  flight elsewhere). */
  startPolling(): void {
    if (this._pollHandle) return;
    const tick = () => {
      this.loadProfiles().subscribe({
        next: () => {
          if (this.running()) {
            this._pollHandle = setTimeout(tick, POLL_INTERVAL_MS);
          } else {
            this._pollHandle = null;
            this._runQueuedAt.set(null);
            const ran = this._profiles()
              .filter((p) => p.last_cycle_status === 'ok')
              .map((p) => p.display_name)
              .join(', ');
            this._runMsg.set(
              ran ? `Done. Latest cycles: ${ran}.` : 'Done.',
            );
          }
        },
        error: () => {
          this._pollHandle = setTimeout(tick, POLL_INTERVAL_MS);
        },
      });
    };
    tick();
  }

  stopPolling(): void {
    if (this._pollHandle) {
      clearTimeout(this._pollHandle);
      this._pollHandle = null;
    }
  }
}
