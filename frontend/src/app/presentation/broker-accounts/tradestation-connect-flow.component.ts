import {
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { BrokerStore } from '../../abstraction/broker.store';
import {
  BrokerAccount,
  TradeStationDiscoveredAccount,
} from '../../core/models/broker.model';

/**
 * P3a-3 TradeStation connect-flow step. Rendered by `BrokerConnectWizardPage`
 * once the draft TradeStation account has been created — drives the
 * OAuth-start → external authorize → return → discover → activate sequence.
 *
 * Steps:
 *  1. runtime-config: check the deployer registered a developer app.
 *  2. POST /oauth/start/ → open the TradeStation authorize URL in a new tab.
 *  3. User completes consent in TradeStation; callback exchanges the code
 *     and stores the OAuth tokens on the draft account server-side.
 *  4. User clicks "I've completed sign-in" → discover accounts.
 *  5. Pick one → activate → emit `completed`.
 */
@Component({
  selector: 'hf-tradestation-connect-flow',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="card mt-4 p-4" data-test="tradestation-connect-flow">
      <div class="card-hd">
        <h2 class="title">Connect TradeStation</h2>
        <span class="text-[11.5px] text-text-3 mono ml-2">
          mode: {{ account.mode }}
        </span>
      </div>

      <ol class="mt-3 space-y-3 text-xs">
        <!-- Step 1: app credentials configured -->
        <li>
          <div class="font-medium flex items-center gap-2">
            <span class="pill"
                  [class.ok]="configured()"
                  [class.warn]="configChecked() && !configured()">
              <span class="dot"></span>1
            </span>
            <span>Developer app configured</span>
          </div>
          @if (step() === 1) {
            @if (!configChecked()) {
              <p class="text-text-2 mt-1">Checking…</p>
            } @else if (configured()) {
              <p class="text-text-2 mt-1">
                TradeStation client credentials are present
                <span class="text-text-3">(source: {{ source() }})</span>.
              </p>
              <button class="btn primary btn-sm mt-2" (click)="advanceToAuthorize()"
                      data-test="ts-step1-continue">
                Continue
              </button>
            } @else {
              <p class="text-text-2 mt-1">
                Enter your TradeStation developer-app
                <span class="mono">client_id</span> and
                <span class="mono">client_secret</span>. We'll store them
                encrypted on your account.
              </p>
              <p class="text-text-3 text-[11px] mt-1">
                No developer app yet?
                <a href="/info/tradestation-setup"
                   target="_blank" rel="noopener"
                   data-test="ts-setup-guide-link">
                  TradeStation Setup guide ↗
                </a>
              </p>
              <div class="space-y-2 mt-2">
                <label class="lbl block">
                  Client ID
                  <input class="input mono" type="text"
                         [(ngModel)]="clientId" name="clientId"
                         autocomplete="off" data-test="ts-client-id-input" />
                </label>
                <label class="lbl block">
                  Client Secret
                  <input class="input mono" type="password"
                         [(ngModel)]="clientSecret" name="clientSecret"
                         autocomplete="off" data-test="ts-client-secret-input" />
                </label>
                <div class="text-right">
                  <button class="btn primary btn-sm"
                          [disabled]="!clientId || !clientSecret || busy()"
                          (click)="saveCredentials()"
                          data-test="ts-step1-save">
                    {{ busy() ? 'Saving…' : 'Save & continue' }}
                  </button>
                </div>
              </div>
            }
          }
        </li>

        <!-- Step 2: authorize -->
        <li [class.text-text-3]="step() < 2">
          <div class="font-medium flex items-center gap-2">
            <span class="pill"
                  [class.ok]="authStarted()">
              <span class="dot"></span>2
            </span>
            <span>Authorize with TradeStation</span>
          </div>
          @if (step() === 2) {
            <p class="text-text-2 mt-1">
              Open the TradeStation sign-in page, grant consent, then return here.
            </p>
            @if (authzUrl(); as url) {
              <a class="btn primary btn-sm mt-2"
                 [href]="url" target="_blank" rel="noopener"
                 (click)="authStarted.set(true)"
                 data-test="ts-step2-authorize">
                Open TradeStation sign-in ↗
              </a>
            } @else {
              <button class="btn primary btn-sm mt-2" (click)="startOAuth()"
                      [disabled]="busy()" data-test="ts-step2-start">
                Get sign-in link
              </button>
            }
            @if (authStarted()) {
              <div class="mt-2">
                <button class="btn btn-sm" (click)="advanceToDiscover()"
                        data-test="ts-step2-done">
                  I've completed sign-in — continue
                </button>
              </div>
            }
            <div class="mt-2">
              <button class="btn btn-sm" (click)="back()"
                      data-test="ts-step2-back">
                ← Back
              </button>
            </div>
          }
        </li>

        <!-- Step 3: discover + activate -->
        <li [class.text-text-3]="step() < 3">
          <div class="font-medium flex items-center gap-2">
            <span class="pill" [class.ok]="picked()">
              <span class="dot"></span>3
            </span>
            <span>Pick a TradeStation account</span>
          </div>
          @if (step() === 3) {
            @if (!discovered().length && !busy()) {
              <button class="btn btn-sm mt-2" (click)="discover()"
                      data-test="ts-step3-discover">
                Load accounts
              </button>
            }
            @if (busy()) {
              <p class="text-text-2 mt-1">Loading…</p>
            }
            @if (discovered().length) {
              <ul class="mt-2 space-y-1">
                @for (acc of discovered(); track acc.account_id) {
                  <li>
                    <label class="flex items-center gap-2">
                      <input type="radio" name="ts-account"
                             [value]="acc.account_id"
                             (change)="picked.set(acc.account_id)"
                             [attr.data-test]="'ts-pick-' + acc.account_id" />
                      <span class="mono">{{ acc.account_id }}</span>
                      <span class="text-text-3">
                        {{ acc.type }} · {{ acc.currency }} · {{ acc.status }}
                      </span>
                    </label>
                  </li>
                }
              </ul>
              <button class="btn primary btn-sm mt-2"
                      [disabled]="!picked() || busy()"
                      (click)="activate()"
                      data-test="ts-step3-activate">
                Activate account
              </button>
            }
            <div class="mt-2">
              <button class="btn btn-sm" (click)="back()"
                      data-test="ts-step3-back">
                ← Back
              </button>
            </div>
          }
        </li>
      </ol>

      @if (error(); as msg) {
        <div role="alert" class="pill err mt-3 h-auto py-1.5 px-2.5">
          <span class="dot"></span>{{ msg }}
        </div>
      }
      <div class="text-right mt-3">
        <button class="btn btn-sm" (click)="cancel.emit()" data-test="ts-cancel">
          Cancel
        </button>
      </div>
    </section>
  `,
})
export class TradeStationConnectFlowComponent implements OnInit {
  @Input({ required: true }) account!: BrokerAccount;
  @Output() completed = new EventEmitter<BrokerAccount>();
  @Output() cancel = new EventEmitter<void>();

  private readonly store = inject(BrokerStore);

  protected readonly step = signal<1 | 2 | 3>(1);
  protected readonly configChecked = signal(false);
  protected readonly configured = signal(false);
  protected readonly source = signal<'user' | 'env' | ''>('');
  protected clientId = '';
  protected clientSecret = '';
  protected readonly authzUrl = signal<string | null>(null);
  protected readonly authStarted = signal(false);
  protected readonly discovered = signal<TradeStationDiscoveredAccount[]>([]);
  protected readonly picked = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.checkConfig();
  }

  checkConfig(): void {
    this.busy.set(true);
    this.store.getTradeStationRuntimeConfig().subscribe({
      next: (r) => {
        this.configured.set(r.configured);
        this.source.set(r.source);
        this.configChecked.set(true);
        this.busy.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'Failed to check configuration.');
        this.configChecked.set(true);
        this.busy.set(false);
      },
    });
  }

  saveCredentials(): void {
    if (!this.clientId || !this.clientSecret) return;
    this.busy.set(true);
    this.error.set(null);
    this.store
      .saveTradeStationAppCredentials({
        client_id: this.clientId,
        client_secret: this.clientSecret,
      })
      .subscribe({
        next: () => {
          this.clientSecret = '';  // never keep the plaintext in memory
          this.configured.set(true);
          this.source.set('user');
          this.busy.set(false);
          this.advanceToAuthorize();
        },
        error: (err) => {
          this.error.set(err?.error?.detail ?? 'Failed to save credentials.');
          this.busy.set(false);
        },
      });
  }

  advanceToAuthorize(): void {
    this.step.set(2);
  }

  back(): void {
    const s = this.step();
    if (s === 3) {
      // Going back from discover lets the user re-authorize (e.g. they
      // signed in to the wrong TradeStation account). Reset the picker
      // so we don't show stale data when they return.
      this.discovered.set([]);
      this.picked.set(null);
      this.step.set(2);
    } else if (s === 2) {
      // Going back from authorize lets the user fix their developer-app
      // credentials. Re-check config in case they updated env vars.
      this.authzUrl.set(null);
      this.authStarted.set(false);
      this.step.set(1);
      this.checkConfig();
    }
    this.error.set(null);
  }

  startOAuth(): void {
    this.busy.set(true);
    this.error.set(null);
    this.store.startTradeStationOAuth(this.account.id).subscribe({
      next: (r) => {
        this.authzUrl.set(r.authorization_url);
        this.busy.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'Failed to start OAuth.');
        this.busy.set(false);
      },
    });
  }

  advanceToDiscover(): void {
    this.step.set(3);
    this.discover();
  }

  discover(): void {
    this.busy.set(true);
    this.error.set(null);
    this.store.discoverTradeStationAccounts(this.account.id).subscribe({
      next: (r) => {
        this.discovered.set(r.accounts || []);
        this.busy.set(false);
        if (!r.accounts?.length) {
          this.error.set(
            'No TradeStation accounts visible. Complete sign-in, then retry.',
          );
        }
      },
      error: (err) => {
        this.error.set(
          err?.error?.detail ??
            'Failed to discover accounts. Complete sign-in, then retry.',
        );
        this.busy.set(false);
      },
    });
  }

  activate(): void {
    const picked = this.picked();
    if (!picked) return;
    this.busy.set(true);
    this.error.set(null);
    this.store
      .activateTradeStationAccount(this.account.id, picked)
      .subscribe({
        next: (acc) => {
          this.busy.set(false);
          this.completed.emit(acc);
        },
        error: (err) => {
          this.error.set(err?.error?.detail ?? 'Activation failed.');
          this.busy.set(false);
        },
      });
  }
}
