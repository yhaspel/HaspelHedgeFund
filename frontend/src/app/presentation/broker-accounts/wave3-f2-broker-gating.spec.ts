import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { BrokerCapability } from '../../core/models/broker.model';
import { BrokerConnectWizardPage } from './connect-wizard.page';

/**
 * WAVE 3 F2 item 7 — the broker deployment gate.
 *
 * The wizard used to gate on `available` plus a hard-coded
 * `code !== 'ibkr' && code !== 'tradestation'`. `available` is still TRUE for
 * both of those (the adapter exists), so the real gate is the new `enabled`
 * flag: a broker switched off by `ENABLED_BROKERS` stayed clickable and only
 * failed at submit, with a 400 whose message never reached the screen.
 */

function cap(patch: Partial<BrokerCapability> = {}): BrokerCapability {
  return {
    code: 'mock',
    display_name: 'Demo broker',
    auth_kind: 'none',
    supports_paper: true,
    supports_live: false,
    supports_fractional: true,
    quantity_increment: '0.000001',
    supported_order_types: ['market'],
    supported_time_in_force: ['day'],
    supports_bracket: false,
    description: 'In-memory demo book.',
    available: true,
    community_unverified: false,
    connect_form: [],
    enabled: true,
    status: 'enabled',
    note: '',
    ...patch,
  };
}

const IBKR = cap({
  code: 'ibkr',
  display_name: 'Interactive Brokers',
  auth_kind: 'gateway_session',
  supports_live: true,
  // NOTE: still `available: true` — the adapter exists. Only `enabled` gates.
  available: true,
  enabled: false,
  status: 'deferred',
  note: 'IBKR is a deferred phase (P3a-2). The adapter and the Client Portal Gateway plumbing exist but the integration has not been validated end to end, so it cannot be connected yet.',
});

describe('wave3-f2 · broker connect wizard gating', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [BrokerConnectWizardPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap({}) } },
        },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  function mount(brokers: BrokerCapability[]) {
    const fixture = TestBed.createComponent(BrokerConnectWizardPage);
    fixture.detectChanges();
    http.expectOne((r) => r.url.endsWith('/brokers/')).flush({ brokers });
    fixture.detectChanges();
    return fixture;
  }

  it('disables a row whose `enabled` is false even though `available` is true', () => {
    const el: HTMLElement = mount([cap(), IBKR]).nativeElement;
    expect(el.querySelector('[data-test="select-broker-mock"]')).not.toBeNull();
    expect(el.querySelector('[data-test="select-broker-ibkr"]')).toBeNull();
    const disabled = el.querySelector(
      '[data-test="select-broker-disabled-ibkr"]',
    ) as HTMLButtonElement;
    expect(disabled.disabled).toBe(true);
    expect(disabled.getAttribute('title')).toContain('deferred phase');
  });

  it('shows the gate’s own note on the tile', () => {
    const el: HTMLElement = mount([cap(), IBKR]).nativeElement;
    expect(el.querySelector('[data-test="broker-note-ibkr"]')!.textContent).toContain(
      'has not been validated end to end',
    );
    expect(el.querySelector('[data-test="broker-status-ibkr"]')!.textContent).toContain(
      'later release',
    );
    // …and nothing of the sort on an enabled broker.
    expect(el.querySelector('[data-test="broker-note-mock"]')).toBeNull();
  });

  it('labels a switched-off (not deferred) broker as switched off', () => {
    const off = cap({
      code: 'alpaca_paper',
      display_name: 'Alpaca (paper)',
      enabled: false,
      status: 'disabled',
      note: 'Alpaca (paper) is switched off for this deployment (ENABLED_BROKERS does not list it).',
    });
    const el: HTMLElement = mount([cap(), off]).nativeElement;
    expect(el.querySelector('[data-test="broker-status-alpaca_paper"]')!.textContent).toContain(
      'switched off',
    );
    expect(el.querySelector('[data-test="broker-note-alpaca_paper"]')!.textContent).toContain(
      'ENABLED_BROKERS',
    );
  });

  it('refuses to select a gated broker even if select() is called directly', () => {
    const fixture = mount([cap(), IBKR]);
    fixture.componentInstance.select(IBKR);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="connect-form"]')).toBeNull();
  });

  it('falls back to `available` when the server predates the gate (no `enabled` key)', () => {
    const legacy = cap({ code: 'legacy', display_name: 'Legacy', enabled: undefined, status: undefined, note: undefined });
    const gone = cap({ code: 'gone', display_name: 'Gone', available: false, enabled: undefined, status: undefined, note: undefined });
    const el: HTMLElement = mount([legacy, gone]).nativeElement;
    expect(el.querySelector('[data-test="select-broker-legacy"]')).not.toBeNull();
    expect(el.querySelector('[data-test="select-broker-disabled-gone"]')).not.toBeNull();
  });

  it('renders the 400 `{broker: "<string>"}` body verbatim (a string, not a DRF list)', () => {
    const fixture = mount([cap()]);
    const el: HTMLElement = fixture.nativeElement;
    (el.querySelector('[data-test="select-broker-mock"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    (el.querySelector('[data-test="label-input"]') as HTMLInputElement).value = 'Demo book';
    fixture.componentInstance['label'] = 'Demo book';
    fixture.detectChanges();
    (el.querySelector('[data-test="create-account-btn"]') as HTMLButtonElement).click();

    http.expectOne((r) => r.method === 'POST' && r.url.endsWith('/broker-accounts/')).flush(
      { broker: 'Demo broker is switched off for this deployment (ENABLED_BROKERS does not list it).' },
      { status: 400, statusText: 'Bad Request' },
    );
    // The wizard re-reads the registry after a gate rejection.
    http.expectOne((r) => r.url.endsWith('/brokers/')).flush({ brokers: [cap()] });
    fixture.detectChanges();

    const alert = el.querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain('switched off for this deployment');
  });
});
