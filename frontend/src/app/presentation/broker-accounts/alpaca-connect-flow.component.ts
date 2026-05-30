import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerAccount } from '../../core/models/broker.model';

/**
 * P3a-4 — Alpaca paper connect flow.
 *
 * Single step: collect the API key id + secret, POST them to the
 * existing `/api/broker-accounts/<id>/credentials/` endpoint. The
 * backend validates the pair against `GET /v2/account` on the Alpaca
 * **paper** host, encrypts + stores them, discovers the account number,
 * and flips `connection_status` to `active`. On success we emit
 * `completed` so the wizard navigates to the account overview.
 *
 * The component renders a "community-unverified" notice because the
 * adapter ships best-effort until the live-sandbox checklist passes
 * (ADR 0013, phase-03a-4 §"Verification caveat").
 */
@Component({
  selector: 'hf-alpaca-connect-flow',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="card mt-4 p-4" data-test="alpaca-connect-flow">
      <div class="card-hd">
        <h2 class="title">Connect Alpaca (paper)</h2>
        <span class="pill warn ml-2"><span class="dot"></span>community-unverified</span>
      </div>

      <p class="text-xs text-text-2 mt-2">
        Generate a <strong>paper-trading</strong> API key pair in your Alpaca
        dashboard
        (<a class="underline" href="https://app.alpaca.markets/paper/dashboard/overview"
          target="_blank" rel="noopener">app.alpaca.markets &rarr; Paper trading &rarr; API keys</a>).
        Live keys will be rejected — this integration is paper-only by
        construction.
      </p>

      <div class="callout warn mt-3 text-xs">
        This adapter has not yet been end-to-end verified against a live
        Alpaca sandbox account by the project maintainer. It is shipped
        best-effort. If you encounter an issue, please report it so the
        first verified run can lift this badge.
      </div>

      <div class="space-y-2.5 mt-3">
        <label class="lbl block">
          API key ID
          <input class="input mono" type="text" autocomplete="off"
                 [(ngModel)]="apiKey" name="api_key"
                 data-test="alpaca-api-key"
                 placeholder="PK…" />
        </label>
        <label class="lbl block">
          Secret key
          <input class="input mono" type="password" autocomplete="off"
                 [(ngModel)]="apiSecret" name="api_secret"
                 data-test="alpaca-api-secret"
                 placeholder="paste secret" />
        </label>

        @if (error(); as msg) {
          <div role="alert" class="pill err h-auto py-1.5 px-2.5">
            <span class="dot"></span>{{ msg }}
          </div>
        }

        <div class="text-right">
          <button class="btn" (click)="onCancel()" data-test="alpaca-cancel">
            Cancel
          </button>
          <button class="btn primary ml-2"
                  [disabled]="!canSubmit() || busy()"
                  (click)="submit()"
                  data-test="alpaca-submit">
            {{ busy() ? 'Validating…' : 'Connect' }}
          </button>
        </div>
      </div>
    </section>
  `,
})
export class AlpacaConnectFlowComponent {
  @Input({ required: true }) account!: BrokerAccount;
  @Output() readonly completed = new EventEmitter<BrokerAccount>();
  @Output() readonly cancel = new EventEmitter<void>();

  private readonly store = inject(BrokerStore);

  protected apiKey = '';
  protected apiSecret = '';
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);

  protected canSubmit(): boolean {
    return this.apiKey.trim().length > 0 && this.apiSecret.trim().length > 0;
  }

  submit(): void {
    if (!this.canSubmit()) return;
    this.busy.set(true);
    this.error.set(null);
    this.store
      .submitAlpacaCredentials(this.account.id, {
        api_key: this.apiKey.trim(),
        api_secret: this.apiSecret.trim(),
      })
      .subscribe({
        next: (acc) => {
          this.busy.set(false);
          this.completed.emit(acc);
        },
        error: (err) => {
          this.busy.set(false);
          this.error.set(
            err?.error?.detail ??
              err?.error?.api_key ??
              err?.error?.api_secret ??
              'Failed to validate Alpaca credentials.',
          );
        },
      });
  }

  onCancel(): void {
    this.cancel.emit();
  }
}
