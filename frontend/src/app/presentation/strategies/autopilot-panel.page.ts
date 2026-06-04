import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { FundStore } from '../../abstraction/fund.store';
import { Autopilot } from '../../core/models/autopilot.model';
import { AppShellComponent } from '../shared/app-shell.component';

// P7 §14 — per-strategy autopilot panel: the validation-gate checklist that
// unlocks the enable toggle, a prominent state chip, the guardrail config, and
// the run-now / resume controls. Paper-only; carries the disclaimer.
@Component({
  selector: 'hf-autopilot-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Fund', link: '/fund' }, { label: 'Autopilot' }]">
      <div class="page-head">
        <div><h1>Autopilot</h1></div>
        <div class="head-actions" *ngIf="ap() as a">
          <span
            class="pill"
            [class.ok]="a.state === 'active' && a.is_enabled"
            [class.warn]="a.state === 'soft_cut'"
            [class.err]="a.state === 'halted'"
            [class.info]="!a.is_enabled"
          ><span class="dot"></span>{{ a.is_enabled ? a.state : 'disabled' }}</span>
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
          <button
            *ngIf="!a.is_enabled"
            class="btn btn-primary"
            [disabled]="!a.validation.passed || busy()"
            [title]="a.validation.passed ? '' : 'Validation gate not passed'"
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

        <!-- Guardrail config -->
        <section class="card">
          <h2>Guardrails</h2>
          <div class="grid">
            <label>Cron <input [(ngModel)]="form.cron_expression" /></label>
            <label>Model preset
              <select [(ngModel)]="form.model_preset">
                <option value="dev">dev</option>
                <option value="frugal">frugal</option>
                <option value="hybrid">hybrid</option>
                <option value="research">research</option>
                <option value="quality">quality</option>
              </select>
            </label>
            <label>Target vol % <input type="number" [(ngModel)]="form.target_vol_pct" /></label>
            <label>Soft cut % <input type="number" [(ngModel)]="form.dd_soft_cut_pct" /></label>
            <label>Hard halt % <input type="number" [(ngModel)]="form.dd_hard_halt_pct" /></label>
            <label>Max orders/day <input type="number" [(ngModel)]="form.max_orders_per_day" /></label>
            <label>Liquidity ADV cap % <input type="number" [(ngModel)]="form.liquidity_adv_cap_pct" /></label>
            <label class="chk"><input type="checkbox" [(ngModel)]="form.flatten_on_halt" /> Flatten to cash on hard halt</label>
          </div>
          <button class="btn" (click)="save()" [disabled]="busy()">Save guardrails</button>
        </section>
      </ng-container>
    </hf-app-shell>
  `,
  styles: [`
    .disclaimer { color: var(--text-3); font-size: 12px; margin: 4px 0 14px; }
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
    .muted { color: var(--text-3); }
  `],
})
export class AutopilotPanelPage implements OnInit {
  private readonly store = inject(FundStore);
  private readonly route = inject(ActivatedRoute);
  readonly ap = this.store.autopilot;
  readonly busy = signal(false);
  readonly notice = signal<string | null>(null);
  strategyId = 0;

  form: Partial<Autopilot> = {};

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
      }
    });
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
  save(): void { this.run(this.store.saveAutopilot(this.strategyId, this.form), 'Guardrails saved.'); }
}
