import { type Page, type Locator } from '@playwright/test';

/**
 * Page Object for the Broker workstream (WS-12). Covers:
 *  - accounts list            /broker-accounts
 *  - connect wizard           /broker-accounts/connect (+ ?resume=<id>)
 *  - account overview         /broker-accounts/:id
 *  - pending orders           /broker-accounts/pending
 *  - the connect-flow tiles   (alpaca / ibkr / tradestation, data-testid'd)
 *  - the order-ticket modal   (hosted on the run-detail page) + order-confirm
 *
 * The disclaimer modal (`hf-disclaimer-modal`) is a standalone component that
 * is not mounted on any broker route — it gates the LIVE-mode order flow only.
 * The LIVE gate that a user actually sees is the order-confirm modal's "type a
 * phrase containing LIVE" requirement, which is what BK-03 exercises instead.
 */
export class BrokerPage {
  constructor(public readonly page: Page) {}

  // ---- navigation ----------------------------------------------------------
  async goto(): Promise<void> {
    await this.page.goto('/broker-accounts');
  }
  async gotoConnect(): Promise<void> {
    await this.page.goto('/broker-accounts/connect');
  }
  async gotoConnectResume(id: number): Promise<void> {
    await this.page.goto(`/broker-accounts/connect?resume=${id}`);
  }
  async gotoOverview(id: number): Promise<void> {
    await this.page.goto(`/broker-accounts/${id}`);
  }
  async gotoPending(): Promise<void> {
    await this.page.goto('/broker-accounts/pending');
  }
  /** The order-ticket modal lives on the run-detail page (Submit-as-broker). */
  async gotoRunDetail(id: number): Promise<void> {
    await this.page.goto(`/runs/${id}`);
  }

  // ---- accounts list -------------------------------------------------------
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Broker accounts' });
  }
  connectCta(): Locator {
    return this.page.getByRole('link', { name: 'Connect an account' });
  }
  accountsTable(): Locator {
    return this.page.locator(`[data-test="accounts-table"], [data-testid="accounts-table"]`);
  }
  accountRow(id: number): Locator {
    return this.page.locator(`[data-test="account-row-${id}"], [data-testid="account-row-${id}"]`);
  }
  /** A status badge cell — the connection_status text (e.g. "active"). */
  statusBadge(status: string): Locator {
    return this.page.getByText(status, { exact: true });
  }
  openAccountLink(id: number): Locator {
    return this.page.locator(`[data-test="open-account-${id}"], [data-testid="open-account-${id}"]`);
  }
  resumeAccountLink(id: number): Locator {
    return this.page.locator(`[data-test="resume-account-${id}"], [data-testid="resume-account-${id}"]`);
  }
  disconnectButton(id: number): Locator {
    return this.page.locator(`[data-test="disconnect-${id}"], [data-testid="disconnect-${id}"]`);
  }
  /** ConfirmService.ask() dialog, titled with the account label. */
  confirmDialog(name: string | RegExp): Locator {
    return this.page.getByRole('dialog', { name });
  }
  /** The danger accept button inside the confirm dialog (scoped to it). */
  confirmDialogAccept(label: string): Locator {
    return this.page.getByRole('dialog').getByRole('button', { name: label, exact: true });
  }
  emptyList(): Locator {
    return this.page.getByText('No broker accounts yet');
  }
  listError(): Locator {
    return this.page.getByRole('alert');
  }

  // ---- connect wizard ------------------------------------------------------
  connectHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Connect an account' });
  }
  brokerGrid(): Locator {
    return this.page.locator(`[data-test="broker-grid"], [data-testid="broker-grid"]`);
  }
  brokerTile(code: string): Locator {
    return this.page.locator(`[data-test="broker-tile-${code}"], [data-testid="broker-tile-${code}"]`);
  }
  /** "Continue" CTA on an available, non-deferred broker tile (mock/alpaca). */
  selectBrokerButton(code: string): Locator {
    return this.page.locator(`[data-test="select-broker-${code}"], [data-testid="select-broker-${code}"]`);
  }
  /** The disabled affordance on a gated broker (ibkr/tradestation). WAVE 3
   *  replaced the fixed "Available in a later release" label with the gate's
   *  own note, so it is located by its data-test hook rather than by name. */
  laterReleaseButton(): Locator {
    return this.page.locator('[data-test^="select-broker-disabled-"]');
  }
  connectForm(): Locator {
    return this.page.locator(`[data-test="connect-form"], [data-testid="connect-form"]`);
  }
  labelInput(): Locator {
    return this.page.locator(`[data-test="label-input"], [data-testid="label-input"]`);
  }
  createAccountButton(): Locator {
    return this.page.locator(`[data-test="create-account-btn"], [data-testid="create-account-btn"]`);
  }
  cancelButton(): Locator {
    return this.page.locator(`[data-test="cancel-btn"], [data-testid="cancel-btn"]`);
  }
  wizardError(): Locator {
    return this.page.getByRole('alert');
  }

  /** Pick a broker tile and (for the inline Demo/label form) name the account. */
  async selectBroker(code: string): Promise<void> {
    await this.selectBrokerButton(code).click();
  }

  // ---- per-broker connect flows (data-testid roots) ------------------------
  alpacaFlow(): Locator {
    return this.page.locator(`[data-test="alpaca-connect-flow"], [data-testid="alpaca-connect-flow"]`);
  }
  alpacaApiKey(): Locator {
    return this.page.locator(`[data-test="alpaca-api-key"], [data-testid="alpaca-api-key"]`);
  }
  alpacaApiSecret(): Locator {
    return this.page.locator(`[data-test="alpaca-api-secret"], [data-testid="alpaca-api-secret"]`);
  }
  alpacaSubmit(): Locator {
    return this.page.locator(`[data-test="alpaca-submit"], [data-testid="alpaca-submit"]`);
  }

  ibkrFlow(): Locator {
    return this.page.locator(`[data-test="ibkr-connect-flow"], [data-testid="ibkr-connect-flow"]`);
  }
  ibkrStep1Continue(): Locator {
    return this.page.locator(`[data-test="ibkr-step1-continue"], [data-testid="ibkr-step1-continue"]`);
  }
  ibkrStep1Retry(): Locator {
    return this.page.locator(`[data-test="ibkr-step1-retry"], [data-testid="ibkr-step1-retry"]`);
  }
  ibkrStep2Continue(): Locator {
    return this.page.locator(`[data-test="ibkr-step2-continue"], [data-testid="ibkr-step2-continue"]`);
  }
  ibkrStep3Activate(): Locator {
    return this.page.locator(`[data-test="ibkr-step3-activate"], [data-testid="ibkr-step3-activate"]`);
  }
  ibkrPick(accountId: string): Locator {
    return this.page.locator(`[data-test="ibkr-pick-${accountId}"], [data-testid="ibkr-pick-${accountId}"]`);
  }

  tradestationFlow(): Locator {
    return this.page.locator(`[data-test="tradestation-connect-flow"], [data-testid="tradestation-connect-flow"]`);
  }
  tsStep1Continue(): Locator {
    return this.page.locator(`[data-test="ts-step1-continue"], [data-testid="ts-step1-continue"]`);
  }
  tsSetupGuideLink(): Locator {
    return this.page.locator(`[data-test="ts-setup-guide-link"], [data-testid="ts-setup-guide-link"]`);
  }
  /** OAuth-start affordance: the "Get sign-in link" button on step 2. */
  tsStep2Start(): Locator {
    return this.page.locator(`[data-test="ts-step2-start"], [data-testid="ts-step2-start"]`);
  }
  tsStep2Authorize(): Locator {
    return this.page.locator(`[data-test="ts-step2-authorize"], [data-testid="ts-step2-authorize"]`);
  }

  // ---- account overview ----------------------------------------------------
  overviewHeading(name: string | RegExp): Locator {
    return this.page.getByRole('heading', { level: 1, name });
  }
  newOrderButton(): Locator {
    return this.page.locator(`[data-test="new-order-btn"], [data-testid="new-order-btn"]`);
  }
  positionsTable(): Locator {
    return this.page.locator(`[data-test="positions-table"], [data-testid="positions-table"]`);
  }
  /** KPI tile by its eyebrow label (Cash / Equity / Positions / Working orders). */
  kpi(label: string): Locator {
    // KPI labels are `.k` cells — scope so "Positions" doesn't also match the
    // positions-table <h2>.
    return this.page.locator('.k', { hasText: label }).first();
  }
  positionsEmpty(): Locator {
    return this.page.getByText('No positions yet. Place an order to begin.');
  }

  // ---- new-order modal (overview) -----------------------------------------
  orderDialog(): Locator {
    return this.page.getByRole('dialog', { name: 'New order' });
  }
  orderTicker(): Locator {
    return this.page.locator(`[data-test="new-order-ticker"], [data-testid="new-order-ticker"]`);
  }
  orderSideBuy(): Locator {
    return this.page.locator(`[data-test="new-order-side-buy"], [data-testid="new-order-side-buy"]`);
  }
  orderSideSell(): Locator {
    return this.page.locator(`[data-test="new-order-side-sell"], [data-testid="new-order-side-sell"]`);
  }
  orderTypeMarket(): Locator {
    return this.page.locator(`[data-test="new-order-type-market"], [data-testid="new-order-type-market"]`);
  }
  orderQty(): Locator {
    return this.page.locator(`[data-test="new-order-qty"], [data-testid="new-order-qty"]`);
  }

  // ---- pending orders ------------------------------------------------------
  pendingHeading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'Pending orders' });
  }
  pendingTable(): Locator {
    return this.page.locator(`[data-test="pending-table"], [data-testid="pending-table"]`);
  }
  pendingRow(id: number): Locator {
    return this.page.locator(`[data-test="pending-row-${id}"], [data-testid="pending-row-${id}"]`);
  }
  pendingConfirm(id: number): Locator {
    return this.page.locator(`[data-test="confirm-${id}"], [data-testid="confirm-${id}"]`);
  }
  pendingEmpty(): Locator {
    return this.page.getByText('No pending orders');
  }

  // ---- order-confirm modal (pending + run-detail) -------------------------
  confirmModalHeading(): Locator {
    return this.page.getByRole('heading', { name: 'Confirm and submit order' });
  }
  confirmSubmit(): Locator {
    return this.page.locator(`[data-test="confirm-submit"], [data-testid="confirm-submit"]`);
  }
  confirmCancel(): Locator {
    return this.page.locator(`[data-test="confirm-cancel"], [data-testid="confirm-cancel"]`);
  }
  typedConfirmInput(): Locator {
    return this.page.locator(`[data-test="typed-confirmation-input"], [data-testid="typed-confirmation-input"]`);
  }

  // ---- order-ticket modal (run-detail "Submit as broker order") -----------
  submitBrokerOrderButton(ticker: string): Locator {
    return this.page.locator(`[data-test="submit-broker-order-${ticker}"], [data-testid="submit-broker-order-${ticker}"]`);
  }
  ticketModalHeading(): Locator {
    return this.page.getByRole('heading', { name: 'Submit as broker order' });
  }
  ticketAccountSelect(): Locator {
    return this.page.locator(`[data-test="ticket-account"], [data-testid="ticket-account"]`);
  }
  ticketQuantity(): Locator {
    return this.page.locator(`[data-test="ticket-quantity"], [data-testid="ticket-quantity"]`);
  }
  ticketOrderType(): Locator {
    return this.page.locator(`[data-test="ticket-order-type"], [data-testid="ticket-order-type"]`);
  }
  ticketReview(): Locator {
    return this.page.locator(`[data-test="ticket-review"], [data-testid="ticket-review"]`);
  }
}
