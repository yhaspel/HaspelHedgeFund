import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { BrokerStore } from '../../abstraction/broker.store';

/**
 * Settings card for the user's TradeStation developer-app credentials
 * (P3a-3 BYO; see ADR 0012). Mirrors the Provider keys (BYO) card style.
 * The stored secret never leaves the server in plaintext — we show the
 * source + a masked client_id and let the user replace either field.
 */
@Component({
  selector: 'hf-tradestation-app-card',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="card" data-test="tradestation-app-card">
      <div class="card-hd"><h2 class="title">TradeStation developer app (BYO)</h2></div>
      <div class="card-bd flex flex-col gap-3">
        <p class="text-[11.5px] text-text-3 m-0">
          Register an app on the
          <a href="https://api.tradestation.com" target="_blank" rel="noopener">
            TradeStation Developer Portal</a>
          and paste the credentials here. They're stored Fernet-encrypted on
          your user row. Full steps in the
          <a href="/info/tradestation-setup" target="_blank" rel="noopener">
            TradeStation Setup guide ↗</a>.
        </p>
        <div class="eyebrow border-b border-solid border-border pb-1.5">
          Current
        </div>
        <div class="text-[12px]">
          @if (loading()) {
            <span class="text-text-3">Loading…</span>
          } @else {
            <span class="pill"
                  [class.ok]="source() === 'user' || source() === 'env'">
              <span class="dot"></span>
              {{ source() === 'user' ? 'user creds set'
                 : source() === 'env' ? 'using env fallback'
                 : 'none' }}
            </span>
            @if (clientIdMasked()) {
              <span class="ml-2 mono text-text-2">client_id: {{ clientIdMasked() }}</span>
            }
          }
        </div>

        <div class="eyebrow border-b border-solid border-border pb-1.5 mt-1.5">
          Update
        </div>
        <div class="field">
          <label class="lbl" for="ts-client-id">Client ID</label>
          <input id="ts-client-id" class="input mono" type="text"
                 autocomplete="off"
                 [(ngModel)]="clientId" name="ts-cid"
                 placeholder="paste your TradeStation client_id"
                 data-test="ts-settings-client-id" />
        </div>
        <div class="field">
          <label class="lbl" for="ts-client-secret">Client Secret</label>
          <input id="ts-client-secret" class="input mono" type="password"
                 autocomplete="new-password"
                 [(ngModel)]="clientSecret" name="ts-csec"
                 placeholder="•••• paste to replace; leave blank to keep current"
                 data-test="ts-settings-client-secret" />
        </div>

        <div class="flex gap-2">
          <button type="button" class="btn primary save-btn"
                  (click)="save()" [disabled]="!canSave() || busy()"
                  data-test="ts-settings-save">
            {{ busy() ? 'Saving…' : 'Save credentials' }}
          </button>
          @if (hasUser()) {
            <button type="button" class="btn"
                    (click)="clear()" [disabled]="busy()"
                    data-test="ts-settings-clear">
              Clear
            </button>
          }
        </div>
        @if (msg(); as m) {
          <p role="status" aria-live="polite"
             class="text-[11.5px] text-[var(--acc-long-fg)] m-0"
             data-test="ts-settings-msg">{{ m }}</p>
        }
        @if (err(); as e) {
          <p role="alert" class="text-[11.5px] text-[var(--acc-short-fg)] m-0"
             data-test="ts-settings-err">{{ e }}</p>
        }
      </div>
    </section>
  `,
})
export class TradeStationAppCardComponent implements OnInit {
  private readonly store = inject(BrokerStore);

  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly source = signal<'user' | 'env' | ''>('');
  protected readonly clientIdMasked = signal('');
  protected readonly hasUser = signal(false);
  protected readonly msg = signal<string | null>(null);
  protected readonly err = signal<string | null>(null);
  protected clientId = '';
  protected clientSecret = '';

  ngOnInit(): void {
    this.refresh();
  }

  canSave(): boolean {
    // Both must be present together (the API rejects half-pairs).
    return !!this.clientId.trim() && !!this.clientSecret.trim();
  }

  refresh(): void {
    this.loading.set(true);
    this.store.getTradeStationAppCredentials().subscribe({
      next: (r) => {
        this.source.set(r.source);
        this.clientIdMasked.set(r.client_id_masked);
        this.hasUser.set(r.has_user_credentials);
        this.loading.set(false);
      },
      error: (e) => {
        this.err.set(e?.error?.detail ?? 'Failed to load credentials.');
        this.loading.set(false);
      },
    });
  }

  save(): void {
    this.busy.set(true);
    this.msg.set(null);
    this.err.set(null);
    this.store
      .saveTradeStationAppCredentials({
        client_id: this.clientId.trim(),
        client_secret: this.clientSecret.trim(),
      })
      .subscribe({
        next: (r) => {
          this.clientSecret = '';
          this.clientId = '';
          this.source.set(r.source);
          this.clientIdMasked.set(r.client_id_masked);
          this.hasUser.set(r.has_user_credentials);
          this.msg.set('Saved.');
          this.busy.set(false);
        },
        error: (e) => {
          this.err.set(e?.error?.detail ?? 'Save failed.');
          this.busy.set(false);
        },
      });
  }

  clear(): void {
    if (!confirm('Clear your TradeStation app credentials? The env fallback (if any) will be used.')) {
      return;
    }
    this.busy.set(true);
    this.store
      .saveTradeStationAppCredentials({ client_id: '', client_secret: '' })
      .subscribe({
        next: (r) => {
          this.source.set(r.source);
          this.clientIdMasked.set(r.client_id_masked);
          this.hasUser.set(r.has_user_credentials);
          this.msg.set('Cleared.');
          this.busy.set(false);
        },
        error: (e) => {
          this.err.set(e?.error?.detail ?? 'Clear failed.');
          this.busy.set(false);
        },
      });
  }
}
