import { type Page, type Locator } from '@playwright/test';

/** Page Object for the Screener (`/screener`). */
export class ScreenerPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/screener');
  }

  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Market Screener' });
  }
  presetBar(): Locator {
    return this.page.getByRole('group', { name: 'Predefined screens' });
  }
  presetChip(name: string): Locator {
    return this.presetBar().getByRole('button', { name });
  }
  runScreen(): Locator {
    return this.page.getByRole('button', { name: 'Run screen' });
  }
  resultsTable(): Locator {
    return this.page.locator('hf-screener-results-table table');
  }
  resultRows(): Locator {
    return this.resultsTable().locator('tbody tr');
  }
  emptyResults(): Locator {
    return this.page.getByText('Try loosening your filters', { exact: false });
  }
}
