import { test, expect } from '../../fixtures';

/**
 * WS-10 · Watchlist.
 *
 * Two surfaces are exercised:
 *  - The full-page manager at /watchlist (hf-watchlists-manager-page). It drives
 *    the NUMERIC-id endpoints (GET /watchlists/, GET /watchlists/:id/,
 *    POST/DELETE /watchlists/:id/tickers/…). The default-list registry alias
 *    (/watchlists/default/) is NOT what this page calls, so GET /watchlists/:id/
 *    is overridden per-test (no registry entry exists for it).
 *  - The compact Dashboard card (hf-watchlist-card) backed by WatchlistStore,
 *    which targets the /watchlists/default/ alias the plan names. Add/remove on
 *    the card update the store signal optimistically, so the change is observable
 *    without a re-fetch — the manager instead re-GETs, which static mocks cannot
 *    differentiate, so the "appears/disappears" rows are asserted on the card.
 */
test.describe('WS-10 · Watchlist', () => {
  // A list-detail body for the default (id 1) list the manager auto-selects.
  const detail = (tickers: string[]) => ({
    id: 1,
    name: 'My Watchlist',
    is_default: true,
    items: tickers.map((t, i) => ({
      id: i + 1,
      ticker: t,
      note: '',
      price: '100.00',
      change_pct: 1.23,
    })),
  });

  test('WL-01 manager lists tickers from the default list', async ({ watchlist, apiMock }) => {
    // GET /watchlists/:id/ has no registry entry; the manager needs it for detail.
    apiMock.override('GET', '/watchlists/:id/', { json: detail(['AAPL', 'MSFT', 'NVDA']) });
    await watchlist.goto();

    await expect(watchlist.heading()).toBeVisible();
    // Default list chip from watchlists-all.json.
    await expect(watchlist.listChip('My Watchlist')).toBeVisible();
    // Detail card titled with the selected list's name.
    await expect(watchlist.detailTitle('My Watchlist')).toBeVisible();
    // Tickers from the (overridden) detail render as table cells.
    await expect(watchlist.tickerCell('AAPL')).toBeVisible();
    await expect(watchlist.tickerCell('MSFT')).toBeVisible();
    await expect(watchlist.tickerCell('NVDA')).toBeVisible();
  });

  test('WL-02 add ticker (POST tickers) appears in the list', async ({ page, watchlist, apiMock }) => {
    // Card surface: WatchlistStore optimistically prepends the POST response,
    // so the new row appears with no re-fetch — the plan's /watchlists/default/ path.
    await watchlist.gotoDashboard();
    await expect(watchlist.cardHeading()).toBeVisible();
    await expect(watchlist.cardTicker('TSLA')).toHaveCount(0);

    const post = page.waitForRequest(
      (r) => r.method() === 'POST' && r.url().includes('/watchlists/default/tickers/'),
    );
    await watchlist.cardAddInput().fill('TSLA');
    await watchlist.cardAddInput().press('Enter');
    await post;

    await expect(watchlist.cardTicker('TSLA')).toBeVisible();
  });

  test('WL-03 remove ticker fires DELETE', async ({ page, watchlist, apiMock }) => {
    // Removal is a manager-only affordance (the card has no Remove button). The
    // manager re-GETs /watchlists/:id/ after the DELETE, and static mocks can't
    // return a smaller list on the re-fetch, so the row's *disappearance* isn't
    // observable here — the honest assertion is that the DELETE request fires
    // (matching the registry's DELETE /watchlists/default/tickers/:sym/ shape,
    // here against the numeric-id manager endpoint).
    apiMock.override('GET', '/watchlists/:id/', { json: detail(['AAPL', 'MSFT', 'NVDA']) });
    apiMock.override('DELETE', '/watchlists/:id/tickers/:sym/', { status: 204 });
    await watchlist.goto();
    await expect(watchlist.tickerCell('AAPL')).toBeVisible();

    const del = page.waitForRequest(
      (r) => r.method() === 'DELETE' && /\/watchlists\/1\/tickers\/AAPL\//.test(r.url()),
    );
    await watchlist.removeButton('AAPL').click();
    await del; // resolves only if the DELETE was issued
  });

  test('WL-04 card actions route to New Run', async ({ page, watchlist, apiMock }) => {
    // Dashboard card "Analyze →" routes to /runs/new?ticker=…
    await watchlist.gotoDashboard();
    await watchlist.cardAnalyzeButton().first().click();
    await expect(page).toHaveURL(/\/runs\/new\?ticker=/);

    // Manager per-row "Analyze" routes the same way.
    apiMock.override('GET', '/watchlists/:id/', { json: detail(['AAPL', 'MSFT']) });
    await watchlist.goto();
    await watchlist.analyzeButton('AAPL').click();
    await expect(page).toHaveURL(/\/runs\/new\?ticker=AAPL/);
  });

  test('WL-05 cross-surface consistency after navigation', async ({ page, watchlist, apiMock }) => {
    // Manager and card read different endpoints (numeric id vs /default/) but the
    // same underlying default list. Add on the card (optimistic), then navigate
    // to /watchlist and assert the manager renders the same default-list tickers
    // (from its GET /watchlists/:id/) without a forced reload.
    apiMock.override('GET', '/watchlists/:id/', { json: detail(['AVGO', 'TSM', 'AMZN']) });

    await watchlist.gotoDashboard();
    await expect(watchlist.cardTicker('AVGO')).toBeVisible();

    const post = page.waitForRequest(
      (r) => r.method() === 'POST' && r.url().includes('/watchlists/default/tickers/'),
    );
    await watchlist.cardAddInput().fill('PLTR');
    await watchlist.cardAddInput().press('Enter');
    await post;
    await expect(watchlist.cardTicker('PLTR')).toBeVisible();

    // SPA navigation (router link) — no full reload.
    await watchlist.manageLink().click();
    await expect(page).toHaveURL(/\/watchlist$/);
    await expect(watchlist.heading()).toBeVisible();
    // The manager surface reflects the same default-list data.
    await expect(watchlist.tickerCell('AVGO')).toBeVisible();
    await expect(watchlist.tickerCell('TSM')).toBeVisible();
  });

  test('WL-06 empty watchlist shows empty state with add CTA', async ({ watchlist, apiMock }) => {
    apiMock.override('GET', '/watchlists/default/', { json: { id: 1, name: 'My Watchlist', is_default: true, items: [] } });
    await watchlist.gotoDashboard();

    await expect(watchlist.cardHeading()).toBeVisible();
    await expect(watchlist.cardEmptyState()).toBeVisible();
    // Add CTA (input + button) is always available below the list.
    await expect(watchlist.cardAddInput()).toBeVisible();
  });
});
