import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { OrderConfirmModalComponent } from './order-confirm.modal';
import { TickerComponent } from '../shared/ticker.component';
import { BrokerStore } from '../../abstraction/broker.store';
import {
  BrokerAccount,
  BrokerOrderRow,
} from '../../core/models/broker.model';

/**
 * Cross-account pending orders page. The single batch-review surface for
 * draft / confirmed BrokerOrders — broken-leg groups share a group_id so
 * the user can resolve them together.
 */
@Component({
  selector: 'hf-pending-orders-page',
  standalone: true,
  imports: [
    CommonModule, DecimalPipe, RouterLink,
    AppShellComponent, EmptyStateComponent, OrderConfirmModalComponent,
    TickerComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[
      {label:'Broker accounts', link:'/broker-accounts'},
      {label:'Pending orders'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Trading</div>
          <h1 class="mt-1.5">Pending orders</h1>
          <p class="text-xs text-text-2 mt-1">
            Draft and confirmed orders across every broker account.
            Confirming runs the gate (re-price, risk check, notional &amp;
            live phrase, disclaimer) and submits to the broker.
          </p>
        </div>
      </div>

      @if (loading()) {
        <div class="card p-4 text-xs text-text-3">Loading…</div>
      } @else if (pending().length === 0) {
        <hf-empty-state
          message="No pending orders"
          detail="Create a draft from an account page or from a council run." />
      } @else {
        <section class="card p-0 overflow-hidden">
          <table class="tbl w-full" data-test="pending-table">
            <thead>
              <tr>
                <th class="text-left">Account</th>
                <th class="text-left">Ticker</th>
                <th class="text-left">Side</th>
                <th class="text-right">Quantity</th>
                <th class="text-right">Est. notional</th>
                <th class="text-left">Status</th>
                <th class="text-left">Group</th>
                <th class="text-right"></th>
              </tr>
            </thead>
            <tbody>
              @for (ord of pending(); track ord.id) {
                <tr [attr.data-test]="'pending-row-' + ord.id">
                  <td>
                    @if (accountFor(ord); as acc) {
                      <a [routerLink]="['/broker-accounts', acc.id]" class="link">{{ acc.label }}</a>
                    } @else {
                      <span class="text-text-3">{{ ord.broker_account }}</span>
                    }
                  </td>
                  <td class="font-medium">
                    <hf-ticker [ticker]="ord.ticker"></hf-ticker>
                    @if (ord.legs.length) {
                      <span class="pill ml-1.5 text-[10px]"
                            [attr.data-test]="'group-kind-' + ord.id">
                        <span class="dot"></span>{{ groupKind(ord) }}
                      </span>
                    }
                  </td>
                  <td>
                    <span class="pill" [class.ok]="ord.side === 'buy'" [class.warn]="ord.side === 'sell'">
                      <span class="dot"></span>{{ ord.side }}
                    </span>
                  </td>
                  <td class="text-right mono">{{ +ord.quantity | number: '1.0-4' }}</td>
                  <td class="text-right mono">{{ '$' + (+ord.notional_estimate | number: '1.2-2') }}</td>
                  <td>
                    <span class="pill"><span class="dot"></span>{{ ord.group_status || ord.status }}</span>
                  </td>
                  <td class="text-[11.5px] text-text-3 mono">
                    @if (ord.group_id) { {{ ord.group_id.slice(0, 8) }}… } @else { — }
                  </td>
                  <td class="text-right">
                    <button class="btn primary btn-sm" (click)="openConfirm(ord)"
                            [attr.data-test]="'confirm-' + ord.id">
                      Confirm
                    </button>
                  </td>
                </tr>
                @for (leg of ord.legs; track leg.id) {
                  <tr class="text-text-3" [attr.data-test]="'pending-leg-' + leg.id">
                    <td></td>
                    <td class="pl-4 text-[11.5px]">↳ {{ legLabel(leg) }}</td>
                    <td>
                      <span class="pill" [class.ok]="leg.side === 'buy'" [class.warn]="leg.side === 'sell'">
                        <span class="dot"></span>{{ leg.side }}
                      </span>
                    </td>
                    <td class="text-right mono text-[11.5px]">{{ +leg.quantity | number: '1.0-4' }}</td>
                    <td></td>
                    <td><span class="pill text-[10px]"><span class="dot"></span>{{ leg.status }}</span></td>
                    <td colspan="2"></td>
                  </tr>
                }
              }
            </tbody>
          </table>
        </section>
      }
    </hf-app-shell>

    @if (confirming(); as ord) {
      <hf-order-confirm-modal
        [order]="ord"
        [account]="accountFor(ord)"
        (closed)="confirming.set(null)"
        (confirmed)="onConfirmed()" />
    }
  `,
})
export class PendingOrdersPage implements OnInit {
  private readonly store = inject(BrokerStore);
  protected readonly orders = this.store.orders;
  protected readonly accounts = this.store.accounts;
  protected readonly loading = signal(true);
  protected readonly confirming = signal<BrokerOrderRow | null>(null);
  // Top-level rows only — a group's protective legs render nested under their
  // anchor (entry / OCO primary), never as standalone rows.
  protected readonly pending = computed(() =>
    this.orders().filter(
      (o) =>
        (o.status === 'draft' || o.status === 'confirmed') &&
        o.parent_order === null,
    ),
  );

  ngOnInit(): void {
    this.refresh();
  }

  groupKind(ord: BrokerOrderRow): string {
    const hasTp = ord.legs.some((l) => l.leg_role === 'take_profit');
    const hasSl = ord.legs.some((l) => l.leg_role === 'stop_loss');
    if (ord.leg_role === 'take_profit') return 'OCO';
    if (hasTp && hasSl) return 'bracket';
    return 'OTO';
  }

  legLabel(leg: BrokerOrderRow): string {
    const role =
      leg.leg_role === 'stop_loss'
        ? 'Stop-loss'
        : leg.leg_role === 'take_profit'
          ? 'Take-profit'
          : leg.order_type;
    let price = '';
    if (leg.order_type === 'limit' && leg.limit_price) {
      price = '$' + Number(leg.limit_price).toFixed(2);
    } else if (
      (leg.order_type === 'stop' || leg.order_type === 'stop_limit') &&
      leg.stop_price
    ) {
      price = '$' + Number(leg.stop_price).toFixed(2);
      if (leg.order_type === 'stop_limit' && leg.limit_price) {
        price += ' / $' + Number(leg.limit_price).toFixed(2);
      }
    }
    return `${role} · ${leg.order_type} ${price}`.trim();
  }

  refresh(): void {
    this.loading.set(true);
    this.store.loadAccounts().subscribe();
    this.store.loadOrders(undefined, 'draft,confirmed').subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  accountFor(ord: BrokerOrderRow): BrokerAccount | null {
    return this.accounts().find((a) => a.id === ord.broker_account) ?? null;
  }

  openConfirm(ord: BrokerOrderRow): void {
    this.confirming.set(ord);
  }

  onConfirmed(): void {
    this.confirming.set(null);
    this.refresh();
  }
}
