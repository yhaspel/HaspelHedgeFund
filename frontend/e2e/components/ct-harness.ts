import { type Page, type Locator } from '@playwright/test';

/** Drives the WS-18 component-in-harness route `/__ct/:component`. */
export class CtHarness {
  constructor(public readonly page: Page) {}

  async mount(component: string): Promise<void> {
    await this.page.goto(`/__ct/${component}`);
  }
  name(): Locator {
    return this.page.getByTestId('ct-name');
  }
  open(): Locator {
    return this.page.getByTestId('ct-open');
  }
  dialog(): Locator {
    return this.page.getByRole('dialog');
  }
}
