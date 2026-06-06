import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ApiClient } from '../../core/api/api-client';
import { GraphsStore } from '../../abstraction/graphs.store';
import { AppShellComponent } from '../shared/app-shell.component';

interface Schedule {
  id: number;
  name: string;
  watchlist: number;
  watchlist_name: string;
  model_preset: string;
  cron_expression: string;
  cron_description: string;
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
interface BrokerAccount {
  id: number;
  broker: string;
  mode: string;
  label: string;
  connection_status: string;
  is_active: boolean;
}
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

type Freq = 'weekdays' | 'daily' | 'weekly' | 'custom';

const DOWS = [
  { value: 1, label: 'Mon' },
  { value: 2, label: 'Tue' },
  { value: 3, label: 'Wed' },
  { value: 4, label: 'Thu' },
  { value: 5, label: 'Fri' },
  { value: 6, label: 'Sat' },
  { value: 0, label: 'Sun' },
];
const DOW_NAMES: Record<number, string> = {
  0: 'Sunday', 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday',
  4: 'Thursday', 5: 'Friday', 6: 'Saturday',
};

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
            Run a watchlist on a schedule. You're notified only when something
            material changes (signal flip, big confidence move, a first-time risk
            veto, or unanimous bullishness).
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

            <label>Frequency
              <select [(ngModel)]="freq" (ngModelChange)="rebuildCron()">
                <option value="weekdays">Every weekday (Mon–Fri)</option>
                <option value="daily">Every day</option>
                <option value="weekly">Specific days of the week</option>
                <option value="custom">Custom (advanced)</option>
              </select>
            </label>
            @if (freq !== 'custom') {
              <label>Time of day <span class="hint">(US Eastern)</span>
                <input type="time" [(ngModel)]="timeOfDay" (ngModelChange)="rebuildCron()" />
              </label>
            }

            @if (freq === 'weekly') {
              <div class="span2 dow">
                <span class="dow-label">On which days?</span>
                <div class="dow-grid">
                  @for (d of dows; track d.value) {
                    <label class="dow-chk" [class.sel]="selectedDows().includes(d.value)">
                      <input type="checkbox"
                        [checked]="selectedDows().includes(d.value)"
                        (change)="toggleDow(d.value)" />
                      {{ d.label }}
                    </label>
                  }
                </div>
              </div>
            }

            @if (freq === 'custom') {
              <div class="span2 cron-fields">
                <label>Minute <span class="hint">0–59</span>
                  <input [(ngModel)]="cf.min" (ngModelChange)="rebuildCron()" placeholder="*" /></label>
                <label>Hour <span class="hint">0–23</span>
                  <input [(ngModel)]="cf.hour" (ngModelChange)="rebuildCron()" placeholder="*" /></label>
                <label>Day of month <span class="hint">1–31</span>
                  <input [(ngModel)]="cf.dom" (ngModelChange)="rebuildCron()" placeholder="*" /></label>
                <label>Month <span class="hint">1–12</span>
                  <input [(ngModel)]="cf.mon" (ngModelChange)="rebuildCron()" placeholder="*" /></label>
                <label>Day of week <span class="hint">0=Sun … 6=Sat</span>
                  <input [(ngModel)]="cf.dow" (ngModelChange)="rebuildCron()" placeholder="*" /></label>
              </div>
            }

            <div class="span2 cron-preview">
              <span class="cron-summary">{{ cronSummary() }}</span>
              <code class="cron-raw" title="Resolved cron expression">{{ f.cron_expression }}</code>
            </div>

            <label>Model preset
              <select [(ngModel)]="f.model_preset">
                <option value="hybrid">hybrid</option>
                <option value="research">research</option>
                <option value="quality">quality</option>
                <option value="frugal">frugal</option>
                <option value="dev">dev</option>
              </select>
            </label>

            <label>Agent graph
              <select [(ngModel)]="f.graph_version_id" data-testid="sched-graph-select">
                <option [ngValue]="null">Council classic (default)</option>
                @for (g of graphOptions(); track g.versionId) {
                  <option [ngValue]="g.versionId">{{ g.label }}</option>
                }
              </select>
              @if (f.graph_version_id) {
                <span class="muted text-[11px]">Models &amp; personas come from this graph; the preset still sets the cost tier.</span>
              }
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
            <label class="chk span2">
              <input type="checkbox" [(ngModel)]="f.is_market_aware" />
              Skip NYSE holidays / weekends (market-aware)
            </label>
            <label class="chk span2">
              <input type="checkbox" [(ngModel)]="f.auto_paper_submit" />
              Auto-submit paper orders from each run's decision
            </label>
            @if (f.auto_paper_submit) {
              <label>Paper account
                <select [(ngModel)]="f.auto_submit_broker_account">
                  <option [ngValue]="null">— select a paper account —</option>
                  @for (a of brokerAccounts(); track a.id) {
                    @if (a.mode === 'paper' && a.is_active) {
                      <option [ngValue]="a.id">{{ a.label || a.broker }} ({{ a.broker }})</option>
                    }
                  }
                </select>
              </label>
              <label>Max orders / day
                <input type="number" min="1" [(ngModel)]="f.max_orders_per_day" />
              </label>
              <label>Max notional / day ($)
                <input type="number" min="0" [(ngModel)]="f.max_notional_per_day_usd" />
              </label>
              <label class="chk span2">
                <input type="checkbox" [(ngModel)]="f.auto_submit_draft_only" />
                Draft only — create orders but don't fill (review + confirm in the UI)
              </label>
              <p class="hint span2">
                Paper accounts only — live accounts are hard-blocked. Orders are
                confirmed via the scheduled-job gate and filled unless “draft only”.
              </p>
            }
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
                  <th>Name</th><th>Watchlist</th><th>Schedule</th><th>Preset</th>
                  <th>Next run</th><th>Status</th><th></th>
                </tr>
              </thead>
              <tbody>
                @for (s of schedules(); track s.id) {
                  <tr [class.paused]="!s.is_active">
                    <td>{{ s.name }}</td>
                    <td class="muted">{{ s.watchlist_name }}</td>
                    <td>
                      <div class="cron-h">{{ s.cron_description || s.cron_expression }}</div>
                      <code class="cron-raw" [title]="s.cron_expression">{{ s.cron_expression }}</code>
                    </td>
                    <td class="muted">{{ s.model_preset }}</td>
                    <td class="muted">{{ s.is_active && s.next_run_at ? (s.next_run_at | date: 'short') : '—' }}</td>
                    <td>
                      <span class="pill" [class.on]="s.is_active">{{ s.is_active ? 'Active' : 'Paused' }}</span>
                    </td>
                    <td class="actions">
                      <button class="btn sm" (click)="toggleActive(s)"
                        [attr.aria-label]="(s.is_active ? 'Pause' : 'Resume') + ' ' + s.name">
                        {{ s.is_active ? 'Pause' : 'Resume' }}
                      </button>
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
      .form input, .form select { padding:7px 10px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text); border:1px solid var(--border); }
      .form .chk { flex-direction:row; align-items:center; gap:8px; }
      .form .span2 { grid-column: 1 / -1; }
      .hint { color: var(--text-3); font-weight:400; opacity:.8; }
      .dow { display:flex; flex-direction:column; gap:6px; }
      .dow-label { font-size:12px; color: var(--text-3); }
      .dow-grid { display:flex; flex-wrap:wrap; gap:6px; }
      .dow-chk { flex-direction:row !important; align-items:center; gap:5px; padding:5px 10px; border:1px solid var(--border); border-radius:14px; cursor:pointer; font-size:12px; color: var(--text-2); }
      .dow-chk.sel { background: var(--acc-info-soft); color: var(--acc-info-fg); border-color: var(--acc-info); }
      .cron-fields { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:8px; }
      .cron-preview { display:flex; align-items:center; gap:10px; padding:8px 12px; background: var(--surface-2); border-radius: var(--r-6); }
      .cron-summary { font-size:13px; color: var(--text); font-weight:500; }
      .cron-raw { font-size:11px; color: var(--text-3); background: var(--surface-3); padding:2px 6px; border-radius:4px; }
      .form-actions { margin-top:14px; }
      .empty { color: var(--text-3); font-size:13px; padding:8px 2px; }
      .alert { padding:10px 14px; background: var(--acc-short-soft); color: var(--acc-short-fg); border-radius: var(--r-6); margin:0 0 12px; }
      .tbl .r { text-align:right; } .tbl .muted { color: var(--text-3); }
      .tbl tr.paused td { opacity:.6; }
      .tbl .cron-h { font-size:12.5px; color: var(--text); }
      .tbl .cron-raw { font-size:10.5px; color: var(--text-3); }
      .actions { display:flex; gap:6px; flex-wrap:wrap; }
      .pill { font-size:11px; padding:2px 9px; border-radius:10px; background: var(--surface-3); color: var(--text-3); }
      .pill.on { background: var(--acc-long-soft); color: var(--acc-long-fg); }
    `,
  ],
})
export class SchedulesPage implements OnInit {
  private readonly api = inject(ApiClient);
  private readonly graphs = inject(GraphsStore);
  readonly dows = DOWS;

  readonly graphOptions = computed(() =>
    this.graphs
      .graphs()
      .filter((g) => g.latest_version && g.latest_version.validation_status === 'valid')
      .map((g) => ({
        versionId: g.latest_version!.id,
        label: g.name + (g.is_template ? ' (template)' : ''),
      })),
  );

  readonly schedules = signal<Schedule[]>([]);
  readonly watchlists = signal<Watchlist[]>([]);
  readonly channels = signal<Channel[]>([]);
  readonly brokerAccounts = signal<BrokerAccount[]>([]);
  readonly showForm = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly historyFor = signal<Schedule | null>(null);
  readonly history = signal<HistoryRow[]>([]);

  // schedule builder state
  freq: Freq = 'weekdays';
  timeOfDay = '09:25';
  readonly selectedDows = signal<number[]>([1]);
  cf = { min: '0', hour: '9', dom: '*', mon: '*', dow: '*' };

  f = {
    name: '',
    watchlist: null as number | null,
    cron_expression: '25 9 * * 1-5',
    model_preset: 'frugal',
    graph_version_id: null as number | null,
    cost_ceiling_usd: '' as string,
    on_breach: 'degrade',
    notification_channel: null as number | null,
    is_market_aware: true,
    auto_paper_submit: false,
    auto_submit_broker_account: null as number | null,
    auto_submit_draft_only: false,
    max_orders_per_day: 10,
    max_notional_per_day_usd: '10000' as string,
  };

  ngOnInit(): void {
    this.reload();
    this.graphs.loadGraphs().subscribe();
    this.api.get<Watchlist[]>('/watchlists/').subscribe((w) => {
      this.watchlists.set(w);
      const def = w.find((x) => x.is_default) ?? w[0];
      if (def) this.f.watchlist = def.id;
    });
    this.api.get<Channel[]>('/notification-channels/').subscribe((c) => this.channels.set(c));
    this.api
      .get<BrokerAccount[]>('/broker-accounts/')
      .subscribe((a) => this.brokerAccounts.set(a ?? []));
    this.rebuildCron();
  }

  reload(): void {
    this.api.get<Schedule[]>('/scheduled-runs/').subscribe((s) => this.schedules.set(s));
  }

  toggleForm(): void {
    this.showForm.update((v) => !v);
  }

  // ----- schedule builder -----

  toggleDow(value: number): void {
    this.selectedDows.update((days) =>
      days.includes(value) ? days.filter((d) => d !== value) : [...days, value],
    );
    this.rebuildCron();
  }

  private hm(): { min: string; hour: string } {
    const [h, m] = (this.timeOfDay || '09:25').split(':');
    return { min: String(parseInt(m, 10) || 0), hour: String(parseInt(h, 10) || 0) };
  }

  rebuildCron(): void {
    const { min, hour } = this.hm();
    if (this.freq === 'weekdays') {
      this.f.cron_expression = `${min} ${hour} * * 1-5`;
    } else if (this.freq === 'daily') {
      this.f.cron_expression = `${min} ${hour} * * *`;
    } else if (this.freq === 'weekly') {
      const days = [...this.selectedDows()].sort((a, b) => a - b).join(',') || '*';
      this.f.cron_expression = `${min} ${hour} * * ${days}`;
    } else {
      const c = this.cf;
      this.f.cron_expression =
        `${c.min || '*'} ${c.hour || '*'} ${c.dom || '*'} ${c.mon || '*'} ${c.dow || '*'}`;
    }
  }

  private fmtTime(t: string): string {
    const [h, m] = (t || '09:25').split(':').map((x) => parseInt(x, 10));
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 === 0 ? 12 : h % 12;
    return `${h12}:${String(m).padStart(2, '0')} ${ampm}`;
  }

  cronSummary(): string {
    const t = this.fmtTime(this.timeOfDay);
    if (this.freq === 'weekdays') return `At ${t}, Monday through Friday (US Eastern)`;
    if (this.freq === 'daily') return `Every day at ${t} (US Eastern)`;
    if (this.freq === 'weekly') {
      const names = [...this.selectedDows()].sort((a, b) => a - b).map((d) => DOW_NAMES[d]);
      return names.length
        ? `At ${t}, on ${names.join(', ')} (US Eastern)`
        : 'Pick at least one day of the week';
    }
    return `Custom cron — ${this.f.cron_expression}`;
  }

  // ----- actions -----

  create(): void {
    this.error.set(null);
    this.busy.set(true);
    const body: Record<string, unknown> = {
      name: this.f.name,
      watchlist: this.f.watchlist,
      cron_expression: this.f.cron_expression,
      model_preset: this.f.model_preset,
      graph_version_id: this.f.graph_version_id,
      on_breach: this.f.on_breach,
      is_market_aware: this.f.is_market_aware,
      notification_channel: this.f.notification_channel,
      auto_paper_submit: this.f.auto_paper_submit,
    };
    if (this.f.cost_ceiling_usd) body['cost_ceiling_usd'] = this.f.cost_ceiling_usd;
    if (this.f.auto_paper_submit) {
      body['auto_submit_broker_account'] = this.f.auto_submit_broker_account;
      body['auto_submit_draft_only'] = this.f.auto_submit_draft_only;
      body['max_orders_per_day'] = this.f.max_orders_per_day;
      body['max_notional_per_day_usd'] = this.f.max_notional_per_day_usd;
    }
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
