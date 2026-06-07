import { type Page, type Locator } from '@playwright/test';

/** Page Object for the Agent graph editor (`/graphs`, `/graphs/:id/edit`). */
export class GraphsPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/graphs');
  }
  async gotoEdit(id: number): Promise<void> {
    await this.page.goto(`/graphs/${id}/edit`);
  }

  // ---- list ----
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Agent graphs' });
  }
  newGraphButton(): Locator {
    return this.page.getByTestId('new-graph-btn');
  }
  newGraphName(): Locator {
    return this.page.getByTestId('new-graph-name');
  }
  createSubmit(): Locator {
    return this.page.getByRole('button', { name: 'Create', exact: true });
  }
  templateCard(): Locator {
    return this.page.locator('[data-testid^="template-"]').first();
  }
  noOwn(): Locator {
    return this.page.getByTestId('no-own');
  }

  // ---- editor ----
  canvas(): Locator {
    return this.page.locator('hf-graph-canvas');
  }
  validationPill(): Locator {
    return this.page.getByTestId('validation-pill');
  }
  saveButton(): Locator {
    return this.page.getByTestId('save-btn');
  }
  versionsButton(): Locator {
    return this.page.getByTestId('versions-btn');
  }
}
