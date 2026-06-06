import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { ConfirmService } from '../shared/confirm.service';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerAccount } from '../../core/models/broker.model';

@Component({
  selector: 'hf-broker-accounts-page',
  standalone: true,
  imports: [CommonModule, DatePipe, AppShellComponent, EmptyStateComponent, RouterLink],
  template: `
    <hf-app-shell [crumbs]="[{label:'Broker accounts'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Trading</div>
          <h1 class="mt-1.5">Broker accounts</h1>
          <p class="text-xs text-text-2 mt-1">
            Connect a broker so the council can submit orders. The
            <strong>Demo broker</strong> is fully wired here and requires
            no real account — use it to walk the full lifecycle end-to-end.
          </p>
        </div>
        <div class="head-actions">
          <a routerLink="/broker-accounts/connect" class="btn primary" data-test="connect-btn">
            <svg width="14" height="14" class="mr-1.5" aria-hidden="true">
              <use href="/icons.svg#i-plus" /></svg>
            Connect an account
          </a>
        </div>
      </div>

      @if (error(); as msg) {
        <div role="alert" class="pill err h-auto py-2 px-3 mb-3.5"><span class="dot"></span>{{ msg }}</div>
      }

      @if (loading()) {
        <div class="card p-4 text-xs text-text-3">Loading accounts…</div>
      } @else if (accounts().length === 0) {
        <hf-empty-state
          message="No broker accounts yet"
          detail="Connect a Demo broker account to walk through the order lifecycle without a real brokerage.">
          <a class="btn primary mt-2" routerLink="/broker-accounts/connect">
            Connect Demo broker
          </a>
        </hf-empty-state>
      } @else {
        <section class="card p-0 overflow-hidden">
          <table class="tbl w-full" data-test="accounts-table">
            <thead>
              <tr>
                <th class="text-left">Account</th>
                <th class="text-left">Broker</th>
                <th class="text-left">Mode</th>
                <th class="text-left">Status</th>
                <th class="text-left">Last sync</th>
                <th class="text-right"></th>
              </tr>
            </thead>
            <tbody>
              @for (a of accounts(); track a.id) {
                <tr [attr.data-test]="'account-row-' + a.id">
                  <td>
                    <div class="font-medium">{{ a.label }}</div>
                    <div class="text-[11px] text-text-3 mono">{{ a.account_id }}</div>
                  </td>
                  <td>{{ a.broker_display }}</td>
                  <td>
                    <span class="pill" [class.warn]="a.mode === 'live'" [class.ok]="a.mode === 'paper'">
                      <span class="dot"></span>{{ a.mode }}
                    </span>
                  </td>
                  <td>
                    <span class="pill" [class.ok]="a.connection_status === 'active'"
                          [class.err]="a.connection_status === 'error' || a.connection_status === 'needs_reauth'">
                      <span class="dot"></span>{{ a.connection_status }}
                    </span>
                    @if (a.drift_pending) {
                      <span class="pill warn ml-1.5">
                        <span class="dot"></span>drift
                      </span>
                    }
                  </td>
                  <td class="text-[11.5px] text-text-3">
                    @if (a.last_synced_at) { {{ a.last_synced_at | date:'short' }} } @else { — }
                  </td>
                  <td class="text-right">
                    @if (a.connection_status === 'active') {
                      <a [routerLink]="['/broker-accounts', a.id]" class="btn ghost btn-sm"
                         [attr.data-test]="'open-account-' + a.id">Open</a>
                    } @else {
                      <a routerLink="/broker-accounts/connect"
                         [queryParams]="{ resume: a.id }"
                         class="btn ghost btn-sm"
                         [attr.data-test]="'resume-account-' + a.id">
                        Resume setup
                      </a>
                    }
                    @if (a.is_active && a.connection_status === 'active') {
                      <button class="btn ghost btn-sm ml-1.5" (click)="onDisconnect(a)"
                              [attr.data-test]="'disconnect-' + a.id">Disconnect</button>
                    }
                    <button class="btn ghost btn-sm ml-1.5" (click)="onDelete(a)"
                            [attr.data-test]="'delete-' + a.id">Delete</button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      }
    </hf-app-shell>
  `,
})
export class BrokerAccountsPage implements OnInit {
  private readonly store = inject(BrokerStore);
  private readonly router = inject(Router);
  private readonly confirm = inject(ConfirmService);

  protected readonly accounts = this.store.accounts;
  protected readonly busy = this.store.busy;
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.store.loadAccounts().subscribe({
      next: () => this.loading.set(false),
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'Failed to load accounts.');
        this.loading.set(false);
      },
    });
  }

  async onDisconnect(a: BrokerAccount): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Disconnect ${a.label}?`,
      body: 'Credentials will be zeroed.',
      confirmLabel: 'Disconnect',
      danger: true,
    });
    if (!ok) return;
    this.store.disconnectAccount(a.id).subscribe({
      next: () => this.store.loadAccounts().subscribe(),
      error: (err) => this.error.set(err?.error?.detail ?? 'Disconnect failed.'),
    });
  }

  async onDelete(a: BrokerAccount): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Permanently delete ${a.label}?`,
      body:
        'This removes the broker account row, its credential, and any local '
        + 'orders/fills. The account at the broker is not affected.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    this.store.deleteAccount(a.id).subscribe({
      next: () => this.store.loadAccounts().subscribe(),
      error: (err) => this.error.set(err?.error?.detail ?? 'Delete failed.'),
    });
  }
}
