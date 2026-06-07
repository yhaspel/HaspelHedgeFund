import { type Page, type Locator } from '@playwright/test';

/**
 * Page Object for the Watchlist surfaces.
 *
 * Two distinct surfaces are exercised here:
 *  - The full-page multi-list manager at `/watchlist` (WatchlistsManagerPage).
 *    It drives the numeric-id endpoints: GET /watchlists/, GET /watchlists/:id/,
 *    POST /watchlists/:id/tickers/, DELETE /watchlists/:id/tickers/:sym/.
 *  - The compact Watchlist card on the Dashboard (`/`, hf-watchlist-card),
 *    backed by WatchlistStore which targets the GET /watchlists/default/ alias.
 */
export class WatchlistPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/watchlist');
  }

  async gotoDashboard(): Promise<void> {
    await this.page.goto('/');
  }

  // ---- manager page (/watchlist) ------------------------------------------
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Watchlists' });
  }

  /** List-selector chip by its visible name (e.g. "My Watchlist"). */
  listChip(name: string | RegExp): Locator {
    return this.page.getByRole('button', { name });
  }

  /** The detail card title (h2) — equals the selected list's name. */
  detailTitle(name?: string | RegExp): Locator {
    return this.page.getByRole('heading', { level: 2, name });
  }

  addTickerInput(): Locator {
    return this.page.getByPlaceholder('Add ticker (e.g. AAPL)');
  }

  /** The add-row "Add" button (the new-list inline "Add" is hidden by default). */
  addTickerButton(): Locator {
    return this.page.getByRole('button', { name: 'Add', exact: true });
  }

  /** A table row scoped by its ticker text. */
  tickerRow(ticker: string): Locator {
    return this.page.getByRole('row').filter({ hasText: ticker });
  }

  tickerCell(ticker: string): Locator {
    return this.page.getByRole('cell', { name: ticker, exact: true });
  }

  /** Per-row "Analyze" action (routes to /runs/new?ticker=). */
  analyzeButton(ticker: string): Locator {
    return this.tickerRow(ticker).getByRole('button', { name: 'Analyze' });
  }

  /** Per-row "Remove" action. */
  removeButton(ticker: string): Locator {
    return this.tickerRow(ticker).getByRole('button', { name: 'Remove' });
  }

  emptyState(): Locator {
    return this.page.getByText('No tickers in this list yet.');
  }

  async addTicker(ticker: string): Promise<void> {
    await this.addTickerInput().fill(ticker);
    await this.addTickerButton().click();
  }

  // ---- dashboard card (/) -------------------------------------------------
  cardHeading(): Locator {
    return this.page.getByRole('heading', { level: 2, name: 'Watchlist' });
  }

  manageLink(): Locator {
    return this.page.getByRole('link', { name: 'Manage watchlist' });
  }

  /** A ticker entry inside the dashboard card list, scoped to the card. */
  cardTicker(ticker: string): Locator {
    return this.cardRegion().getByText(ticker, { exact: true });
  }

  cardAnalyzeButton(): Locator {
    return this.cardRegion().getByRole('button', { name: 'Analyze in New Run' });
  }

  cardAddInput(): Locator {
    return this.cardRegion().getByRole('textbox', { name: 'Add ticker' });
  }

  cardEmptyState(): Locator {
    return this.page.getByText('No tickers yet.');
  }

  /** The dashboard Watchlist card section (anchored on its h2). */
  private cardRegion(): Locator {
    return this.page
      .locator('section')
      .filter({ has: this.page.getByRole('heading', { level: 2, name: 'Watchlist' }) });
  }
}
