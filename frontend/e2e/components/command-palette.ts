import { type Page, type Locator } from '@playwright/test';

/** ⌘K command palette (ADR 0004) — rendered inside hf-modal (role=dialog). */
export class CommandPalette {
  constructor(public readonly page: Page) {}

  dialog(): Locator {
    return this.page.getByRole('dialog');
  }
  searchInput(): Locator {
    return this.page.getByRole('textbox', { name: 'Search runs, strategies, backtests' });
  }
  results(): Locator {
    return this.page.getByRole('listbox', { name: 'Search results' });
  }
  options(): Locator {
    return this.results().getByRole('option');
  }
  activeOption(): Locator {
    return this.results().locator('[role=option][aria-selected="true"]');
  }

  async type(query: string): Promise<void> {
    await this.searchInput().fill(query);
  }
}
