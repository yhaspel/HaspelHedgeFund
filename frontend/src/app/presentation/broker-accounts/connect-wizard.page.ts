import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerAccount, BrokerCapability } from '../../core/models/broker.model';
import { IBKRConnectFlowComponent } from './ibkr-connect-flow.component';
import { TradeStationConnectFlowComponent } from './tradestation-connect-flow.component';

/**
 * Connect-wizard SHELL. Demo and IBKR are fully wired here. TradeStation
 * and Alpaca render placeholder tiles with "ships in a later release"
 * until their phases land (P3a-3/4).
 *
 * For IBKR (P3a-2), the wizard's `create()` does NOT navigate after
 * `createAccount` succeeds — instead it stashes the new `pending-{uuid}`
 * draft and renders `<hf-ibkr-connect-flow>`, which drives the
 * /gateway/* probe → auth-status → discover → activate sequence and
 * emits `completed` once activation succeeds.
 */
@Component({
  selector: 'hf-broker-connect-wizard-page',
  standalone: true,
  imports: [
    CommonModule, FormsModule, AppShellComponent,
    IBKRConnectFlowComponent, TradeStationConnectFlowComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[
      {label:'Broker accounts', link:'/broker-accounts'},
      {label:'Connect'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Trading</div>
          <h1 class="mt-1.5">Connect an account</h1>
          <p class="text-xs text-text-2 mt-1">
            Pick a broker. The
            <strong>Demo broker</strong> is in-memory and seeds $100,000.
            Real brokers ship in later phases.
          </p>
        </div>
      </div>

      @if (loading()) {
        <div class="card p-4 text-xs text-text-3">Loading registry…</div>
      } @else {
        <section class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5" data-test="broker-grid">
          @for (cap of registry(); track cap.code) {
            <article class="card p-3.5"
                     [attr.data-test]="'broker-tile-' + cap.code">
              <div class="flex items-center justify-between">
                <div>
                  <div class="font-medium">{{ cap.display_name }}</div>
                  <div class="text-[11.5px] text-text-3 mono mt-0.5">code: {{ cap.code }}</div>
                </div>
                @if (!cap.available || cap.code === 'ibkr' || cap.code === 'tradestation') {
                  <span class="pill"><span class="dot"></span>later release</span>
                } @else if (cap.community_unverified) {
                  <span class="pill warn"><span class="dot"></span>unverified</span>
                } @else {
                  <span class="pill ok"><span class="dot"></span>ready</span>
                }
              </div>
              <p class="text-xs text-text-2 mt-2">{{ cap.description }}</p>
              <ul class="text-[11px] text-text-3 mt-2 space-y-0.5">
                <li>auth: <span class="mono">{{ cap.auth_kind }}</span></li>
                <li>order types: <span class="mono">{{ cap.supported_order_types.join(', ') }}</span></li>
                <li>fractional: <span class="mono">{{ cap.supports_fractional ? 'yes' : 'no' }}</span></li>
                @if (cap.supports_paper && cap.supports_live) {
                  <li>modes: <span class="mono">paper + live</span></li>
                } @else if (cap.supports_paper) {
                  <li>modes: <span class="mono">paper only</span></li>
                } @else {
                  <li>modes: <span class="mono">live only</span></li>
                }
              </ul>
              <div class="mt-2.5 text-right">
                @if (cap.available && cap.code !== 'ibkr' && cap.code !== 'tradestation') {
                  <button class="btn primary btn-sm" (click)="select(cap)"
                          [attr.data-test]="'select-broker-' + cap.code">
                    Continue
                  </button>
                } @else {
                  <button class="btn ghost btn-sm" disabled aria-disabled="true">
                    Available in a later release
                  </button>
                }
              </div>
            </article>
          }
        </section>

        @if (ibkrDraft(); as draft) {
          <hf-ibkr-connect-flow
            [account]="draft"
            (completed)="onIBKRActivated($event)"
            (cancel)="onIBKRCancel()" />
        } @else if (tsDraft(); as draft) {
          <hf-tradestation-connect-flow
            [account]="draft"
            (completed)="onTSActivated($event)"
            (cancel)="onTSCancel()" />
        } @else if (selected(); as cap) {
          <section class="card mt-4 p-4" data-test="connect-form">
            <div class="card-hd">
              <span class="title">Step 2 — {{ cap.display_name }}</span>
            </div>
            <div class="space-y-2.5 mt-2">
              <label class="lbl block">
                Account label
                <input class="input" type="text" [(ngModel)]="label" name="label"
                       placeholder="e.g. Demo book A" data-test="label-input" />
              </label>
              @if (cap.supports_paper && cap.supports_live) {
                <div class="lbl">
                  Mode
                  <div class="seg mt-1" role="radiogroup">
                    <button type="button" class="seg-btn" role="radio"
                            [attr.aria-checked]="mode === 'paper'"
                            [class.active]="mode === 'paper'" (click)="mode = 'paper'">Paper</button>
                    <button type="button" class="seg-btn" role="radio"
                            [attr.aria-checked]="mode === 'live'"
                            [class.active]="mode === 'live'" (click)="mode = 'live'">Live</button>
                  </div>
                </div>
              }
              @if (error(); as msg) {
                <div role="alert" class="pill err h-auto py-1.5 px-2.5"><span class="dot"></span>{{ msg }}</div>
              }
              <div class="text-right">
                <button class="btn" (click)="cancel()" data-test="cancel-btn">Cancel</button>
                <button class="btn primary ml-2" (click)="create()"
                        [disabled]="!label || busy()" data-test="create-account-btn">
                  {{ busy() ? 'Connecting…' : 'Create account' }}
                </button>
              </div>
            </div>
          </section>
        }
      }
    </hf-app-shell>
  `,
})
export class BrokerConnectWizardPage implements OnInit {
  private readonly store = inject(BrokerStore);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly registry = this.store.registry;
  protected readonly busy = this.store.busy;
  protected readonly loading = signal(true);
  protected readonly selected = signal<BrokerCapability | null>(null);
  protected readonly error = signal<string | null>(null);
  // P3a-2: when the user creates an IBKR draft, we stash it here and
  // render <hf-ibkr-connect-flow> instead of navigating away. The draft
  // carries the `pending-{uuid}` account_id that activate will rewrite.
  protected readonly ibkrDraft = signal<BrokerAccount | null>(null);
  // P3a-3: same pattern for TradeStation. Draft account is created here
  // (`connection_status="connecting"`) and the OAuth-start endpoint stamps
  // the chosen api_base_url + PKCE verifier onto it.
  protected readonly tsDraft = signal<BrokerAccount | null>(null);
  protected label = '';
  protected mode: 'paper' | 'live' = 'paper';

  ngOnInit(): void {
    this.store.loadRegistry().subscribe({
      next: () => {
        this.loading.set(false);
        this.maybeResume();
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'Failed to load registry.');
        this.loading.set(false);
      },
    });
  }

  /**
   * If `?resume=<id>` is present, re-enter the per-broker connect flow
   * for that draft account. Used when the user clicks "Resume setup" on
   * a half-configured account from the list page.
   */
  private maybeResume(): void {
    const raw = this.route.snapshot.queryParamMap.get('resume');
    const id = raw ? Number(raw) : NaN;
    if (!Number.isFinite(id)) return;
    this.store.loadAccounts().subscribe({
      next: (rows) => {
        const acc = rows.find((r) => r.id === id);
        if (!acc) {
          this.error.set('Account not found — it may have been deleted.');
          return;
        }
        if (acc.connection_status === 'active') {
          this.router.navigate(['/broker-accounts', acc.id]);
          return;
        }
        if (acc.broker === 'ibkr') {
          this.ibkrDraft.set(acc);
        } else if (acc.broker === 'tradestation') {
          this.tsDraft.set(acc);
        } else {
          this.router.navigate(['/broker-accounts', acc.id]);
        }
      },
      error: (err) =>
        this.error.set(err?.error?.detail ?? 'Failed to load account.'),
    });
  }

  select(cap: BrokerCapability): void {
    this.selected.set(cap);
    this.label = cap.code === 'mock' ? 'Demo book' : '';
    this.mode = cap.supports_paper ? 'paper' : 'live';
    this.error.set(null);
  }

  cancel(): void {
    this.selected.set(null);
    this.ibkrDraft.set(null);
    this.tsDraft.set(null);
    this.error.set(null);
  }

  create(): void {
    const cap = this.selected();
    if (!cap || !this.label) return;
    this.store
      .createAccount({ broker: cap.code, mode: this.mode, label: this.label })
      .subscribe({
        next: (acc) => {
          if (cap.code === 'ibkr') {
            // Stash + render the IBKR multi-step flow. The draft account
            // already exists with `account_id="pending-{uuid}"`; activate
            // rewrites it once the user picks a real DU id.
            this.ibkrDraft.set(acc);
          } else if (cap.code === 'tradestation') {
            // Same pattern for TradeStation OAuth.
            this.tsDraft.set(acc);
          } else {
            this.router.navigate(['/broker-accounts', acc.id]);
          }
        },
        error: (err) =>
          this.error.set(
            err?.error?.detail ??
              err?.error?.broker ??
              err?.error?.label ??
              'Failed to create account.',
          ),
      });
  }

  onIBKRActivated(account: BrokerAccount): void {
    // Activation succeeded — navigate to the overview page for the now-
    // real account id.
    this.ibkrDraft.set(null);
    this.router.navigate(['/broker-accounts', account.id]);
  }

  onIBKRCancel(): void {
    // User bailed mid-wizard. The `pending-{uuid}` row stays in the DB
    // for the user to delete from the Accounts page (no automatic GC in
    // v1 — see Risks #8 of the phase plan). Send the user back there.
    this.ibkrDraft.set(null);
    this.router.navigate(['/broker-accounts']);
  }

  onTSActivated(account: BrokerAccount): void {
    this.tsDraft.set(null);
    this.router.navigate(['/broker-accounts', account.id]);
  }

  onTSCancel(): void {
    // Draft row stays in the DB for the user to delete from the
    // Accounts page — same policy as IBKR.
    this.tsDraft.set(null);
    this.router.navigate(['/broker-accounts']);
  }
}
