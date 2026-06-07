import { type Page, type Locator } from '@playwright/test';

/** Page Object for Runs list (`/runs`) + Run detail (`/runs/:id`). */
export class RunsPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/runs');
  }
  async gotoNew(query = ''): Promise<void> {
    await this.page.goto(`/runs/new${query}`);
  }
  async gotoDetail(id: number, tab?: string): Promise<void> {
    await this.page.goto(`/runs/${id}${tab ? `?tab=${tab}` : ''}`);
  }

  // ---- new run ----
  newHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'New analysis' });
  }
  tickerInput(): Locator {
    return this.page.locator('#run-ticker');
  }
  personaCheckboxes(): Locator {
    return this.page.locator('hf-persona-card input[type="checkbox"]');
  }
  runCouncil(): Locator {
    return this.page.getByRole('button', { name: 'Run council' });
  }

  // ---- list ----
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Runs' });
  }
  countHeading(): Locator {
    return this.page.getByRole('heading', { name: /All runs/ });
  }
  statusFilter(label: string): Locator {
    return this.page.getByRole('group', { name: 'Filter by status' }).getByRole('button', { name: label });
  }
  sourceFilter(label: string): Locator {
    return this.page.getByRole('group', { name: 'Filter by source' }).getByRole('button', { name: label });
  }
  search(): Locator {
    return this.page.getByRole('searchbox', { name: 'Search run transcripts' });
  }
  rows(): Locator {
    return this.page.getByRole('row').filter({ hasText: '#' });
  }
  newRunCta(): Locator {
    return this.page.getByRole('link', { name: 'New run' });
  }
  emptyNoRuns(): Locator {
    return this.page.getByText("You haven't started any runs yet.");
  }
  emptyNoMatch(): Locator {
    return this.page.getByText('No runs match the current filters.');
  }

  // ---- detail ----
  detailHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: /Run #/ });
  }
  tab(name: string): Locator {
    return this.page.getByRole('tab', { name });
  }
  tabPanel(id: string): Locator {
    return this.page.locator(`#tab-${id}`);
  }
  stopAnalysis(): Locator {
    return this.page.getByRole('button', { name: 'Stop analysis' });
  }
}
