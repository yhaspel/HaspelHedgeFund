import { type Page, type Locator } from '@playwright/test';

/** Page Object for the Leaderboard (`/leaderboard`). */
export class LeaderboardPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/leaderboard');
  }

  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Leaderboard' });
  }
  windowSelect(): Locator {
    return this.page.getByLabel('Window');
  }
  tab(name: 'Agents' | 'Strategies'): Locator {
    return this.page.getByRole('tab', { name });
  }
  topPersonas(): Locator {
    return this.page.getByRole('heading', { name: 'Top personas' });
  }
  personaRows(): Locator {
    return this.page.locator('table.tbl tbody tr.clk');
  }
  drillPanel(): Locator {
    return this.page.getByRole('heading', { name: /^Decisions —/ });
  }
  emptyAgents(): Locator {
    return this.page.getByText('No scored decisions yet for this window.');
  }
  flavorBenchmarks(): Locator {
    return this.page.getByRole('heading', { name: 'Flavor benchmarks' });
  }
}
