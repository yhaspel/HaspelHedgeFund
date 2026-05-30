import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ApiClient } from '../../core/api/api-client';
import { AppShellComponent } from '../shared/app-shell.component';

interface Schedule {
  id: number;
  name: string;
  watchlist: number;
  watchlist_name: string;
  model_preset: string;
  cron_expression: string;
  timezone: string;
  is_market_aware: boolean;
  cost_ceiling_usd: string | null;
  on_breach: string;
  notification_channel: number | null;
  is_active: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
}
interface Watchlist { id: number; name: string; is_default: boolean; }
interface Channel { id: number; kind: string; label?: string; name: string; }
interface HistoryRow {
  id: number;
  fire_time_utc: string;
  status: string;
  notified_count: number;
  estimated_cost_usd: string | null;
  degraded_preset: string;
  error: string;
  run_ids: number[];
}

const CRON_PRESETS = [
  { label: 'Every weekday at 9:25am ET (pre-open)', cron: '25 9 * * 1-5' },
  { label: 'Every weekday at 4:05pm ET (post-close)', cron: '5 16 * * 1-5' },
  { label: 'Every day at 7:00am', cron: '0 7 * * *' },
  { label: 'Every Sunday at 5:00pm', cron: '0 17 * * 0' },
  { label: 'Custom…', cron: '' },
];

@Component({
  selector: 'hf-schedules-page',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Schedules' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Automation</div>
          <h1 class="mt-1.5">Scheduled runs</h1>
          <p class="sub">
            Run a watchlist on a cron schedule. You're notified only when
            something material changes (signal flip, big confidence move, a
            first-time risk veto, or unanimous bullishness).
          </p>
        </div>
        <button class="btn primary" (click)="toggleForm()">
          {{ showForm() ? 'Cancel' : '+ New schedule' }}
        </button>
      </div>

      @if (error()) { <p class="alert" role="alert">{{ error() }}</p> }

      @if (showForm()) {
        <section class="card form">
          <div class="card-hd"><h2 class="title">New scheduled run</h2></div>
          <div class="grid">
            <label>Name<input [(ngModel)]="f.name" placeholder="Daily quality check" /></label>
            <label>Watchlist
              <select [(ngModel)]="f.watchlist">
                @for (w of watchlists(); track w.id) {
                  <option [ngValue]="w.id">{{ w.name }}{{ w.is_default ? ' (default)' : '' }}</option>
                }
              </select>
            </label>
            <label>When
              <select [ngModel]="cronChoice()" (ngModelChange)="onCronChoice($event)">
                @for (p of cronPresets; track p.label) {
                  <option [ngValue]="p.label">{{ p.label }}</option>
                }
              </select>
            </label>
            <label>Cron expression
              <input [(ngModel)]="f.cron_expression" placeholder="25 9 * * 1-5" />
            </label>
            <label>Model preset
              <select [(ngModel)]="f.model_preset">
                <option value="hybrid">hybrid</option>
                <option value="research">research</option>
                <option value="quality">quality</option>
                <option value="frugal">frugal</option>
                <option value="dev">dev</option>
              </select>
            </label>
            <label>Cost ceiling (USD / run)
              <input type="number" step="0.01" [(ngModel)]="f.cost_ceiling_usd" placeholder="2.00" />
            </label>
            <label>On breach
              <select [(ngModel)]="f.on_breach">
                <option value="degrade">Degrade to cheaper preset</option>
                <option value="skip">Skip the run</option>
                <option value="notify_only">Run anyway, notify</option>
              </select>
            </label>
            <label>Notify via
              <select [(ngModel)]="f.notification_channel">
                <option [ngValue]="null">— none —</option>
                @for (c of channels(); track c.id) {
                  <option [ngValue]="c.id">{{ c.label || c.name || c.kind }}</option>
                }
              </select>
            </label>
            <label class="chk">
              <input type="checkbox" [(ngModel)]="f.is_market_aware" />
              Skip NYSE holidays / weekends (market-aware)
            </label>
          </div>
          <div class="form-actions">
            <button class="btn primary" (click)="create()" [disabled]="busy()">Create schedule</button>
          </div>
        </section>
      }

      <section class="card">
        <div class="card-hd"><h2 class="title">Your schedules</h2></div>
        @if (schedules().length === 0) {
          <p class="empty">No schedules yet. Create one to run a watchlist automatically.</p>
        } @else {
          <div class="tbl-scroll">
            <table class="tbl">
              <thead>
                <tr>
                  <th>Name</th><th>Watchlist</th><th>Cron</th><th>Preset</th>
                  <th>Next run</th><th>Active</th><th></th>
                </tr>
              </thead>
              <tbody>
                @for (s of schedules(); track s.id) {
                  <tr>
                    <td>{{ s.name }}</td>
                    <td class="muted">{{ s.watchlist_name }}</td>
                    <td><code>{{ s.cron_expression }}</code></td>
                    <td class="muted">{{ s.model_preset }}</td>
                    <td class="muted">{{ s.next_run_at ? (s.next_run_at | date: 'short') : '—' }}</td>
                    <td>
                      <button class="badge" [class.on]="s.is_active" (click)="toggleActive(s)">
                        {{ s.is_active ? 'active' : 'paused' }}
                      </button>
                    </td>
                    <td class="actions">
                      <button class="btn sm" (click)="runNow(s)">Run now</button>
                      <button class="btn sm ghost" (click)="loadHistory(s)">History</button>
                      <button class="btn sm danger" (click)="remove(s)">Delete</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </section>

      @if (historyFor()) {
        <section class="card">
          <div class="card-hd">
            <h2 class="title">History — {{ historyFor()?.name }}</h2>
            <button class="btn sm ghost" (click)="historyFor.set(null)">Close</button>
          </div>
          @if (history().length === 0) {
            <p class="empty">No fires yet.</p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr><th>Fired</th><th>Status</th><th class="r">Runs</th><th class="r">Notified</th><th class="r">Est $</th><th>Note</th></tr>
                </thead>
                <tbody>
                  @for (h of history(); track h.id) {
                    <tr>
                      <td class="muted">{{ h.fire_time_utc | date: 'short' }}</td>
                      <td>{{ h.status }}</td>
                      <td class="r">{{ h.run_ids.length }}</td>
                      <td class="r">{{ h.notified_count }}</td>
                      <td class="r">{{ h.estimated_cost_usd ?? '—' }}</td>
                      <td class="muted">{{ h.degraded_preset ? ('degraded from ' + h.degraded_preset) : (h.error || '') }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:16px; }
      .sub { color: var(--text-3); font-size:12.5px; margin-top:4px; max-width:640px; }
      .card { margin-bottom:16px; }
      .form .grid { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:12px; }
      .form label { display:flex; flex-direction:column; gap:4px; font-size:12px; color: var(--text-3); }
      .form input, .form select { padding:7px 10px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text-1); border:1px solid var(--border); }
      .form .chk { flex-direction:row; align-items:center; gap:8px; }
      .form-actions { margin-top:14px; }
      .empty { color: var(--text-3); font-size:13px; padding:8px 2px; }
      .alert { padding:10px 14px; background: var(--acc-short-soft); color: var(--acc-short-fg); border-radius: var(--r-6); margin:0 0 12px; }
      .tbl .r { text-align:right; } .tbl .muted { color: var(--text-3); }
      .actions { display:flex; gap:6px; }
      .badge { font-size:11px; padding:2px 8px; border-radius:8px; background: var(--surface-3); color: var(--text-3); border:none; cursor:pointer; }
      .badge.on { background: var(--acc-long-soft); color: var(--acc-long-fg); }
    `,
  ],
})
export class SchedulesPage implements OnInit {
  private readonly api = inject(ApiClient);
  readonly cronPresets = CRON_PRESETS;

  readonly schedules = signal<Schedule[]>([]);
  readonly watchlists = signal<Watchlist[]>([]);
  readonly channels = signal<Channel[]>([]);
  readonly showForm = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly cronChoice = signal(CRON_PRESETS[0].label);
  readonly historyFor = signal<Schedule | null>(null);
  readonly history = signal<HistoryRow[]>([]);

  f = {
    name: '',
    watchlist: null as number | null,
    cron_expression: CRON_PRESETS[0].cron,
    model_preset: 'hybrid',
    cost_ceiling_usd: '' as string,
    on_breach: 'degrade',
    notification_channel: null as number | null,
    is_market_aware: true,
  };

  ngOnInit(): void {
    this.reload();
    this.api.get<Watchlist[]>('/watchlists/').subscribe((w) => {
      this.watchlists.set(w);
      const def = w.find((x) => x.is_default) ?? w[0];
      if (def) this.f.watchlist = def.id;
    });
    this.api.get<Channel[]>('/notification-channels/').subscribe((c) => this.channels.set(c));
  }

  reload(): void {
    this.api.get<Schedule[]>('/scheduled-runs/').subscribe((s) => this.schedules.set(s));
  }

  toggleForm(): void {
    this.showForm.update((v) => !v);
  }

  onCronChoice(label: string): void {
    this.cronChoice.set(label);
    const p = CRON_PRESETS.find((x) => x.label === label);
    if (p && p.cron) this.f.cron_expression = p.cron;
  }

  create(): void {
    this.error.set(null);
    this.busy.set(true);
    const body: Record<string, unknown> = {
      name: this.f.name,
      watchlist: this.f.watchlist,
      cron_expression: this.f.cron_expression,
      model_preset: this.f.model_preset,
      on_breach: this.f.on_breach,
      is_market_aware: this.f.is_market_aware,
      notification_channel: this.f.notification_channel,
    };
    if (this.f.cost_ceiling_usd) body['cost_ceiling_usd'] = this.f.cost_ceiling_usd;
    this.api.post('/scheduled-runs/', body).subscribe({
      next: () => {
        this.busy.set(false);
        this.showForm.set(false);
        this.reload();
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(this.errMsg(e));
      },
    });
  }

  runNow(s: Schedule): void {
    this.api.post(`/scheduled-runs/${s.id}/run-now/`, {}).subscribe({
      next: () => this.loadHistory(s),
      error: (e) => this.error.set(this.errMsg(e)),
    });
  }

  toggleActive(s: Schedule): void {
    this.api
      .patch(`/scheduled-runs/${s.id}/`, { is_active: !s.is_active })
      .subscribe(() => this.reload());
  }

  remove(s: Schedule): void {
    if (!confirm(`Delete schedule "${s.name}"?`)) return;
    this.api.delete(`/scheduled-runs/${s.id}/`).subscribe(() => this.reload());
  }

  loadHistory(s: Schedule): void {
    this.historyFor.set(s);
    this.api
      .get<HistoryRow[]>(`/scheduled-runs/${s.id}/history/`)
      .subscribe((h) => this.history.set(h));
  }

  private errMsg(e: unknown): string {
    const err = e as { error?: { detail?: string; [k: string]: unknown }; message?: string };
    if (err?.error?.detail) return String(err.error.detail);
    if (err?.error && typeof err.error === 'object') return JSON.stringify(err.error);
    return err?.message ?? 'Request failed';
  }
}
