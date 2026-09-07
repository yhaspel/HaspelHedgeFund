import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { BrokerStore } from '../../abstraction/broker.store';
import { ConfirmService } from '../shared/confirm.service';
import { BrokerAccountsPage } from './accounts.page';
import { BrokerAccount } from '../../core/models/broker.model';

/**
 * WP F1 — DELETE /api/broker-accounts/<id>/ can now answer
 * 409 `{detail, code:"open_orders", open_orders:[{id,ticker,status}]}`.
 *
 * The page showed only `error.detail`, so the user was told "there are open
 * orders" without being told WHICH — and with 18 held orders on this account
 * that is not an answer they can act on.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

const account = { id: 1, label: 'Alpaca paper' } as BrokerAccount;

function build(deleteResult: unknown): Cmp {
  TestBed.resetTestingModule();
  const store = {
    accounts: signal([account]).asReadonly(),
    loadAccounts: () => of([account]),
    deleteAccount: () => deleteResult,
  } as unknown as BrokerStore;
  TestBed.configureTestingModule({
    providers: [
      { provide: BrokerStore, useValue: store },
      { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
      { provide: Router, useValue: { navigate: vi.fn() } },
    ],
  });
  return TestBed.runInInjectionContext(() => new BrokerAccountsPage());
}

describe('fix-f1 · broker account delete 409', () => {
  it('names the orders that are blocking the delete', async () => {
    const page = build(
      throwError(() => ({
        status: 409,
        error: {
          detail: 'This account still has open orders.',
          code: 'open_orders',
          open_orders: [
            { id: 1, ticker: 'AAPL', status: 'pending_open' },
            { id: 2, ticker: 'MSFT', status: 'submitted' },
          ],
        },
      })),
    );
    await page.onDelete(account);
    const msg: string = page.error();
    expect(msg).toContain('This account still has open orders.');
    expect(msg).toContain('AAPL (pending_open)');
    expect(msg).toContain('MSFT (submitted)');
    expect(msg).toContain('Cancel them first');
  });

  it('truncates a long blocking list rather than dumping 18 rows', async () => {
    const page = build(
      throwError(() => ({
        status: 409,
        error: {
          detail: 'Open orders.',
          code: 'open_orders',
          open_orders: Array.from({ length: 18 }, (_, i) => ({
            id: i,
            ticker: `T${i}`,
            status: 'pending_open',
          })),
        },
      })),
    );
    await page.onDelete(account);
    expect(page.error()).toContain('+10 more');
  });

  it('falls back to the flattened message for any other failure', async () => {
    const page = build(throwError(() => ({ status: 500, error: { detail: 'kaboom' } })));
    await page.onDelete(account);
    expect(page.error()).toBe('kaboom');
  });

  it('clears the previous error on a successful delete', async () => {
    const page = build(of(undefined));
    page.error.set('stale');
    await page.onDelete(account);
    expect(page.error()).toBeNull();
  });
});
