import { type Page, type Locator } from '@playwright/test';

/** Page Object for Guides/Info (`/info`, `/info/:slug`) — public, static content. */
export class InfoPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/info');
  }
  async gotoGuide(slug: string): Promise<void> {
    await this.page.goto(`/info/${slug}`);
  }

  listHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Guides' });
  }
  guideLink(name: string | RegExp): Locator {
    return this.page.getByRole('link', { name });
  }
  detailHeading(name: string | RegExp): Locator {
    return this.page.getByRole('heading', { level: 1, name });
  }
}
