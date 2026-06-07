import { type Page, type Locator } from '@playwright/test';

/** Page Object for the Dashboard (`/`). */
export class DashboardPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/');
  }

  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Dashboard' });
  }
  navHero(): Locator {
    return this.page.getByRole('heading', { name: 'Net asset value' });
  }
  navValue(): Locator {
    return this.page.locator('.nav-hero .nav-val');
  }
  bookExposure(): Locator {
    return this.page.getByRole('heading', { name: 'Book exposure' });
  }
  regimeStrip(): Locator {
    return this.page.getByText('Macro regime', { exact: true });
  }
  sectorHeatmap(): Locator {
    return this.page.getByRole('heading', { name: 'Sector implications' });
  }
  watchlistCard(): Locator {
    return this.page.getByRole('heading', { name: 'Watchlist' });
  }
  sectorCell(name: string | RegExp): Locator {
    return this.page.getByLabel(name);
  }
  runRow(): Locator {
    return this.page.getByRole('link', { name: /^Open run/ });
  }
  strategiesViewAll(): Locator {
    return this.page.getByRole('link', { name: 'View all →' });
  }
  newAnalysisCta(): Locator {
    return this.page.getByRole('link', { name: 'New analysis' });
  }
}
