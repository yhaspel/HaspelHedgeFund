import { type Page, type Locator } from '@playwright/test';

/** Page Object for Strategies (list / new / detail). */
export class StrategiesPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/strategies');
  }
  async gotoNew(): Promise<void> {
    await this.page.goto('/strategies/new');
  }
  async gotoDetail(id: number): Promise<void> {
    await this.page.goto(`/strategies/${id}`);
  }

  // ---- list ----
  listHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Strategies' });
  }
  newCta(): Locator {
    return this.page.getByRole('link', { name: 'New strategy' });
  }
  openLink(): Locator {
    return this.page.getByRole('link', { name: 'Open' });
  }
  emptyList(): Locator {
    return this.page.getByText('No active strategies.');
  }

  // ---- new ----
  newHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'New strategy' });
  }
  nameInput(): Locator {
    return this.page.locator('input[name="name"]');
  }
  kindSelect(): Locator {
    return this.page.locator('#strat-kind');
  }
  universeSelect(): Locator {
    return this.page.locator('#strat-universe');
  }
  grossInput(): Locator {
    return this.page.locator('#strat-g');
  }
  netInput(): Locator {
    return this.page.locator('#strat-n');
  }
  // Persona checkboxes are visually-hidden inside hf-persona-card (toggle via the
  // label); scope to the component since the aria-label is dynamic per persona.
  personaCheckboxes(): Locator {
    return this.page.locator('hf-persona-card input[type="checkbox"]');
  }
  personaCount(): Locator {
    return this.page.getByText(/\d+ of \d+/).first();
  }
  createButton(): Locator {
    return this.page.getByRole('button', { name: 'Create strategy' });
  }

  // ---- detail ----
  detailHeading(name: string | RegExp): Locator {
    return this.page.getByRole('heading', { level: 1, name });
  }
  runCycleNow(): Locator {
    return this.page.getByRole('button', { name: 'Run cycle now' });
  }
  estimateModalTitle(): Locator {
    return this.page.getByRole('heading', { name: 'Confirm cycle dispatch' });
  }
  confirmRun(): Locator {
    return this.page.getByRole('button', { name: 'Confirm & run cycle' });
  }
  cancelEstimate(): Locator {
    return this.page.getByRole('button', { name: 'Cancel' });
  }
  enterStrategy(): Locator {
    return this.page.getByRole('button', { name: 'Enter strategy' });
  }
  enrollModalTitle(): Locator {
    return this.page.getByRole('heading', { name: /^Enter strategy — cycle/ });
  }
  enrollAll(): Locator {
    return this.page.getByRole('button', { name: 'Enter all' });
  }
  enrollSelected(): Locator {
    return this.page.getByRole('button', { name: /^Enter selected/ });
  }
  enrollSkip(): Locator {
    return this.page.getByRole('button', { name: 'Skip' });
  }
  approveCheckbox(ticker: string): Locator {
    return this.page.getByRole('checkbox', { name: `Approve ${ticker}` });
  }
  regimeWidget(): Locator {
    return this.page.locator('hf-regime-context-widget');
  }
  autoEnrollToggle(): Locator {
    return this.page.locator('[data-test="auto-enroll-toggle"]');
  }
  cycleButton(): Locator {
    // a cycle row "open" button in the cycles list
    return this.page.locator('[data-test="enter-strategy"]');
  }
  /** The transient dispatch/apply notice. Scoped to the live-region pill: the
   *  detail page also renders a static role="status" (expected-vs-realized),
   *  so a bare [role="status"] is ambiguous. */
  notice(): Locator {
    return this.page.locator('[role="status"][aria-live="polite"]');
  }
}
