import { type Page, type Locator } from '@playwright/test';

/** Generic hf-modal dialog wrapper. */
export class Modal {
  constructor(public readonly page: Page) {}

  dialog(): Locator {
    return this.page.getByRole('dialog');
  }

  byName(name: string | RegExp): Locator {
    return this.page.getByRole('dialog', { name });
  }
}
