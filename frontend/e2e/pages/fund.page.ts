import { type Page, type Locator } from '@playwright/test';

/** Page Object for the Autonomous Fund (`/fund`) + per-strategy Autopilot
 *  panel (`/strategies/:id/autopilot`). Paper-only by construction. */
export class FundPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/fund');
  }
  async gotoAutopilot(strategyId: number): Promise<void> {
    await this.page.goto(`/strategies/${strategyId}/autopilot`);
  }

  // ---- fund dashboard ----
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Autonomous Fund' });
  }
  statePill(): Locator {
    return this.page.locator('.head-actions .pill').first();
  }
  haltButton(): Locator {
    return this.page.getByRole('button', { name: /Halt all/ });
  }
  clearHaltButton(): Locator {
    return this.page.getByRole('button', { name: 'Clear fund halt' });
  }
  // Clearing is an acknowledgment: the confirm dialog's action re-arms the
  // drawdown breakers from current equity (peaks rebase) before resuming.
  clearRearmConfirm(): Locator {
    return this.page.getByRole('button', { name: 'Clear & re-arm' });
  }
  accountNameLink(name: string | RegExp): Locator {
    return this.page.getByRole('link', { name });
  }
  disabledBadge(): Locator {
    return this.page.getByRole('button', { name: /^Disabled —/ }).first();
  }
  runNowButton(): Locator {
    return this.page.getByRole('button', { name: 'Run now' }).first();
  }
  queuedNotice(): Locator {
    return this.page.getByText('Cycle queued ✓').first();
  }
  correlationHeading(): Locator {
    return this.page.getByRole('heading', { name: /correlation/i });
  }
  notLiveBanner(): Locator {
    return this.page.locator('.banner-warn');
  }
  emptyState(): Locator {
    return this.page.getByText('No autonomous fund yet');
  }
  disclaimer(): Locator {
    return this.page.getByText('Paper trading only', { exact: false });
  }

  // ---- fund kill-switch confirm dialog (hf-confirm, requireText 'HALT') ----
  confirmDialog(): Locator {
    return this.page.getByRole('dialog');
  }
  confirmInput(): Locator {
    return this.confirmDialog().getByRole('textbox');
  }
  haltConfirm(): Locator {
    return this.page.getByRole('button', { name: 'Halt fund' });
  }

  // ---- autopilot panel ----
  autopilotHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Autopilot' });
  }
  validationGate(): Locator {
    return this.page.getByRole('heading', { name: 'Validation gate' });
  }
  // The panel renders responsive (desktop/mobile) duplicates of its controls;
  // target the visible one.
  enableButton(): Locator {
    return this.page
      .getByRole('button', { name: 'Enable autopilot' })
      .filter({ visible: true })
      .first();
  }
  disableButton(): Locator {
    return this.page.getByRole('button', { name: 'Disable' }).filter({ visible: true }).first();
  }
  resumeButton(): Locator {
    return this.page
      .getByRole('button', { name: /Resume/ })
      .filter({ visible: true })
      .first();
  }
  runValidationLink(): Locator {
    return this.page
      .getByRole('link', { name: /Run validation backtest/ })
      .filter({ visible: true })
      .first();
  }
  apStatePill(): Locator {
    return this.page.locator('.head-actions .pill').first();
  }
}
