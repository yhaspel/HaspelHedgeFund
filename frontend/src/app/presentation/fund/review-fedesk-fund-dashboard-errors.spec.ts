import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { FundStore } from '../../abstraction/fund.store';
import { FundOverview } from '../../core/models/autopilot.model';
import { MacroStore } from '../../abstraction/macro.store';
import { ConfirmService } from '../shared/confirm.service';
import { FundDashboardPage } from './fund-dashboard.page';

/**
 * Review (fedesk) — fund dashboard error / dangerous-action behaviour.
 *
 * 1. GET /fund/ failing (5xx, network, expired auth) is indistinguishable
 *    from "no fund exists": the template branch is
 *    `@if (!fund() && !loading())` → "No autonomous fund yet" + the create
 *    form (fund-dashboard.page.ts:77-83).
 * 2. "Run now" on a member card fires a live paper-trading cycle with no
 *    confirm dialog, and a failure is silent (fund-dashboard.page.ts:1033-1043).
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ask = any;

function build(store: Partial<FundStore>, confirmAsk: Ask = vi.fn()) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      { provide: FundStore, useValue: { fund: signal(null).asReadonly(), ...store } },
      { provide: MacroStore, useValue: { snapshot: signal(null).asReadonly(), loadSnapshot: () => of(null) } },
      { provide: ConfirmService, useValue: { ask: confirmAsk } },
    ],
  });
  return TestBed.runInInjectionContext(() => new FundDashboardPage());
}

describe('review-fedesk · FundDashboardPage', () => {
  it('shows the "No autonomous fund yet" set-up flow when GET /fund/ fails', () => {
    const page = build({
      loadFund: () => throwError(() => ({ status: 503, error: { detail: 'upstream down' } })),
    });
    page.ngOnInit();
    // FIXED: the onboarding branch now also requires a SUCCESSFUL load…
    expect(!page.fund() && !page.loading() && !page.loadError() && page.loaded()).toBe(false);
    // …and the failure is surfaced with the server's own message + a retry.
    expect(page.loadError()).toBe('upstream down');
    expect(page.loaded()).toBe(false);
  });

  it('offers the set-up flow only on a successful {fund: null}', () => {
    // GET /fund/ resolving with no fund: the store leaves `fund()` null.
    const page = build({ loadFund: () => of(null as unknown as FundOverview) });
    page.ngOnInit();
    expect(page.loadError()).toBeNull();
    expect(!page.fund() && !page.loading() && !page.loadError() && page.loaded()).toBe(true);
  });

  it('fires a live autopilot cycle from the member card without any confirmation', async () => {
    const ask = vi.fn((_opts: { body?: string }) => Promise.resolve(true));
    const runNow = vi.fn(() => of({}));
    const page = build({ runNow }, ask);
    await page.runNow(7);
    // FIXED: gated like every other live action on this page.
    expect(ask).toHaveBeenCalledTimes(1);
    expect(String(ask.mock.calls[0][0].body)).toContain('paper brokerage account');
    expect(runNow).toHaveBeenCalledWith(7); // POST /strategies/7/autopilot/run-now/
  });

  it('does not run a cycle when the confirmation is declined', async () => {
    const ask = vi.fn(() => Promise.resolve(false));
    const runNow = vi.fn(() => of({}));
    const page = build({ runNow }, ask);
    await page.runNow(7);
    expect(runNow).not.toHaveBeenCalled();
    expect(page.queuedId()).toBeNull();
  });

  it('swallows a failed "Run now" (no message, no error state)', async () => {
    const page = build(
      { runNow: () => throwError(() => ({ status: 409, error: { detail: 'autopilot halted' } })) },
      vi.fn(() => Promise.resolve(true)),
    );
    await page.runNow(7);
    expect(page.runningId()).toBeNull();
    expect(page.queuedId()).toBeNull();
    // FIXED: the 409 detail reaches the user.
    expect(page.actionError()).toBe('autopilot halted');
  });
});
