import { CommonModule } from '@angular/common';
import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { FundStore } from '../../abstraction/fund.store';
import { FundOverview } from '../../core/models/autopilot.model';
import { ConfirmService } from '../shared/confirm.service';

// P14 — Fund settings: the ONE shared paper account every member trades in, the
// fund-wide drawdown halt, and the fund's name. Creates the fund on first save
// (PUT /fund/), so this card is also the "no fund yet" set-up flow.
@Component({
  selector: 'hf-fund-settings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <section class="card settings">
      <h2>{{ fund() ? 'Fund settings' : 'Set up the autonomous fund' }}</h2>
      <p class="muted lead">
        The fund trades <b>one</b> paper account. Every strategy you add gets a slice of that
        account's cash (its <i>sleeve</i>) and trades only its own slice — so several strategies
        share the pool without touching each other's positions.
      </p>

      <div class="grid">
        <label class="field">
          <span class="lbl">Fund name</span>
          <input
            class="input sans"
            type="text"
            [(ngModel)]="name"
            name="fund-name"
            maxlength="80"
          />
        </label>
        <label class="field">
          <span class="lbl">Fund drawdown halt (%)</span>
          <input
            class="input"
            type="number"
            min="0"
            max="50"
            step="0.5"
            [(ngModel)]="ddHalt"
            name="fund-dd"
          />
        </label>
        <label class="field acct">
          <span class="lbl">Shared paper account</span>
          <select class="input sans" [(ngModel)]="accountId" name="fund-account">
            <option [ngValue]="null">— choose a paper account —</option>
            @for (a of accounts(); track a.id) {
              <option [ngValue]="a.id">
                {{ a.label }} · {{ a.broker_display }} · {{ a.connection_status }} · cash
                {{ +a.cash | currency: 'USD' : 'symbol' : '1.0-0' }}
                {{ a.positions_count ? '· ' + a.positions_count + ' positions' : '' }}
              </option>
            }
          </select>
        </label>
      </div>

      @if (!accounts().length) {
        <p class="hint">
          No paper accounts connected yet —
          <a routerLink="/broker-accounts" class="link">connect an Alpaca paper account</a>
          first, then pick it here.
        </p>
      }
      @if (changingAccount()) {
        <p class="hint warn">
          Changing the account moves every strategy onto the new pool. Sleeves must be flat first
          (flatten the fund), and you'll <b>Reset</b> afterwards to re-split the new account's cash.
        </p>
      }
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }

      <div class="actions">
        <button
          type="button"
          class="btn primary"
          (click)="save()"
          [disabled]="busy() || (!!fund() && !isDirty())"
        >
          {{ busy() ? 'Saving…' : fund() ? 'Save settings' : 'Create fund' }}
        </button>
        @if (saved()) {
          <span class="ok">Saved ✓</span>
        }
      </div>
    </section>
  `,
  styles: [
    `
      .settings {
        padding: 16px;
        margin: 12px 0;
      }
      .settings > h2 {
        font-size: var(--fs-11);
        line-height: 16px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--text-3);
        font-weight: 500;
        margin: 0 0 8px;
      }
      .lead {
        font-size: 12.5px;
        margin: 0 0 12px;
        max-width: 70ch;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 12px;
      }
      .field.acct {
        grid-column: 1 / -1;
      }
      .hint {
        font-size: 12px;
        color: var(--text-3);
        margin: 10px 0 0;
      }
      .hint.warn {
        color: var(--acc-short-fg);
      }
      .link {
        text-decoration: underline;
        color: var(--acc-info-fg);
      }
      .error {
        color: var(--acc-short-fg);
        font-size: 12.5px;
        margin: 10px 0 0;
      }
      .actions {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 12px;
      }
      .ok {
        color: var(--acc-long-fg);
        font-size: 12px;
      }
      .muted {
        color: var(--text-3);
      }
    `,
  ],
})
export class FundSettingsComponent {
  private readonly store = inject(FundStore);
  private readonly confirm = inject(ConfirmService);

  /** The current overview (null before the fund exists). */
  readonly fund = input<FundOverview | null>(null);
  readonly saved$ = output<FundOverview>();

  readonly accounts = this.store.accounts;
  readonly busy = signal(false);
  readonly saved = signal(false);
  readonly error = signal<string | null>(null);

  name = 'Autonomous Fund';
  ddHalt: number | null = 6;
  accountId: number | null = null;
  // Snapshot of the last-applied values so "Save" only lights up on a change.
  private applied = {
    name: 'Autonomous Fund',
    ddHalt: 6 as number | null,
    accountId: null as number | null,
  };

  constructor() {
    this.store.loadAccounts().subscribe({ error: () => {} });
    // Re-seed the form whenever the SERVER state changes (fund loaded, saved,
    // reset) — never on the user's own in-progress edits.
    effect(() => this.syncFrom(this.fund()));
  }

  /** Seed the form from the overview when its applied values changed server-side. */
  syncFrom(f: FundOverview | null): void {
    if (!f) return;
    const incoming = {
      name: f.name,
      ddHalt: +f.fund_dd_halt_pct,
      accountId: f.broker_account?.id ?? null,
    };
    if (
      incoming.name === this.applied.name &&
      incoming.ddHalt === this.applied.ddHalt &&
      incoming.accountId === this.applied.accountId
    ) {
      return;
    }
    this.name = incoming.name;
    this.ddHalt = incoming.ddHalt;
    this.accountId = incoming.accountId;
    this.applied = { ...incoming };
  }

  isDirty(): boolean {
    return (
      this.name.trim() !== this.applied.name ||
      (this.ddHalt ?? null) !== this.applied.ddHalt ||
      this.accountId !== this.applied.accountId
    );
  }

  changingAccount(): boolean {
    return this.applied.accountId !== null && this.accountId !== this.applied.accountId;
  }

  async save(): Promise<void> {
    this.error.set(null);
    if (this.changingAccount()) {
      const ok = await this.confirm.ask({
        title: 'Move the fund to another account?',
        body:
          'Every member strategy will trade the new paper account instead. Sleeve cash is reset ' +
          'to $0 and you will Reset the fund to split the new account’s cash by allocation.',
        confirmLabel: 'Move fund',
        danger: true,
      });
      if (!ok) return;
    }
    const body: { name?: string; fund_dd_halt_pct?: number; broker_account_id?: number } = {};
    if (this.name.trim()) body.name = this.name.trim();
    if (this.ddHalt !== null && this.ddHalt !== undefined) body.fund_dd_halt_pct = this.ddHalt;
    if (this.accountId !== null) body.broker_account_id = this.accountId;
    this.busy.set(true);
    this.store.configureFund(body).subscribe({
      next: (f) => {
        this.busy.set(false);
        this.saved.set(true);
        setTimeout(() => this.saved.set(false), 2500);
        this.syncFrom(f);
        this.saved$.emit(f);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(e?.error?.detail ?? 'Could not save the fund settings.');
      },
    });
  }
}
