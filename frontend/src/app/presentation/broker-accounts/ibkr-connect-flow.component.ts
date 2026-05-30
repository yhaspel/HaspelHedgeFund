import {
  Component,
  EventEmitter,
  Input,
  OnDestroy,
  OnInit,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription, interval } from 'rxjs';
import { BrokerStore } from '../../abstraction/broker.store';
import {
  BrokerAccount,
  IBKRDiscoveredAccount,
  IBKRGatewayAuthStatus,
  IBKRGatewayProbeResult,
} from '../../core/models/broker.model';

/**
 * P3a-2 IBKR connect-flow step. Rendered by `BrokerConnectWizardPage` once
 * the `pending-{uuid}` draft IBKR account has been created — drives the
 * four /gateway/* endpoints in sequence (probe → auth-status poll →
 * discover → activate) and emits `completed` with the activated account
 * (real `DU…` id, connection_status=active) on success.
 *
 * Designed to be incremental — the generic broker tile grid + label/mode
 * form in `BrokerConnectWizardPage` stays unchanged; this component
 * replaces only the per-broker step once the user picks IBKR.
 */
@Component({
  selector: 'hf-ibkr-connect-flow',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="card mt-4 p-4" data-test="ibkr-connect-flow">
      <div class="card-hd">
        <h2 class="title">Connect Interactive Brokers</h2>
        <span class="text-[11.5px] text-text-3 mono ml-2">
          account: {{ account.account_id }}
        </span>
      </div>

      <ol class="mt-3 space-y-3 text-xs">
        <!-- Step 1: probe gateway -->
        <li [class.text-text-3]="step() !== 1 && probeOk()">
          <div class="font-medium flex items-center gap-2">
            <span class="pill"
                  [class.ok]="probeOk()"
                  [class.warn]="probeChecked() && !probeOk()">
              <span class="dot"></span>1
            </span>
            <span>Gateway reachable</span>
          </div>
          @if (step() === 1) {
            @if (!probeChecked()) {
              <p class="text-text-2 mt-1">Probing the gateway at the configured URL…</p>
            } @else if (probeOk()) {
              <p class="text-text-2 mt-1">Gateway is up.</p>
              <button class="btn primary btn-sm mt-2" (click)="advanceToLogin()"
                      data-test="ibkr-step1-continue">
                Continue
              </button>
            } @else {
              <p class="text-text-2 mt-1">
                The IBKR gateway sidecar isn't reachable at the configured URL.
                Make sure it's running:
              </p>
              <pre class="mono text-[11px] bg-bg-2 p-2 rounded mt-1 overflow-x-auto"
              >docker compose -f infra/docker-compose.yml up -d ibkr-gateway</pre>
              <p class="text-text-3 text-[11px] mt-1">
                Full setup guide:
                <a href="https://github.com/anthropics/claude-code"
                   target="_blank" rel="noopener">guides/ibkr-gateway.md</a>
              </p>
              <p class="text-text-3 text-[11px] mt-1">Detail: {{ probeDetail() }}</p>
              <button class="btn btn-sm mt-2" (click)="runProbe()"
                      [disabled]="busy()" data-test="ibkr-step1-retry">
                Retry
              </button>
            }
          }
        </li>

        <!-- Step 2: interactive gateway login -->
        <li [class.text-text-3]="step() < 2 || (step() > 2 && authReady())">
          <div class="font-medium flex items-center gap-2">
            <span class="pill"
                  [class.ok]="authReady()"
                  [class.warn]="step() === 2 && !authReady()">
              <span class="dot"></span>2
            </span>
            <span>Authenticate gateway</span>
          </div>
          @if (step() === 2) {
            @if (authReady()) {
              <p class="text-text-2 mt-1">Gateway session is authenticated and connected.</p>
              <button class="btn primary btn-sm mt-2" (click)="advanceToDiscover()"
                      data-test="ibkr-step2-continue">
                Continue
              </button>
            } @else {
              <p class="text-text-2 mt-1">
                Open the gateway login page in your browser, sign in with
                your IBKR username + password, and complete 2FA. We'll
                detect the authenticated session automatically.
              </p>
              @if (loginUrl()) {
                <a class="btn primary btn-sm mt-2" [href]="loginUrl()"
                   target="_blank" rel="noopener"
                   data-test="ibkr-step2-login-link">
                  Open gateway login →
                </a>
              }
              <p class="text-text-3 text-[11px] mt-2">
                Polling auth-status every 3 s.
                @if (authStatus()) {
                  authenticated: {{ authStatus()!.authenticated }} ·
                  connected: {{ authStatus()!.connected }}
                  @if (authStatus()!.detail) {
                    · {{ authStatus()!.detail }}
                  }
                }
              </p>
            }
          }
        </li>

        <!-- Step 3: pick an IBKR account -->
        <li [class.text-text-3]="step() < 3 || step() > 3">
          <div class="font-medium flex items-center gap-2">
            <span class="pill"
                  [class.ok]="pickedAccountId()"
                  [class.warn]="step() === 3 && !pickedAccountId() && !discoveryError()">
              <span class="dot"></span>3
            </span>
            <span>Pick an account</span>
          </div>
          @if (step() === 3) {
            @if (discoveryError(); as msg) {
              <p class="text-text-2 mt-1">{{ msg }}</p>
              <button class="btn btn-sm mt-2" (click)="runDiscover()"
                      [disabled]="busy()" data-test="ibkr-step3-retry">
                Retry
              </button>
            } @else if (discovered().length === 0) {
              <p class="text-text-2 mt-1">Discovering accounts…</p>
            } @else {
              <p class="text-text-2 mt-1">
                Pick the account to connect.
                @if (account.mode === 'paper') {
                  Paper accounts start with <span class="mono">DU</span>.
                }
              </p>
              <ul class="space-y-1.5 mt-2">
                @for (a of discovered(); track a.account_id) {
                  <li>
                    <button class="btn btn-sm w-full text-left flex justify-between items-center"
                            [class.primary]="pickedAccountId() === a.account_id"
                            [disabled]="!isPickableForMode(a)"
                            (click)="pick(a.account_id)"
                            [attr.data-test]="'ibkr-pick-' + a.account_id">
                      <span class="mono">{{ a.account_id }}</span>
                      <span class="pill" [class.ok]="a.is_paper" [class.warn]="!a.is_paper">
                        <span class="dot"></span>{{ a.is_paper ? 'paper' : 'live' }}
                      </span>
                    </button>
                  </li>
                }
              </ul>
              @if (pickedAccountId()) {
                <button class="btn primary btn-sm mt-2" (click)="activate()"
                        [disabled]="busy()" data-test="ibkr-step3-activate">
                  Activate {{ pickedAccountId() }}
                </button>
              }
            }
          }
        </li>

        <!-- Step 4: activating -->
        <li [class.text-text-3]="step() < 4">
          <div class="font-medium flex items-center gap-2">
            <span class="pill" [class.warn]="step() === 4"><span class="dot"></span>4</span>
            <span>Activate</span>
          </div>
          @if (step() === 4) {
            <p class="text-text-2 mt-1">Activating — rewriting account id, creating credential row…</p>
          }
        </li>
      </ol>

      @if (error(); as msg) {
        <div role="alert" class="pill err h-auto py-1.5 px-2.5 mt-3"
             data-test="ibkr-flow-error">
          <span class="dot"></span>{{ msg }}
        </div>
      }

      <div class="text-right mt-4">
        <button class="btn" (click)="cancel.emit()" data-test="ibkr-flow-cancel">
          Cancel
        </button>
      </div>
    </section>
  `,
})
export class IBKRConnectFlowComponent implements OnInit, OnDestroy {
  @Input({ required: true }) account!: BrokerAccount;
  @Output() completed = new EventEmitter<BrokerAccount>();
  @Output() cancel = new EventEmitter<void>();

  private readonly store = inject(BrokerStore);

  protected readonly step = signal<1 | 2 | 3 | 4>(1);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly probeChecked = signal(false);
  protected readonly probeResult = signal<IBKRGatewayProbeResult | null>(null);
  protected readonly probeOk = () => !!this.probeResult()?.reachable;
  protected readonly probeDetail = () => this.probeResult()?.detail ?? '';

  protected readonly loginUrl = signal<string>('');
  protected readonly authStatus = signal<IBKRGatewayAuthStatus | null>(null);
  protected readonly authReady = () => !!this.authStatus()?.ready;

  protected readonly discovered = signal<IBKRDiscoveredAccount[]>([]);
  protected readonly discoveryError = signal<string | null>(null);
  protected readonly pickedAccountId = signal<string | null>(null);

  private authPollSub?: Subscription;

  ngOnInit(): void {
    // Fetch the login URL once — used by Step 2's "Open gateway login" link.
    this.store.getIBKRRuntimeConfig().subscribe({
      next: (cfg) => this.loginUrl.set(cfg.gateway_login_url),
      error: () => this.loginUrl.set(''),
    });
    this.runProbe();
  }

  ngOnDestroy(): void {
    this.stopAuthPoll();
  }

  // -- Step 1 -----------------------------------------------------------

  runProbe(): void {
    this.busy.set(true);
    this.error.set(null);
    this.probeChecked.set(false);
    this.store.probeIBKRGateway(this.account.id).subscribe({
      next: (r) => {
        this.probeResult.set(r);
        this.probeChecked.set(true);
        this.busy.set(false);
      },
      error: (err) => {
        this.busy.set(false);
        this.probeChecked.set(true);
        this.probeResult.set({
          reachable: false,
          detail: err?.error?.detail ?? 'Probe request failed.',
        });
      },
    });
  }

  advanceToLogin(): void {
    this.step.set(2);
    this.startAuthPoll();
  }

  // -- Step 2 -----------------------------------------------------------

  private startAuthPoll(): void {
    this.stopAuthPoll();
    // Single immediate check + 3 s interval. When ready=true we stop the
    // poll and let the user click Continue (rather than auto-advancing,
    // so they see the green check before the UI moves).
    this.checkAuthOnce();
    this.authPollSub = interval(3000).subscribe(() => this.checkAuthOnce());
  }

  private stopAuthPoll(): void {
    if (this.authPollSub) {
      this.authPollSub.unsubscribe();
      this.authPollSub = undefined;
    }
  }

  private checkAuthOnce(): void {
    this.store.getIBKRGatewayAuthStatus(this.account.id).subscribe({
      next: (s) => {
        this.authStatus.set(s);
        if (s.ready) {
          this.stopAuthPoll();
        }
      },
      error: () => {
        // Transient API failures shouldn't tear down the poll — the next
        // tick will retry.
      },
    });
  }

  advanceToDiscover(): void {
    this.stopAuthPoll();
    this.step.set(3);
    this.runDiscover();
  }

  // -- Step 3 -----------------------------------------------------------

  runDiscover(): void {
    this.busy.set(true);
    this.error.set(null);
    this.discoveryError.set(null);
    this.discovered.set([]);
    this.pickedAccountId.set(null);
    this.store.discoverIBKRAccounts(this.account.id).subscribe({
      next: (r) => {
        this.discovered.set(r.accounts);
        if (r.accounts.length === 0) {
          this.discoveryError.set('No accounts are visible to the gateway session.');
        } else if (r.selected) {
          // Pre-select IBKR's suggested account if it's pickable.
          const match = r.accounts.find((a) => a.account_id === r.selected);
          if (match && this.isPickableForMode(match)) {
            this.pickedAccountId.set(match.account_id);
          }
        }
        this.busy.set(false);
      },
      error: (err) => {
        this.busy.set(false);
        this.discoveryError.set(
          err?.error?.detail ?? 'Could not discover accounts. Try again.',
        );
      },
    });
  }

  isPickableForMode(a: IBKRDiscoveredAccount): boolean {
    // Paper mode requires DU-prefix; live mode requires non-DU. Backend
    // re-validates on activate (ADR 0011 §6) — this just avoids a known-
    // bad click.
    return this.account.mode === 'paper' ? a.is_paper : !a.is_paper;
  }

  pick(id: string): void {
    this.pickedAccountId.set(id);
    this.error.set(null);
  }

  // -- Step 4 -----------------------------------------------------------

  activate(): void {
    const picked = this.pickedAccountId();
    if (!picked) return;
    this.step.set(4);
    this.busy.set(true);
    this.error.set(null);
    this.store.activateIBKRAccount(this.account.id, picked).subscribe({
      next: (acc) => {
        this.busy.set(false);
        this.completed.emit(acc);
      },
      error: (err) => {
        this.busy.set(false);
        this.step.set(3); // back to picker so the user can adjust
        this.error.set(
          err?.error?.detail ?? 'Activation failed.',
        );
      },
    });
  }
}
