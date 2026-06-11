import { type Page, type Locator } from '@playwright/test';

/**
 * Page Object for /settings/* — P10 §D5: TWO top-level groups
 * (General · Models & advanced) with the original five pages as sections
 * (General: Data & News, Notifications, Your profile; Advanced: Models,
 * Providers, Personas).
 *
 * Every page wraps its body in a matching `role="tabpanel"`; the group row is
 * a real ARIA `tablist` of routed `role="tab"` links and the active group's
 * sections render as a secondary nav.
 */
export class SettingsPage {
  constructor(public readonly page: Page) {}

  // ---- navigation ---------------------------------------------------------
  async goto(): Promise<void> {
    await this.page.goto('/settings');
  }
  async gotoModels(): Promise<void> {
    await this.page.goto('/settings/models');
  }
  async gotoProviders(): Promise<void> {
    await this.page.goto('/settings/providers');
  }
  async gotoPersonas(): Promise<void> {
    await this.page.goto('/settings/personas');
  }
  async gotoDataNews(): Promise<void> {
    await this.page.goto('/settings/data-news');
  }
  async gotoNotifications(): Promise<void> {
    await this.page.goto('/settings/notifications');
  }

  // ---- shared sub-nav (tablist) ------------------------------------------
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1 });
  }
  tablist(): Locator {
    return this.page.getByRole('tablist', { name: 'Settings sections' });
  }
  tab(name: string | RegExp): Locator {
    return this.page.getByRole('tab', { name });
  }
  tabpanel(name: string | RegExp): Locator {
    return this.page.getByRole('tabpanel', { name });
  }
  /** A section link in the active group's secondary row (P10 §D5). */
  section(name: string | RegExp): Locator {
    return this.page
      .getByRole('navigation', { name: 'Sections' })
      .getByRole('link', { name });
  }

  // ---- Models -------------------------------------------------------------
  modelsTable(): Locator {
    // The "Available models" catalog table.
    return this.page.getByRole('table');
  }
  modelRow(name: string | RegExp): Locator {
    // `getByRole('row', { name })` matches on the row's *computed accessible
    // name* (concatenated cell text), which WebKit/Firefox build differently
    // from Chromium — so the name match is engine-specific. Filter by text
    // content instead, which is identical across engines.
    return this.modelsTable().getByRole('row').filter({ hasText: name });
  }
  globalDefaultSelect(): Locator {
    return this.page.locator(`[data-test="global-default-select"], [data-testid="global-default-select"]`);
  }
  agentSelect(agentId: string): Locator {
    return this.page.getByRole('combobox', { name: `Model for ${agentId}` });
  }
  fetchOpenRouterButton(): Locator {
    return this.page.getByRole('button', { name: /Fetch latest OpenRouter models/ });
  }
  fetchMsg(): Locator {
    return this.page.locator(`[data-test="fetch-msg"], [data-testid="fetch-msg"]`);
  }
  savePrefsButton(): Locator {
    return this.page.locator(`[data-test="save-prefs"], [data-testid="save-prefs"]`);
  }

  // ---- Providers ----------------------------------------------------------
  llmKeyInput(provider: string): Locator {
    return this.page.locator(`[data-test="llm-key-${provider}"], [data-testid="llm-key-${provider}"]`);
  }
  dataKeyInput(provider: string): Locator {
    return this.page.locator(`[data-test="data-key-${provider}"], [data-testid="data-key-${provider}"]`);
  }
  /** The "set" / "unset" status pill that sits in a provider's <label>. */
  keyStatusFor(label: string | RegExp): Locator {
    // The pill text lives in the same <label> as the field name. We scope by
    // the label and read its trailing status word.
    return this.page.locator('label', { hasText: label }).first();
  }
  saveKeysButton(): Locator {
    return this.page.getByRole('button', { name: /Save provider keys/ });
  }
  keysMsg(): Locator {
    return this.page.locator(`[data-test="keys-msg"], [data-testid="keys-msg"]`);
  }

  // ---- Personas (evolution) ----------------------------------------------
  evoEnabledToggle(): Locator {
    return this.page.locator(`[data-test="evo-enabled"], [data-testid="evo-enabled"]`);
  }
  evoWebToggle(): Locator {
    return this.page.locator(`[data-test="evo-web"], [data-testid="evo-web"]`);
  }
  evoCadenceSelect(): Locator {
    return this.page.locator(`[data-test="evo-cadence"], [data-testid="evo-cadence"]`);
  }
  saveEvoButton(): Locator {
    return this.page.locator(`[data-test="save-evo"], [data-testid="save-evo"]`);
  }
  evoMsg(): Locator {
    return this.page.locator(`[data-test="evo-msg"], [data-testid="evo-msg"]`);
  }
  personaTable(): Locator {
    return this.page.locator(`[data-test="evo-persona-table"], [data-testid="evo-persona-table"]`);
  }

  // ---- Data & News --------------------------------------------------------
  newsSentimentToggle(): Locator {
    return this.page.locator(`[data-test="news-sentiment-toggle"], [data-testid="news-sentiment-toggle"]`);
  }
  newsChyronToggle(): Locator {
    return this.page.locator(`[data-test="news-chyron-toggle"], [data-testid="news-chyron-toggle"]`);
  }
  saveNewsButton(): Locator {
    return this.page.locator(`[data-test="save-news-prefs"], [data-testid="save-news-prefs"]`);
  }
  newsMsg(): Locator {
    return this.page.locator(`[data-test="news-prefs-msg"], [data-testid="news-prefs-msg"]`);
  }
  cadenceRadio(value: string): Locator {
    return this.page.locator(`[data-test="cadence-${value}"], [data-testid="cadence-${value}"]`);
  }
  savePortfolioButton(): Locator {
    return this.page.locator(`[data-test="save-portfolio-prefs"], [data-testid="save-portfolio-prefs"]`);
  }

  // ---- Notifications ------------------------------------------------------
  channelsTable(): Locator {
    return this.page.getByRole('table');
  }
  channelTypeSelect(): Locator {
    return this.page.getByLabel('Type');
  }
  channelLabelInput(): Locator {
    return this.page.getByLabel('Label (optional)');
  }
  emailAddressInput(): Locator {
    return this.page.getByLabel('Email address (blank = account email)');
  }
  botTokenInput(): Locator {
    return this.page.getByLabel('Bot token');
  }
  chatIdInput(): Locator {
    return this.page.getByLabel('Chat id');
  }
  addChannelButton(): Locator {
    return this.page.getByRole('button', { name: 'Add channel' });
  }
  testChannelButton(): Locator {
    return this.page.getByRole('button', { name: 'Send test' }).first();
  }
  noteMsg(): Locator {
    return this.page.getByRole('status');
  }
  errorAlert(): Locator {
    return this.page.getByRole('alert');
  }
}
