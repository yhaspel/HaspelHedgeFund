import { type Page, type Locator } from '@playwright/test';

/** Page Object for /schedules — scheduled runs list + create/toggle/delete. */
export class SchedulesPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/schedules');
  }

  // ---- page chrome ----
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Scheduled runs' });
  }
  newScheduleButton(): Locator {
    return this.page.getByRole('button', { name: '+ New schedule' });
  }
  cancelButton(): Locator {
    return this.page.getByRole('button', { name: 'Cancel', exact: true });
  }
  alert(): Locator {
    return this.page.getByRole('alert');
  }

  // ---- list table ----
  schedulesHeading(): Locator {
    return this.page.getByRole('heading', { name: 'Your schedules' });
  }
  emptyState(): Locator {
    return this.page.getByText('No schedules yet.');
  }
  /** A schedule row matched by its name cell text. */
  row(name: string | RegExp): Locator {
    return this.page.getByRole('row').filter({ hasText: name });
  }
  /** The status pill ("Active" / "Paused") for a given schedule row. */
  statusPill(name: string | RegExp): Locator {
    return this.row(name).getByText(/^(Active|Paused)$/);
  }
  /** Pause / Resume toggle button on a row (aria-label is "Pause <name>" / "Resume <name>"). */
  toggleButton(name: string): Locator {
    return this.page.getByRole('button', { name: new RegExp(`(Pause|Resume) ${name}`) });
  }
  deleteButton(name: string | RegExp): Locator {
    return this.row(name).getByRole('button', { name: 'Delete', exact: true });
  }

  // ---- create form ----
  formHeading(): Locator {
    return this.page.getByRole('heading', { name: 'New scheduled run' });
  }
  nameInput(): Locator {
    return this.page.getByPlaceholder('Daily quality check');
  }
  /** Cadence: the "Frequency" select drives the resolved cron expression. */
  frequencySelect(): Locator {
    return this.page.getByLabel('Frequency');
  }
  graphSelect(): Locator {
    return this.page.locator(`[data-test="sched-graph-select"], [data-testid="sched-graph-select"]`);
  }
  /** The human-readable cron summary line ("At 9:25 AM, Monday through Friday …"). */
  cronSummary(): Locator {
    return this.page.locator('.cron-summary');
  }
  createButton(): Locator {
    return this.page.getByRole('button', { name: 'Create schedule' });
  }

  // ---- confirm dialog (ConfirmService → hf-modal) ----
  confirmDialog(name: string | RegExp): Locator {
    return this.page.getByRole('dialog', { name });
  }
  confirmDeleteButton(): Locator {
    return this.page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true });
  }
}
