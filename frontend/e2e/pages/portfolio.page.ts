import { type Page, type Locator } from '@playwright/test';

/** Page Object for Portfolios hub (`/portfolios`) + Manual Book (`/portfolio`)
 *  and the shared enter-position / cash-adjust modals. */
export class PortfolioPage {
  constructor(public readonly page: Page) {}

  async gotoHub(): Promise<void> {
    await this.page.goto('/portfolios');
  }
  async goto(): Promise<void> {
    await this.page.goto('/portfolio');
  }

  // ---- hub ----
  hubHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Portfolios' });
  }
  // Hub books are clickable rows (`<tr [routerLink]>`), not anchors.
  manualBookLink(): Locator {
    return this.page.getByRole('row').filter({ hasText: 'Manual book' });
  }

  // ---- manual book ----
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Manual book' });
  }
  positionsTable(): Locator {
    return this.page.locator('hf-positions-table table');
  }
  positionRow(ticker: string): Locator {
    return this.positionsTable().getByRole('row').filter({ hasText: ticker }).first();
  }
  emptyPositions(): Locator {
    return this.page.getByText('No positions yet.');
  }
  addPositionButton(): Locator {
    return this.page.locator('[data-test="add-position-btn"]');
  }
  cashButton(): Locator {
    return this.page.getByRole('button', { name: /Cash deposit/ });
  }

  // ---- enter-position modal ----
  enterModal(): Locator {
    return this.page.getByRole('dialog');
  }
  tickerInput(): Locator {
    return this.enterModal().getByRole('textbox').first();
  }
  sizeInput(): Locator {
    return this.enterModal().getByRole('spinbutton').first();
  }
  priceInput(): Locator {
    return this.enterModal().getByRole('spinbutton').nth(1);
  }
  sideButton(side: 'Long' | 'Short'): Locator {
    return this.enterModal().getByRole('button', { name: side });
  }
  quantityModeButton(mode: 'Whole shares' | 'Fractional'): Locator {
    return this.enterModal().getByRole('button', { name: mode });
  }
  confirmPosition(): Locator {
    return this.enterModal().getByRole('button', { name: /Confirm position|Increase position/ });
  }
  cancelButton(): Locator {
    return this.enterModal().getByRole('button', { name: 'Cancel' });
  }

  // ---- cash-adjust modal ----
  cashModalTitle(): Locator {
    return this.page.getByRole('heading', { name: 'Adjust manual cash' });
  }
  depositRadio(): Locator {
    return this.page.getByRole('radio', { name: 'Deposit' });
  }
  amountInput(): Locator {
    return this.page.locator('#cash-amount');
  }
}
