import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FundStore } from '../../abstraction/fund.store';
import { ModelsStore } from '../../abstraction/models.store';
import { Autopilot } from '../../core/models/autopilot.model';
import { AppShellComponent } from '../shared/app-shell.component';
import { PopoverComponent } from '../shared/popover.component';

// P7 §14 — per-strategy autopilot panel: the validation-gate checklist that
// unlocks the enable toggle, a prominent state chip, the guardrail config, and
// the run-now / resume controls. Paper-only; carries the disclaimer.
@Component({
  selector: 'hf-autopilot-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent, PopoverComponent],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Fund', link: '/fund' }, { label: 'Autopilot' }]">
      <div class="page-head">
        <div><h1>Autopilot</h1></div>
        <div class="head-actions" *ngIf="ap() as a">
          <!-- Enabled: a plain status pill. -->
          <span
            *ngIf="a.is_enabled"
            class="pill"
            [class.ok]="a.state === 'active'"
            [class.warn]="a.state === 'soft_cut'"
            [class.err]="a.state === 'halted'"
          ><span class="dot"></span>{{ a.state }}</span>
          <!-- Disabled: the pill explains *why* it's off + the next step on hover / focus / tap. -->
          <span class="pill-wrap" *ngIf="!a.is_enabled">
            <button
              type="button"
              class="pill info pill-btn"
              (mouseenter)="pop.show()"
              (mouseleave)="pop.maybeHide()"
              (focus)="pop.show()"
              (blur)="pop.maybeHide()"
              (click)="pop.toggle()"
              [attr.aria-label]="'Disabled — ' + disabledHint(a)"
              [attr.aria-describedby]="pop.open() ? pop.popoverId : null"
            ><span class="dot"></span>disabled<span class="pill-q" aria-hidden="true">?</span></button>
            <hf-popover #pop role="tooltip" placement="bottom" align="end" [dismissOnOutsideClick]="true">
              <span class="disabled-pop">
                <b>Why “disabled”?</b>
                {{ disabledHint(a) }}
              </span>
            </hf-popover>
          </span>
        </div>
      </div>

      <p class="disclaimer">Educational use only — not investment advice. Paper trading only.</p>

      <ng-container *ngIf="ap() as a">
        <!-- Validation-gate checklist -->
        <section class="card">
          <h2>Validation gate (§9)</h2>
          <ul class="checklist">
            <li *ngFor="let c of a.validation.checks" [class.ok]="c.ok" [class.bad]="!c.ok">
              <span class="mark">{{ c.ok ? '✓' : '✕' }}</span>{{ c.detail }}
            </li>
          </ul>
          <p class="muted" *ngIf="!a.validation.passed">
            Run a passing walk-forward backtest (positive OOS Sharpe + max DD within the
            hard-halt limit) for this strategy to unlock the enable toggle.
          </p>
        </section>

        <!-- Controls -->
        <section class="card controls">
          <!-- Gate not passed → the next step is a (strategy-linked) backtest, not a
               dead-ended grey button. Completing it unlocks Enable. -->
          <a
            *ngIf="!a.is_enabled && !a.validation.passed"
            class="btn btn-primary"
            [routerLink]="['/backtests/new']"
            [queryParams]="{ strategy: strategyId }"
          >Run validation backtest →</a>
          <button
            *ngIf="!a.is_enabled"
            class="btn"
            [class.btn-primary]="a.validation.passed"
            [disabled]="!a.validation.passed || busy()"
            [title]="a.validation.passed ? '' : 'Validation gate not passed — run a validation backtest first'"
            (click)="enable()"
          >Enable autopilot</button>
          <button *ngIf="a.is_enabled" class="btn" (click)="disable()" [disabled]="busy()">Disable</button>
          <button
            *ngIf="a.state === 'halted'"
            class="btn"
            (click)="resume()"
            [disabled]="busy()"
          >Resume (un-halt)</button>
          <button *ngIf="a.is_enabled" class="btn" (click)="runNow()" [disabled]="busy()">Run now</button>
          <span class="next" *ngIf="a.next_run_at">Next run: {{ a.next_run_at | date: 'EEE MMM d, HH:mm' }}</span>
          <span class="notice" *ngIf="notice()">{{ notice() }}</span>
        </section>

        <!-- Schedule: human-friendly recurrence builder; raw cron behind Advanced. -->
        <section class="card">
          <div class="sched-head">
            <h2>Schedule</h2>
            <div class="seg" role="group" aria-label="Schedule editor mode">
              <button type="button" class="opt" [class.on]="scheduleMode === 'simple'" (click)="setMode('simple')">Simple</button>
              <button type="button" class="opt" [class.on]="scheduleMode === 'advanced'" (click)="setMode('advanced')">Advanced (cron)</button>
            </div>
          </div>

          <div *ngIf="scheduleMode === 'simple'">
            <div class="sched-row">
              <label class="fld">Frequency
                <select class="input sans" [(ngModel)]="schedFreq" (ngModelChange)="syncCron()">
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </label>
              <label class="fld">Time <small>({{ a.timezone }})</small>
                <input class="input" type="time" [(ngModel)]="schedTime" (ngModelChange)="syncCron()" />
              </label>
              <label class="fld" *ngIf="schedFreq === 'monthly'">Day of month
                <input class="input" type="number" min="1" max="31" [(ngModel)]="schedDom" (ngModelChange)="syncCron()" />
              </label>
            </div>

            <div class="days" *ngIf="schedFreq === 'weekly'" role="group" aria-label="Days of week">
              <button type="button" class="daychip" *ngFor="let d of weekdays"
                [class.on]="schedDays.includes(d.dow)" [attr.aria-pressed]="schedDays.includes(d.dow)"
                (click)="toggleDay(d.dow)">{{ d.label }}</button>
            </div>

            <p class="preview">{{ schedulePreview() }}</p>
          </div>

          <div *ngIf="scheduleMode === 'advanced'" class="sched-adv">
            <label class="fld">Cron expression
              <input class="input" [(ngModel)]="form.cron_expression" />
            </label>
            <small class="hint" *ngIf="a.cron_description">{{ a.cron_description }} · {{ a.timezone }}</small>
            <small class="hint">minute hour day-of-month month day-of-week — e.g. <code>30 16 * * 5</code> = Fri 16:30.</small>
          </div>

          <button class="btn" (click)="save()" [disabled]="busy() || !scheduleValid()">Save changes</button>
        </section>

        <!-- Guardrail config -->
        <section class="card">
          <h2>Guardrails</h2>
          <div class="grid">
            <label>Model preset
              <select class="input sans" [(ngModel)]="form.model_preset" (ngModelChange)="onPresetChange()">
                <option value="dev">dev</option>
                <option value="frugal">frugal</option>
                <option value="hybrid">hybrid</option>
                <option value="research">research</option>
                <option value="quality">quality</option>
              </select>
              <small class="hint">Governs autonomous runs for this account only — independent of your
                <a routerLink="/settings/models">Settings → Models</a> defaults
                (those apply to manual runs &amp; the questionnaire).</small>
            </label>
            <label>Target vol % <input class="input" type="number" [(ngModel)]="form.target_vol_pct" /></label>
            <label>Soft cut % <input class="input" type="number" [(ngModel)]="form.dd_soft_cut_pct" /></label>
            <label>Hard halt % <input class="input" type="number" [(ngModel)]="form.dd_hard_halt_pct" /></label>
            <label>Max orders/day <input class="input" type="number" [(ngModel)]="form.max_orders_per_day" /></label>
            <label>Liquidity ADV cap % <input class="input" type="number" [(ngModel)]="form.liquidity_adv_cap_pct" /></label>
            <label class="chk"><input type="checkbox" [(ngModel)]="form.flatten_on_halt" /> Flatten to cash on hard halt</label>
          </div>

          <!-- Which actual model(s) this account's autonomous runs use. -->
          <div class="council" *ngIf="councilModels().length">
            <div class="council-hd">Models this account's runs use · preset <b>{{ form.model_preset }}</b></div>
            <ul class="council-list">
              <li *ngFor="let m of councilModels()">
                <span class="cm-roles">{{ m.roles.join(' · ') }}</span>
                <span class="cm-arrow">→</span>
                <span class="cm-name">{{ m.name }}</span>
                <span class="cm-tier" *ngIf="m.tier">· {{ m.tier }}</span>
              </li>
            </ul>
            <p class="council-note">
              Different roles can use different models, and personas are spread across several for
              diversity — this is what the <b>{{ form.model_preset }}</b> preset resolves to. Autonomous
              runs use this preset only, independent of your
              <a routerLink="/settings/models">Settings → Models</a> defaults.
            </p>
          </div>

          <button class="btn" (click)="save()" [disabled]="busy() || !scheduleValid()">Save changes</button>
        </section>
      </ng-container>
    </hf-app-shell>
  `,
  styles: [`
    /* Section cards hold content directly (no .card-bd), so the shell .card has no
       inner padding and no separation — restore comfortable padding + gaps. */
    section.card { padding: 18px 20px; margin-bottom: 16px; }
    section.card > h2 { margin: 0 0 14px; }
    .pill-wrap { position: relative; display: inline-flex; }
    button.pill-btn { font-family: inherit; line-height: 1; cursor: help; -webkit-appearance: none; appearance: none; }
    button.pill-btn:focus-visible { outline: none; box-shadow: var(--focus-ring); }
    .pill-btn .pill-q {
      display: inline-flex; align-items: center; justify-content: center;
      width: 12px; height: 12px; margin-left: 1px; border-radius: var(--r-full);
      border: 1px solid currentColor; font-size: 8px; font-weight: 700; line-height: 1; opacity: 0.6;
    }
    .pill-btn:hover .pill-q, .pill-btn:focus-visible .pill-q { opacity: 1; }
    .disabled-pop { display: block; max-width: 240px; }
    .disabled-pop b { display: block; margin-bottom: 3px; }
    .checklist { list-style: none; padding: 0; }
    .checklist li { padding: 4px 0; }
    .checklist li .mark { display: inline-block; width: 20px; font-weight: 700; }
    .checklist li.ok .mark { color: var(--acc-long-fg); }
    .checklist li.bad .mark { color: var(--acc-short-fg); }
    .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .controls .next, .controls .notice { color: var(--text-3); font-size: 12px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-bottom: 12px; }
    .grid label { display: flex; flex-direction: column; font-size: 12px; color: var(--text-2); gap: 3px; }
    .grid label.chk { flex-direction: row; align-items: center; gap: 6px; }
    .grid label .hint { color: var(--text-3); font-size: 11px; font-weight: 400; }
    .grid label .hint a { text-decoration: underline; }
    .sched-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
    .sched-head h2 { margin: 0; }
    .sched-row { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 12px; }
    .fld { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-2); }
    .fld small { color: var(--text-3); font-weight: 400; }
    .fld .input { width: 160px; }
    .days { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .daychip { background: var(--surface-2); color: var(--text-2); border: 1px solid var(--border); border-radius: var(--r-full); padding: 5px 13px; font-size: 12px; cursor: pointer; transition: border-color var(--dur-fast); }
    .daychip:hover { border-color: var(--border-2); }
    .daychip.on { background: var(--acc-info-soft); color: var(--text); border-color: var(--acc-info); }
    .preview { color: var(--text-2); font-size: 12px; margin: 8px 0 12px; }
    .sched-adv { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; max-width: 380px; }
    .sched-adv .hint { color: var(--text-3); font-size: 11px; }
    .sched-adv code { font-family: var(--font-mono); }
    .council { margin: 4px 0 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--r-6); background: var(--surface-2); }
    .council-hd { font-size: 12px; font-weight: 600; color: var(--text-2); margin-bottom: 7px; }
    .council-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 5px; }
    .council-list li { display: flex; align-items: baseline; gap: 8px; font-size: 12px; flex-wrap: wrap; }
    .council-list .cm-roles { color: var(--text-3); min-width: 130px; }
    .council-list .cm-arrow { color: var(--text-3); }
    .council-list .cm-name { font-family: var(--font-mono); color: var(--text); }
    .council-list .cm-tier { color: var(--text-3); font-size: 11px; }
    .council-note { color: var(--text-3); font-size: 11px; margin: 9px 0 0; line-height: 1.45; }
    .council-note a { text-decoration: underline; }
  `],
})
export class AutopilotPanelPage implements OnInit {
  private readonly store = inject(FundStore);
  private readonly models = inject(ModelsStore);
  private readonly route = inject(ActivatedRoute);
  readonly ap = this.store.autopilot;
  readonly busy = signal(false);
  readonly notice = signal<string | null>(null);
  strategyId = 0;

  // Which actual model(s) this account's autonomous runs use, grouped by model
  // (the council assigns different models to different roles per preset).
  readonly councilModels = signal<{ name: string; tier: string; roles: string[] }[]>([]);
  private readonly ROLE_GROUP: Record<string, string> = {
    buffett: 'Personas', munger: 'Personas', graham: 'Personas', wood: 'Personas',
    druckenmiller: 'Personas', burry: 'Personas', damodaran: 'Personas', lynch: 'Personas',
    fundamentals: 'Analytical', technicals: 'Analytical', valuation: 'Analytical', sentiment: 'Analytical',
    macro: 'Macro', news_digest: 'News',
    risk_manager: 'Risk / PM / CIO', portfolio_manager: 'Risk / PM / CIO', cio: 'Risk / PM / CIO',
  };
  private readonly ROLE_ORDER = ['Personas', 'Analytical', 'Macro', 'News', 'Risk / PM / CIO', 'Other'];

  form: Partial<Autopilot> = {};

  // --- schedule builder (friendly alternative to the raw cron string) ---
  readonly weekdays = [
    { dow: 1, label: 'Mon' }, { dow: 2, label: 'Tue' }, { dow: 3, label: 'Wed' },
    { dow: 4, label: 'Thu' }, { dow: 5, label: 'Fri' }, { dow: 6, label: 'Sat' },
    { dow: 0, label: 'Sun' },
  ];
  scheduleMode: 'simple' | 'advanced' = 'simple';
  schedFreq: 'daily' | 'weekly' | 'monthly' = 'weekly';
  schedDays: number[] = [5];   // cron day-of-week (0/7 = Sun, normalised to 0)
  schedDom = 1;                // day of month (monthly)
  schedTime = '16:30';         // HH:MM in the account timezone

  ngOnInit(): void {
    this.strategyId = Number(this.route.snapshot.paramMap.get('id'));
    this.reload();
  }

  private reload(): void {
    this.store.loadAutopilot(this.strategyId).subscribe((r) => {
      const a = r?.autopilot;
      if (a) {
        this.form = {
          cron_expression: a.cron_expression,
          model_preset: a.model_preset,
          target_vol_pct: a.target_vol_pct,
          dd_soft_cut_pct: a.dd_soft_cut_pct,
          dd_hard_halt_pct: a.dd_hard_halt_pct,
          max_orders_per_day: a.max_orders_per_day,
          liquidity_adv_cap_pct: a.liquidity_adv_cap_pct,
          flatten_on_halt: a.flatten_on_halt,
        };
        // Parse the stored cron into the friendly builder; fall back to the raw
        // cron view when it's an expression the simple builder can't represent.
        this.scheduleMode = this.hydrateSchedule(a.cron_expression) ? 'simple' : 'advanced';
        this.refreshCouncil(a.model_preset);
      }
    });
  }

  // Resolve the preset → per-agent model map and group it by model so the user
  // can see exactly which model(s) this account's autonomous runs will use.
  onPresetChange(): void { this.refreshCouncil(this.form.model_preset ?? ''); }
  private refreshCouncil(preset: string): void {
    if (!preset) { this.councilModels.set([]); return; }
    if (this.models.models().length === 0) {
      this.models.loadModels().subscribe(() => this.buildCouncil(preset));
    } else {
      this.buildCouncil(preset);
    }
  }
  private buildCouncil(preset: string): void {
    this.models.fetchPreset(preset).subscribe((r) => {
      const overrides = (r?.overrides ?? {}) as Record<string, string>;
      const catalog = this.models.models();
      const byModel = new Map<string, Set<string>>();
      for (const [agent, mid] of Object.entries(overrides)) {
        if (agent.includes('*')) continue;            // skip any unexpanded wildcard
        const role = this.ROLE_GROUP[agent] ?? 'Other';
        (byModel.get(mid) ?? byModel.set(mid, new Set()).get(mid)!).add(role);
      }
      const out = [...byModel.entries()].map(([mid, roles]) => {
        const m = catalog.find((x) => x.id === mid);
        return {
          name: m?.display_name ?? this.shortModelId(mid),
          tier: m?.tier ?? '',
          roles: this.ROLE_ORDER.filter((x) => roles.has(x)),
        };
      });
      out.sort((a, b) => b.roles.length - a.roles.length);
      this.councilModels.set(out);
    });
  }
  private shortModelId(id: string): string {
    const slug = id.split(':').pop() ?? id;
    return slug.split('/').pop() ?? slug;
  }

  private run(obs: { subscribe: (h: { next: () => void; error: (e: unknown) => void }) => void }, msg: string): void {
    this.busy.set(true);
    this.notice.set(null);
    obs.subscribe({
      next: () => { this.busy.set(false); this.notice.set(msg); },
      error: (e: unknown) => { this.busy.set(false); this.notice.set(this.errMsg(e)); },
    });
  }

  private errMsg(e: unknown): string {
    const err = e as { error?: { detail?: string } };
    return err?.error?.detail ?? 'Request failed.';
  }

  enable(): void { this.run(this.store.enable(this.strategyId), 'Autopilot enabled.'); }
  disable(): void { this.run(this.store.disable(this.strategyId), 'Autopilot disabled.'); }
  resume(): void { this.run(this.store.resume(this.strategyId), 'Un-halted — re-checks drawdown on the next tick.'); }
  runNow(): void { this.run(this.store.runNow(this.strategyId), 'Cycle queued.'); }
  save(): void { this.run(this.store.saveAutopilot(this.strategyId, this.form), 'Saved.'); }

  // Copy for the disabled-state pill tooltip: why it's off + the one next step.
  disabledHint(a: Autopilot): string {
    return a.validation?.passed
      ? 'Validated — click “Enable autopilot” below.'
      : 'Run a validation backtest to unlock the enable toggle.';
  }

  // --- schedule builder helpers ---
  setMode(m: 'simple' | 'advanced'): void {
    // Switching to Simple re-parses the current cron so the builder reflects it.
    if (m === 'simple') this.hydrateSchedule(this.form.cron_expression || '');
    this.scheduleMode = m;
  }

  toggleDay(dow: number): void {
    this.schedDays = this.schedDays.includes(dow)
      ? this.schedDays.filter((d) => d !== dow)
      : [...this.schedDays, dow];
    this.syncCron();
  }

  // Rebuild the cron string from the simple fields (kept on form for save()).
  syncCron(): void {
    this.form.cron_expression = this.buildCron();
  }

  scheduleValid(): boolean {
    if (this.scheduleMode === 'advanced') return !!(this.form.cron_expression || '').trim();
    if (this.schedFreq === 'weekly') return this.schedDays.length > 0;
    if (this.schedFreq === 'monthly') return this.schedDom >= 1 && this.schedDom <= 31;
    return true;
  }

  schedulePreview(): string {
    const [h, m] = this.parseTime(this.schedTime);
    const t = `${this.pad(h)}:${this.pad(m)}`;
    const tz = this.ap()?.timezone || 'America/New_York';
    if (this.schedFreq === 'daily') return `Runs every day at ${t} (${tz}).`;
    if (this.schedFreq === 'monthly') return `Runs monthly on day ${this.schedDom || 1} at ${t} (${tz}).`;
    const names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const sel = [1, 2, 3, 4, 5, 6, 0].filter((d) => this.schedDays.includes(d)).map((d) => names[d]);
    return sel.length ? `Runs weekly on ${sel.join(', ')} at ${t} (${tz}).` : 'Pick at least one day to run.';
  }

  private buildCron(): string {
    const [h, m] = this.parseTime(this.schedTime);
    if (this.schedFreq === 'daily') return `${m} ${h} * * *`;
    if (this.schedFreq === 'monthly') return `${m} ${h} ${this.schedDom || 1} * *`;
    const days = [...this.schedDays].sort((a, b) => a - b);
    return `${m} ${h} * * ${days.length ? days.join(',') : '*'}`;
  }

  // Populate the simple fields from a cron string. Returns false when the cron
  // can't be represented by the simple builder (caller shows the Advanced view).
  private hydrateSchedule(cron: string): boolean {
    const parts = (cron || '').trim().split(/\s+/);
    if (parts.length !== 5) return false;
    const [min, hour, dom, mon, dow] = parts;
    const int = (s: string) => /^\d+$/.test(s);
    if (!int(min) || !int(hour) || mon !== '*') return false;
    const mi = +min, hr = +hour;
    if (mi > 59 || hr > 23) return false;
    this.schedTime = `${this.pad(hr)}:${this.pad(mi)}`;
    if (dom === '*' && dow === '*') { this.schedFreq = 'daily'; return true; }
    if (dom === '*' && /^[0-7](,[0-7])*$/.test(dow)) {
      this.schedFreq = 'weekly';
      this.schedDays = [...new Set(dow.split(',').map((d) => (d === '7' ? 0 : +d)))];
      return true;
    }
    if (int(dom) && +dom >= 1 && +dom <= 31 && dow === '*') {
      this.schedFreq = 'monthly';
      this.schedDom = +dom;
      return true;
    }
    return false;
  }

  private parseTime(s: string): [number, number] {
    const m = /^(\d{1,2}):(\d{2})$/.exec((s || '').trim());
    if (!m) return [0, 0];
    return [Math.min(23, +m[1]), Math.min(59, +m[2])];
  }

  private pad(n: number): string { return String(n).padStart(2, '0'); }
}
