import { type Page, type Locator } from '@playwright/test';

/** Page Object for Backtests (list / detail / new / compare). */
export class BacktestsPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/backtests');
  }
  async gotoNew(): Promise<void> {
    await this.page.goto('/backtests/new');
  }
  async gotoDetail(id: number): Promise<void> {
    await this.page.goto(`/backtests/${id}`);
  }
  async gotoCompare(id: number): Promise<void> {
    await this.page.goto(`/backtests/${id}/compare`);
  }

  // ---- list ----
  listHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Backtests' });
  }
  newCta(): Locator {
    return this.page.getByRole('link', { name: 'New walk-forward' });
  }
  rowLink(name: string | RegExp): Locator {
    return this.page.getByRole('link', { name });
  }
  emptyList(): Locator {
    return this.page.getByText('No backtests yet.');
  }

  // ---- detail ----
  detailHeading(name: string | RegExp): Locator {
    return this.page.getByRole('heading', { level: 1, name });
  }
  metricLabel(text: string): Locator {
    return this.page.getByText(text, { exact: true });
  }
  equityCanvas(): Locator {
    return this.page.getByRole('img', { name: /equity curve/i });
  }
  compareLink(): Locator {
    return this.page.getByRole('link', { name: 'Compare…' });
  }
  backToList(): Locator {
    return this.page.getByRole('link', { name: 'Back to list' });
  }

  // ---- new ----
  graphSelect(): Locator {
    return this.page.locator(`[data-test="bt-graph-select"], [data-testid="bt-graph-select"]`);
  }
  runButton(): Locator {
    return this.page.locator(`[data-test="bt-run-graph"], [data-testid="bt-run-graph"]`);
  }
  formError(): Locator {
    return this.page.getByRole('alert');
  }

  // ---- compare ----
  compareBSelect(): Locator {
    return this.page.locator('select').first();
  }
  metricsTable(): Locator {
    return this.page.getByRole('table');
  }
}
