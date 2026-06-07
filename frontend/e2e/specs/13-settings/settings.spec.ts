import { test, expect } from '../../fixtures';

/** WS-13 · Settings (models / providers / personas / data-news / notifications). */
test.describe('WS-13 · Settings', () => {
  test('SE-01 tabs route the five settings sections; /settings redirects to models', async ({
    page,
    settings,
  }) => {
    // Bare /settings redirects to the Models tab.
    await settings.goto();
    await expect(page).toHaveURL(/\/settings\/models$/);
    await expect(settings.heading()).toHaveText('Models');
    await expect(settings.tablist()).toBeVisible();

    // Each tab is a routed role="tab" link; clicking navigates + flips the panel.
    await settings.tab('Providers').click();
    await expect(page).toHaveURL(/\/settings\/providers$/);
    await expect(settings.tabpanel('Providers settings')).toBeVisible();

    await settings.tab('Personas').click();
    await expect(page).toHaveURL(/\/settings\/personas$/);
    await expect(settings.tabpanel('Personas settings')).toBeVisible();

    await settings.tab('Data & News').click();
    await expect(page).toHaveURL(/\/settings\/data-news$/);
    await expect(settings.tabpanel('Data and News settings')).toBeVisible();

    await settings.tab('Notifications').click();
    await expect(page).toHaveURL(/\/settings\/notifications$/);
    await expect(settings.tabpanel('Notifications settings')).toBeVisible();
  });

  test('SE-02 models catalog renders + per-agent/default model selects', async ({ settings }) => {
    await settings.gotoModels();
    await expect(settings.tabpanel('Models settings')).toBeVisible();

    // Catalog table (sourced from GET /models/) shows a real model row.
    await expect(settings.modelsTable().first()).toBeVisible();
    await expect(settings.modelRow(/Claude Sonnet 4\.6/)).toBeVisible();

    // The global-default select + at least one per-agent select are present.
    await expect(settings.globalDefaultSelect()).toBeVisible();
    await expect(settings.agentSelect('buffett')).toBeVisible();
  });

  test('SE-03 fetch/refresh catalog posts to /models/fetch/', async ({ settings, apiMock }) => {
    await settings.gotoModels();

    // Assert the real POST endpoint fires (the status message text depends on
    // the response shape; the round-trip is the meaningful, stable signal).
    const post = settings.page.waitForRequest(
      (r) => r.method() === 'POST' && /\/models\/fetch\/$/.test(r.url()),
    );
    await settings.fetchOpenRouterButton().click();
    await post;
    await expect(settings.fetchOpenRouterButton()).toBeEnabled(); // fetch completed
  });

  test('SE-04 providers BYOK keys are masked + show a "set" indicator', async ({ settings }) => {
    await settings.gotoProviders();
    await expect(settings.tabpanel('Providers settings')).toBeVisible();

    // Keys are write-only secrets: password inputs with a paste-to-replace hint.
    const anthropic = settings.llmKeyInput('anthropic');
    await expect(anthropic).toHaveAttribute('type', 'password');
    await expect(anthropic).toHaveAttribute('placeholder', /paste to replace/);

    // The seeded provider-keys fixture has anthropic="set", openrouter="unset".
    await expect(settings.keyStatusFor('Anthropic API key')).toContainText('set');
    await expect(settings.keyStatusFor('OpenRouter API key')).toContainText('unset');
  });

  test('SE-05 clear/replace a key saves + clears the input', async ({ settings }) => {
    await settings.gotoProviders();

    const openai = settings.llmKeyInput('openai');
    await openai.fill('sk-e2e-replacement-key');
    await expect(openai).toHaveValue('sk-e2e-replacement-key');

    // Save PUTs only the edited fields; on success the input is wiped so the
    // secret never lingers in the DOM, and a confirmation renders.
    const put = settings.page.waitForRequest(
      (r) => r.method() === 'PUT' && /\/me\/provider-keys\/$/.test(r.url()),
    );
    await settings.saveKeysButton().click();
    await put;
    await expect(settings.keysMsg()).toBeVisible();
    await expect(openai).toHaveValue('');
  });

  test('SE-06 personas evolution toggle persists via save', async ({ settings, apiMock }) => {
    await settings.gotoPersonas();
    await expect(settings.tabpanel('Personas settings')).toBeVisible();
    await expect(settings.personaTable()).toBeVisible();

    // Seeded settings have evolution enabled → flip it off to make the form dirty.
    const enabled = settings.evoEnabledToggle();
    await expect(enabled).toBeChecked();
    await enabled.uncheck();
    await expect(settings.saveEvoButton()).toBeEnabled();

    // PATCH /persona-evolution/settings/ echoes the body back as enabled:false.
    apiMock.override('PATCH', '/persona-evolution/settings/', {
      json: {
        enabled: false,
        cadence: 'weekly',
        model_id: '',
        web_search_enabled: true,
        monthly_cost_cap_usd: '2.00',
        cost_cap_reached_at: null,
        updated_at: '2026-06-06T00:00:00Z',
        month_to_date_cost_usd: '0.00',
      },
    });
    await settings.saveEvoButton().click();
    await expect(settings.evoMsg()).toHaveText('Saved.');
    await expect(enabled).not.toBeChecked();
  });

  test('SE-07 data-news toggle persists via save', async ({ settings }) => {
    await settings.gotoDataNews();
    await expect(settings.tabpanel('Data and News settings')).toBeVisible();

    // Seeded news prefs have sentiment enabled → toggle it to dirty the form.
    const sentiment = settings.newsSentimentToggle();
    await expect(sentiment).toBeChecked();
    await sentiment.uncheck();
    await expect(settings.saveNewsButton()).toBeEnabled();

    // PUT /news/preferences/ merges the patch into preferences + echoes back.
    const put = settings.page.waitForRequest(
      (r) => r.method() === 'PUT' && /\/news\/preferences\/$/.test(r.url()),
    );
    await settings.saveNewsButton().click();
    await put;
    await expect(settings.newsMsg()).toHaveText('Saved.');
  });

  test('SE-08 notifications — add email + telegram channels', async ({ settings }) => {
    await settings.gotoNotifications();
    await expect(settings.tabpanel('Notifications settings')).toBeVisible();

    // Default type is email; add with an explicit address.
    await settings.channelLabelInput().fill('Desk');
    await settings.emailAddressInput().fill('desk@example.com');
    let post = settings.page.waitForRequest(
      (r) => r.method() === 'POST' && /\/notification-channels\/$/.test(r.url()),
    );
    await settings.addChannelButton().click();
    await expect((await post).postDataJSON()).toMatchObject({ kind: 'email' });

    // Switch to Telegram — the bot-token + chat-id fields appear, then submit.
    await settings.channelTypeSelect().selectOption('telegram');
    await settings.botTokenInput().fill('123456:ABC-token');
    await settings.chatIdInput().fill('987654321');
    post = settings.page.waitForRequest(
      (r) => r.method() === 'POST' && /\/notification-channels\/$/.test(r.url()),
    );
    await settings.addChannelButton().click();
    await expect((await post).postDataJSON()).toMatchObject({ kind: 'telegram' });

    // The existing seeded channels remain listed.
    await expect(settings.channelsTable()).toBeVisible();
    await expect(settings.page.getByText('EmailHHF')).toBeVisible();
  });

  test('SE-09 server error on add surfaces an inline alert affordance', async ({
    settings,
    apiMock,
  }) => {
    await settings.gotoNotifications();

    // A failed add (invalid token / server reject) → role="alert" inline error.
    apiMock.override('POST', '/notification-channels/', {
      status: 400,
      json: { detail: 'Invalid bot token' },
    });
    await settings.channelTypeSelect().selectOption('telegram');
    await settings.botTokenInput().fill('not-a-real-token');
    await settings.chatIdInput().fill('1');
    await settings.addChannelButton().click();
    await expect(settings.errorAlert()).toContainText('Invalid bot token');

    // Provider-keys save 500 also degrades to a visible message, not a crash.
    await settings.gotoProviders();
    apiMock.override('PUT', '/me/provider-keys/', { status: 500 });
    await settings.llmKeyInput('openai').fill('sk-bad');
    await settings.saveKeysButton().click();
    await expect(settings.keysMsg()).toHaveText('Failed to save');
  });
});
