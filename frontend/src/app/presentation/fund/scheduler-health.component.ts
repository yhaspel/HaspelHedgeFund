import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { FundStore } from '../../abstraction/fund.store';
import { durationLabel } from '../../core/models/fund-activity.model';
import { ErrorStateComponent } from '../shared/error-state.component';

/**
 * WAVE 3 item 3 — "is the scheduler actually dispatching, or is it a quiet
 * Friday?".
 *
 * The single most important rule here is that `beat_alive` is TRI-STATE:
 *   • `true`  — a per-tick heartbeat is fresh.
 *   • `false` — something is demonstrably wrong (an armed autopilot is past
 *               its next run, or the per-tick stamp has gone stale).
 *   • `null`  — UNKNOWN. The only evidence available is dispatch cadence, and
 *               a weekly autopilot produces that signal once a week. Rendering
 *               `null` as "dead" would page the owner every Saturday, so it is
 *               shown as "unknown" with `last_beat_tick.detail` as the reason.
 *
 * `queue_depth` is deliberately never read by the backend (no broker
 * round-trip on the request path) and comes back null with its own reason.
 */
@Component({
  selector: 'hf-scheduler-health',
  standalone: true,
  imports: [CommonModule, RouterLink, ErrorStateComponent],
  template: `
    <section class="card" data-test="scheduler-health">
      <div class="card-hd">
        <h2 class="title">Scheduler health</h2>
        <div class="actions">
          <span class="pill" [class]="beatPillClass()" data-test="scheduler-beat">
            <span class="dot"></span>beat {{ beatLabel() }}
          </span>
          <button
            type="button"
            class="btn sm"
            data-test="scheduler-reload"
            [disabled]="store.schedulerLoading()"
            (click)="reload()"
          >
            {{ store.schedulerLoading() ? 'Checking…' : 'Re-check' }}
          </button>
        </div>
      </div>

      <div class="card-bd">
        @if (store.schedulerError(); as e) {
          <hf-error-state
            [compact]="true"
            title="Couldn’t read scheduler health"
            [detail]="e"
            (retry)="reload()"
          ></hf-error-state>
        } @else if (store.schedulerLoading() && !health()) {
          <p class="muted">Checking the dispatch record…</p>
        } @else if (health(); as h) {
          @if (!h.available) {
            <p class="muted" data-test="scheduler-unavailable">
              {{ h.reason || 'This account has no fund yet, so nothing is scheduled.' }}
            </p>
          } @else {
            <div class="strip">
              <div class="stat">
                <div class="k">Last dispatch</div>
                <div class="v mono" data-test="scheduler-last-tick">
                  {{ h.last_beat_tick?.at ? (h.last_beat_tick!.at | date: 'MMM d, HH:mm') : 'never' }}
                </div>
                <div class="sub" data-test="scheduler-tick-age">
                  {{ dur(h.last_beat_tick?.age_seconds) }} ago
                </div>
              </div>
              <div class="stat">
                <div class="k">Evidence</div>
                <div class="v src mono" data-test="scheduler-tick-source">
                  {{ h.last_beat_tick?.source || 'none' }}
                </div>
                <div class="sub" data-test="scheduler-per-tick">
                  {{ h.last_beat_tick?.per_tick ? 'per-tick heartbeat' : 'dispatch cadence only' }}
                </div>
              </div>
              <div class="stat">
                <div class="k">Armed</div>
                <div class="v mono" data-test="scheduler-armed">{{ h.armed_count ?? 0 }}</div>
                <div class="sub">autopilot(s) enabled</div>
              </div>
              <div class="stat" [class.bad]="(h.overdue_count ?? 0) > 0">
                <div class="k">Overdue</div>
                <div class="v mono" data-test="scheduler-overdue">{{ h.overdue_count ?? 0 }}</div>
                <div class="sub">grace {{ dur(h.overdue_grace_seconds) }}</div>
              </div>
              <div class="stat">
                <div class="k">Queue depth</div>
                <div class="v mono" data-test="scheduler-queue">
                  {{ h.queue_depth === null || h.queue_depth === undefined ? '—' : h.queue_depth }}
                </div>
                <div class="sub">not collected</div>
              </div>
            </div>

            <!-- The "per_tick: false" caveat is the whole reason beat_alive is
                 allowed to be null; print it rather than implying a verdict. -->
            @if (h.last_beat_tick?.detail; as detail) {
              <p class="caveat" data-test="scheduler-tick-detail">{{ detail }}</p>
            }
            @if (beatUnknown()) {
              <p class="caveat" data-test="scheduler-beat-unknown">
                Liveness is <b>unknown</b>, not dead — nothing here proves beat stopped.
              </p>
            }
            @if (h.queue_depth_reason) {
              <p class="caveat subtle" data-test="scheduler-queue-reason">
                {{ h.queue_depth_reason }}
              </p>
            }

            @if (warnings().length) {
              <!-- aria-live rather than role="status": the status role would
                   REPLACE the list role and orphan the <li> children. -->
              <ul class="warnings" aria-live="polite" data-test="scheduler-warnings">
                @for (w of warnings(); track w) {
                  <li>{{ w }}</li>
                }
              </ul>
            }

            @if (autopilots().length) {
              <div class="tbl-wrap">
                <table class="tbl" data-test="scheduler-autopilots">
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Schedule</th>
                      <th>Next run</th>
                      <th>Last dispatch</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (a of autopilots(); track a.autopilot_id) {
                      <tr [attr.data-test]="'scheduler-ap-' + a.autopilot_id">
                        <td>
                          <a [routerLink]="['/strategies', a.strategy_id]">{{ a.strategy_name }}</a>
                        </td>
                        <td class="mono sm">{{ a.cron_expression }} · {{ a.timezone }}</td>
                        <td class="mono sm">
                          {{ a.next_run_at ? (a.next_run_at | date: 'MMM d, HH:mm') : '—' }}
                          @if (a.overdue) {
                            <span
                              class="pill err"
                              [attr.data-test]="'scheduler-overdue-' + a.autopilot_id"
                            >
                              <span class="dot"></span>overdue by
                              {{ dur(a.overdue_by_seconds) }}
                            </span>
                          }
                          @if (a.armed_without_next_run) {
                            <span
                              class="pill warn"
                              [attr.data-test]="'scheduler-nonext-' + a.autopilot_id"
                            >
                              <span class="dot"></span>armed, no next run
                            </span>
                          }
                        </td>
                        <td class="mono sm">
                          {{ a.last_dispatch_at ? (a.last_dispatch_at | date: 'MMM d, HH:mm') : '—' }}
                          @if (a.last_dispatch_status) {
                            <span class="sub">{{ a.last_dispatch_status }}</span>
                          }
                        </td>
                        <td>
                          <span class="pill" [class.ok]="a.is_enabled" [class.warn]="!a.is_enabled">
                            <span class="dot"></span>{{ a.is_enabled ? a.state : 'disabled' }}
                          </span>
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            } @else {
              <p class="muted" data-test="scheduler-no-autopilots">
                No autopilot is attached to this fund yet.
              </p>
            }
          }
        }
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .card-bd {
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .strip {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 10px;
      }
      .stat {
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        padding: 8px 10px;
        background: var(--surface-2);
      }
      .stat.bad {
        border-color: var(--acc-short);
        background: var(--acc-short-soft);
      }
      .k {
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-3);
      }
      .v {
        font-size: var(--fs-16);
        color: var(--text);
      }
      .v.src {
        font-size: var(--fs-12);
        overflow-wrap: anywhere;
      }
      .sub {
        font-size: var(--fs-11);
        color: var(--text-3);
      }
      .mono {
        font-family: var(--font-mono);
        letter-spacing: var(--tracking-mono);
      }
      .sm {
        font-size: var(--fs-11);
      }
      .caveat {
        margin: 0;
        font-size: var(--fs-11);
        color: var(--text-2);
      }
      .caveat.subtle {
        color: var(--text-3);
      }
      .muted {
        margin: 0;
        font-size: var(--fs-12);
        color: var(--text-3);
      }
      .warnings {
        margin: 0;
        padding: 8px 12px 8px 28px;
        border: 1px solid var(--acc-hold);
        background: var(--acc-hold-soft);
        border-radius: var(--r-6);
        font-size: var(--fs-12);
        color: var(--acc-hold-fg);
      }
      .tbl-wrap {
        overflow-x: auto;
      }
      td a {
        color: var(--acc-info-fg);
        text-decoration: underline;
      }
    `,
  ],
})
export class SchedulerHealthComponent implements OnInit {
  readonly store = inject(FundStore);

  readonly health = this.store.scheduler;
  readonly autopilots = computed(() => this.store.scheduler()?.autopilots ?? []);
  readonly warnings = computed(() => this.store.scheduler()?.warnings ?? []);

  /** `beat_alive === null` means unknown — never "dead". */
  readonly beatUnknown = computed(() => {
    const h = this.store.scheduler();
    return !!h?.available && (h.beat_alive === null || h.beat_alive === undefined);
  });

  readonly beatLabel = computed(() => {
    const h = this.store.scheduler();
    if (!h || !h.available) return 'n/a';
    if (h.beat_alive === true) return 'alive';
    if (h.beat_alive === false) return 'not dispatching';
    return 'unknown';
  });

  readonly beatPillClass = computed(() => {
    const h = this.store.scheduler();
    if (!h || !h.available) return '';
    if (h.beat_alive === true) return 'ok';
    if (h.beat_alive === false) return 'err';
    return 'warn';
  });

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.store.loadSchedulerHealth().subscribe();
  }

  dur(seconds: number | null | undefined): string {
    return durationLabel(seconds);
  }
}
