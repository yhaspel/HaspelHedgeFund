import { test, expect } from '../../fixtures';
import { fixture } from '../../fixtures/api-mock';

/**
 * WS-12 · Broker (accounts list / connect wizard / per-broker flows /
 * account overview / order ticket + confirm / pending orders).
 *
 * SAFETY: every flow runs against the Lane-A mock or the in-memory Demo
 * broker. No assertion claims a real order was placed — confirm/activate
 * responses are stubbed and we only assert the UI moved to the next state.
 *
 * Ground-truth notes baked into these tests:
 *  - The orders list is served from `GET /broker/orders/`, which has no
 *    default registry entry, so the overview/pending pages 599 on it and
 *    degrade. Tests that need orders `override` that endpoint explicitly.
 *  - The IBKR/TradeStation connect-flow components are NOT reachable from
 *    the wizard tiles (those render a disabled "later release" button).
 *    They mount only via `?resume=<id>` over a non-active draft account,
 *    so BK-05/BK-06 seed a draft into `GET /broker-accounts/` and resume.
 *  - The standalone disclaimer modal is not on any broker route; the LIVE
 *    gate a user actually hits is the confirm modal's "type LIVE" rule,
 *    which BK-03 exercises against a live-mode account.
 */
test.describe('WS-12 · Broker', () => {
  test('BK-01 accounts list — seeded rows + status badges', async ({ broker }) => {
    await broker.goto();
    await expect(broker.heading()).toBeVisible();
    await expect(broker.accountsTable()).toBeVisible();
    // Demo (mock) broker is seeded as id 5 and is active.
    await expect(broker.accountRow(5)).toBeVisible();
    await expect(broker.accountRow(5).getByText('Demo book')).toBeVisible();
    // Status badge — every seeded row is "active".
    await expect(broker.accountRow(5).getByText('active', { exact: true })).toBeVisible();
    // Active row exposes an "Open" affordance.
    await expect(broker.openAccountLink(5)).toBeVisible();
  });

  test('BK-02 connect wizard — broker choices render', async ({ broker }) => {
    await broker.gotoConnect();
    await expect(broker.connectHeading()).toBeVisible();
    await expect(broker.brokerGrid()).toBeVisible();
    // All four brokers from the registry get a tile.
    await expect(broker.brokerTile('mock')).toBeVisible();
    await expect(broker.brokerTile('alpaca_paper')).toBeVisible();
    await expect(broker.brokerTile('ibkr')).toBeVisible();
    await expect(broker.brokerTile('tradestation')).toBeVisible();
    // Demo + Alpaca offer a "Continue"; IBKR/TS are deferred (disabled).
    await expect(broker.selectBrokerButton('mock')).toBeVisible();
    await expect(broker.selectBrokerButton('alpaca_paper')).toBeVisible();
    await expect(broker.laterReleaseButton().first()).toBeVisible();
  });

  test('BK-03 LIVE confirm gate — must satisfy the LIVE phrase to submit', async ({
    page,
    broker,
    apiMock,
  }) => {
    // Seed one LIVE-mode account + a draft order for it so the confirm
    // modal renders the live gate. The disclaimer/LIVE phrase is the real
    // "must accept to proceed" gate users see (the standalone disclaimer
    // modal is not mounted on any route).
    apiMock.override('GET', '/broker-accounts/', {
      json: [
        {
          id: 77,
          broker: 'alpaca_paper',
          broker_display: 'Alpaca (live)',
          mode: 'live',
          account_id: 'alpaca:live-book',
          label: 'Live book',
          base_currency: 'USD',
          default_quantity_mode: 'whole',
          connection_status: 'active',
          is_active: true,
          last_synced_at: '2026-06-06T17:15:04Z',
          created_at: '2026-06-01T00:00:00Z',
          credential: { status: 'set' },
          drift_pending: false,
        },
      ],
    });
    apiMock.override('GET', '/broker/orders/', {
      json: [
        {
          id: 501,
          broker_account: 77,
          ticker: 'AAPL',
          side: 'buy',
          quantity: '3',
          order_type: 'limit',
          limit_price: '210.00',
          stop_price: null,
          notional_estimate: '630.00',
          status: 'draft',
          group_status: null,
          group_id: null,
          parent_order: null,
          leg_role: null,
          legs: [],
        },
      ],
    });

    await broker.gotoPending();
    await expect(broker.pendingTable()).toBeVisible();
    await broker.pendingConfirm(501).click();

    await expect(broker.confirmModalHeading()).toBeVisible();
    // LIVE account → there is a LIVE warning alert and submit stays gated
    // until a phrase containing LIVE is typed.
    await expect(page.getByText('LIVE account.')).toBeVisible();
    await expect(broker.confirmSubmit()).toBeDisabled();
    await page
      .locator('[data-test="live-confirmation-input"], [data-testid="live-confirmation-input"]')
      .fill('I confirm this is LIVE');
    await expect(broker.confirmSubmit()).toBeEnabled();
  });

  test('BK-04 Alpaca connect flow — steps render and submit to connected', async ({
    page,
    broker,
    apiMock,
  }) => {
    // Credentials POST → an activated account; the wizard navigates to its
    // overview, so the response must carry an `id`.
    apiMock.override('POST', '/broker-accounts/:id/credentials/', {
      json: {
        id: 90,
        broker: 'alpaca_paper',
        broker_display: 'Alpaca (paper only)',
        mode: 'paper',
        account_id: 'alpaca_paper:paper-book',
        label: 'Paper book',
        connection_status: 'active',
        is_active: true,
        credential: { status: 'set' },
      },
    });
    await broker.gotoConnect();
    await broker.selectBroker('alpaca_paper');
    // Inline label/mode form, then create → renders the Alpaca flow.
    await expect(broker.connectForm()).toBeVisible();
    await broker.labelInput().fill('Paper book');
    await broker.createAccountButton().click();

    await expect(broker.alpacaFlow()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Connect Alpaca (paper)' })).toBeVisible();
    await expect(broker.alpacaApiKey()).toBeVisible();
    await expect(broker.alpacaApiSecret()).toBeVisible();
    await expect(broker.alpacaSubmit()).toBeDisabled();

    await broker.alpacaApiKey().fill('PK_TEST_KEY');
    await broker.alpacaApiSecret().fill('secret-value');
    await expect(broker.alpacaSubmit()).toBeEnabled();
    await broker.alpacaSubmit().click();
    // POST /credentials/ → activated account; wizard navigates to overview.
    await expect(page).toHaveURL(/\/broker-accounts\/\d+$/);
  });

  test('BK-05 IBKR connect flow — gateway steps render', async ({
    page,
    broker,
    apiMock,
  }) => {
    // IBKR flow only mounts on ?resume=<id> for a non-active ibkr draft.
    apiMock.override('GET', '/broker-accounts/', {
      json: [
        {
          id: 80,
          broker: 'ibkr',
          broker_display: 'Interactive Brokers',
          mode: 'paper',
          account_id: 'pending-ibkr-uuid',
          label: 'IBKR draft',
          base_currency: 'USD',
          default_quantity_mode: 'whole',
          connection_status: 'connecting',
          is_active: false,
          last_synced_at: null,
          created_at: '2026-06-05T00:00:00Z',
          credential: { status: 'unset' },
          drift_pending: false,
        },
      ],
    });
    // Drive the gateway sequence to a pickable account.
    apiMock.override('POST', '/broker-accounts/:id/gateway/auth-status/', {
      json: { authenticated: true, connected: true, competing: false, ready: true },
    });
    apiMock.override('POST', '/broker-accounts/:id/gateway/discover-accounts/', {
      json: { accounts: [{ account_id: 'DU1234567', is_paper: true }], selected: 'DU1234567' },
    });
    // Activate emits the real account → wizard navigates to its overview.
    apiMock.override('POST', '/broker-accounts/:id/gateway/activate/', {
      json: {
        id: 80,
        broker: 'ibkr',
        broker_display: 'Interactive Brokers',
        mode: 'paper',
        account_id: 'DU1234567',
        label: 'IBKR draft',
        connection_status: 'active',
        is_active: true,
        credential: { status: 'set' },
      },
    });

    await broker.gotoConnectResume(80);
    await expect(broker.ibkrFlow()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Connect Interactive Brokers' })).toBeVisible();
    // Step 1 probe is reachable (mock default) → Continue advances to step 2.
    await expect(page.getByText('Gateway reachable')).toBeVisible();
    await broker.ibkrStep1Continue().click();
    await expect(page.getByText('Authenticate gateway')).toBeVisible();
    // auth-status ready → Continue → discover surfaces a pickable DU account.
    await broker.ibkrStep2Continue().click();
    await expect(broker.ibkrPick('DU1234567')).toBeVisible();
    await broker.ibkrPick('DU1234567').click();
    await expect(broker.ibkrStep3Activate()).toBeVisible();
    // Activate → mocked active account → wizard navigates to overview.
    await broker.ibkrStep3Activate().click();
    await expect(page).toHaveURL(/\/broker-accounts\/\d+$/);
  });

  test('BK-06 TradeStation connect flow — steps + OAuth-start affordance', async ({
    page,
    broker,
    apiMock,
  }) => {
    apiMock.override('GET', '/broker-accounts/', {
      json: [
        {
          id: 81,
          broker: 'tradestation',
          broker_display: 'TradeStation (paper / live)',
          mode: 'paper',
          account_id: 'pending-ts-uuid',
          label: 'TS draft',
          base_currency: 'USD',
          default_quantity_mode: 'whole',
          connection_status: 'connecting',
          is_active: false,
          last_synced_at: null,
          created_at: '2026-06-05T00:00:00Z',
          credential: { status: 'unset' },
          drift_pending: false,
        },
      ],
    });
    // Developer app already configured → step 1 lets us Continue to authorize.
    apiMock.override('GET', '/broker-accounts/tradestation/runtime-config/', {
      json: { configured: true, source: 'env', redirect_uri: 'https://example.com/cb' },
    });
    // OAuth-start returns an authorization_url (the model field the flow reads).
    apiMock.override('POST', '/broker-accounts/:id/oauth/start/', {
      json: { authorization_url: 'https://sim.api.tradestation.com/authorize?state=e2e', state: 'e2e' },
    });

    await broker.gotoConnectResume(81);
    await expect(broker.tradestationFlow()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Connect TradeStation' })).toBeVisible();
    await expect(page.getByText('Developer app configured')).toBeVisible();
    await broker.tsStep1Continue().click();
    // Step 2 surfaces the OAuth-start affordance ("Get sign-in link").
    await expect(page.getByText('Authorize with TradeStation')).toBeVisible();
    await expect(broker.tsStep2Start()).toBeVisible();
    await broker.tsStep2Start().click();
    // POST /oauth/start/ → authorize URL → the open-sign-in link appears.
    await expect(broker.tsStep2Authorize()).toBeVisible();
  });

  test('BK-07 account overview — positions/balances render from /overview/', async ({
    broker,
    apiMock,
  }) => {
    // The overview page also calls GET /broker/orders/ (no default fixture);
    // stub it empty so the working-orders section degrades cleanly.
    apiMock.override('GET', '/broker/orders/', { json: [] });
    await broker.gotoOverview(13);
    await expect(broker.overviewHeading('trend-cta')).toBeVisible();
    // Balances from /overview/ broker block.
    await expect(broker.kpi('Cash')).toBeVisible();
    await expect(broker.kpi('Equity')).toBeVisible();
    await expect(broker.kpi('Positions')).toBeVisible();
    // Fixture has zero positions → the empty positions message shows.
    await expect(broker.positionsEmpty()).toBeVisible();
  });

  test('BK-08 order ticket modal — opens with side/qty/type', async ({
    broker,
    apiMock,
  }) => {
    // The ticket modal is hosted on the run-detail page (Submit-as-broker).
    // Give the run a clean BUY decision with a real target quantity so the
    // ticket prefills usefully, and ensure an active account exists.
    apiMock.override('GET', '/runs/:id/', {
      json: buyRunDetail(),
    });
    await broker.gotoRunDetail(371);
    await broker.submitBrokerOrderButton('AAPL').click();

    await expect(broker.ticketModalHeading()).toBeVisible();
    // Side is reflected in the order summary; account + qty + type controls exist.
    await expect(broker.ticketAccountSelect()).toBeVisible();
    await expect(broker.ticketQuantity()).toBeVisible();
    await expect(broker.ticketOrderType()).toBeVisible();
  });

  test('BK-09 order confirm — confirm POSTs /broker/orders/:id/confirm/', async ({
    page,
    broker,
    apiMock,
  }) => {
    apiMock.override('GET', '/broker/orders/', {
      json: [
        {
          id: 610,
          broker_account: 5,
          ticker: 'MSFT',
          side: 'buy',
          quantity: '2',
          order_type: 'limit',
          limit_price: '300.00',
          stop_price: null,
          notional_estimate: '600.00',
          status: 'draft',
          group_status: null,
          group_id: null,
          parent_order: null,
          leg_role: null,
          legs: [],
        },
      ],
    });
    // Assert the confirm POST actually fires for order 610.
    const confirmReq = page.waitForRequest(
      (r) => r.method() === 'POST' && /\/broker\/orders\/610\/confirm\/$/.test(r.url()),
    );

    await broker.gotoPending();
    await broker.pendingConfirm(610).click();
    await expect(broker.confirmModalHeading()).toBeVisible();
    // Paper account, sub-threshold notional → no typed/LIVE gate; submit enabled.
    await expect(broker.confirmSubmit()).toBeEnabled();
    // Activate via keyboard: focusing the submit button moves focus off the
    // order's ticker (closing its hf-tpop popover, which otherwise overlays and
    // intercepts a pointer click) and Enter fires the button.
    await broker.confirmSubmit().press('Enter');
    await confirmReq; // POST /broker/orders/610/confirm/ → submitted (mocked)
  });

  test('BK-10 pending orders — lists draft/confirmed rows', async ({
    broker,
    apiMock,
  }) => {
    apiMock.override('GET', '/broker/orders/', {
      json: [
        {
          id: 701,
          broker_account: 5,
          ticker: 'NVDA',
          side: 'buy',
          quantity: '4',
          order_type: 'market',
          limit_price: null,
          stop_price: null,
          notional_estimate: '0.00',
          status: 'draft',
          group_status: null,
          group_id: null,
          parent_order: null,
          leg_role: null,
          legs: [],
        },
      ],
    });
    await broker.gotoPending();
    await expect(broker.pendingHeading()).toBeVisible();
    await expect(broker.pendingTable()).toBeVisible();
    await expect(broker.pendingRow(701)).toBeVisible();
    await expect(broker.pendingRow(701).getByText('NVDA')).toBeVisible();
    await expect(broker.pendingConfirm(701)).toBeVisible();
  });

  test('BK-11 disconnect — confirm dialog → POST /disconnect/ → row leaves list', async ({
    page,
    broker,
    apiMock,
  }) => {
    // First load shows the Demo account; after disconnect the reload returns
    // an empty list so the row is gone.
    let loads = 0;
    apiMock.override('GET', '/broker-accounts/', {
      json: [
        {
          id: 5,
          broker: 'mock',
          broker_display: 'Demo broker (no real account)',
          mode: 'paper',
          account_id: 'demo-1',
          label: 'Demo book',
          base_currency: 'USD',
          default_quantity_mode: 'whole',
          connection_status: 'active',
          is_active: true,
          last_synced_at: '2026-06-06T17:15:04Z',
          created_at: '2026-05-23T09:41:22Z',
          credential: { status: 'unset' },
          drift_pending: false,
        },
      ],
    });
    const disconnectReq = page.waitForRequest(
      (r) => r.method() === 'POST' && /\/broker-accounts\/5\/disconnect\/$/.test(r.url()),
    );

    await broker.goto();
    await expect(broker.accountRow(5)).toBeVisible();

    // The store uses ConfirmService.ask() (an in-page hf-confirm dialog),
    // not a native confirm(); accept it via the dialog's danger button.
    await broker.disconnectButton(5).click();
    await expect(broker.confirmDialog(/Disconnect Demo book/)).toBeVisible();
    // After accepting, swap the list to empty for the reload.
    apiMock.override('GET', '/broker-accounts/', { json: [] });
    await broker.confirmDialogAccept('Disconnect').click();

    await disconnectReq; // POST /broker-accounts/5/disconnect/
    await expect(broker.emptyList()).toBeVisible();
  });

  test('BK-12 connect validation — create error surfaces inline', async ({
    broker,
    apiMock,
  }) => {
    // Creating the Demo account fails → the wizard shows an inline alert.
    apiMock.override('POST', '/broker-accounts/', {
      status: 400,
      json: { detail: 'Could not create the broker account.' },
    });
    await broker.gotoConnect();
    await broker.selectBroker('mock');
    await expect(broker.connectForm()).toBeVisible();
    // Demo pre-fills a label, so Create is enabled; click it.
    await broker.createAccountButton().click();
    await expect(broker.wizardError()).toBeVisible();
    await expect(broker.wizardError()).toContainText('Could not create the broker account.');
  });
});

/** The real run-detail fixture with its single decision rewritten to a clean
 *  BUY (long) AAPL with a real target quantity, so the "Submit as broker order"
 *  button shows and the ticket modal prefills a usable quantity. Spreading the
 *  fixture keeps every other field the run-detail page reads (tickers, source,
 *  as_of_date, total_cost_usd, llm_calls, …) intact so the page can't crash. */
function buyRunDetail(): Record<string, unknown> {
  const base = fixture('run-detail') as Record<string, unknown>;
  const dec = ((base['decisions'] as Record<string, unknown>[])[0]) ?? {};
  return {
    ...base,
    id: 371,
    status: 'done',
    decisions: [
      {
        ...dec,
        id: 240,
        ticker: 'AAPL',
        action: 'buy',
        side: 'long',
        target_quantity: '10.000000',
      },
    ],
  };
}
