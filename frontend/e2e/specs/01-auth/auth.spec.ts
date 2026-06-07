import { test, expect, UNAUTHENTICATED } from '../../fixtures';
import { LANE, LIVE_USER, STORAGE_STATE, TOKEN_KEYS } from '../../setup/env';

const accessToken = (page: import('@playwright/test').Page) =>
  page.evaluate((k) => localStorage.getItem(k), TOKEN_KEYS.access);

// A-01 runs in both lanes (it's @smoke). Mock-lane creds are arbitrary (login is
// intercepted); the live lane must use the seeded `e2e@local` user (seed_e2e).
const SMOKE_CREDS =
  LANE === 'live' ? LIVE_USER : { email: 'e2e@example.com', password: 'e2e-password-123' };

// ---------------------------------------------------------------------------
// WS-1 · Auth, routing & guards — these specs start UNAUTHENTICATED.
// ---------------------------------------------------------------------------
test.describe('WS-1 · Auth (unauthenticated start)', () => {
  test.use({ storageState: UNAUTHENTICATED });

  test('A-01 @smoke @mobile login success redirects to dashboard', async ({ page, login, shell }) => {
    await login.goto();
    await login.login(SMOKE_CREDS.email, SMOKE_CREDS.password);
    await expect(page).toHaveURL(/\/$/);
    await expect(shell.nav).toBeVisible();
    expect(await accessToken(page)).toBeTruthy();
  });

  test('A-02 login failure (bad creds) shows inline error, no token', async ({
    page,
    login,
    apiMock,
  }) => {
    apiMock.override('POST', '/auth/login/', { status: 401, json: { detail: 'invalid' } });
    await login.goto();
    await login.login('e2e@example.com', 'wrong');
    await expect(login.error()).toBeVisible();
    await expect(login.error()).toContainText(/invalid/i);
    await expect(page).toHaveURL(/\/login$/);
    expect(await accessToken(page)).toBeNull();
  });

  test('A-03 login validation contract (required + email format)', async ({ login }) => {
    // The login form declares its validation via native attributes (required +
    // type=email) for a11y/semantics; credential rejection itself is server-side
    // (see A-02). This asserts the declared contract via the browser's own
    // constraint validation, without submitting.
    await login.goto();
    const valid = (el: HTMLInputElement) => el.checkValidity();
    await expect(login.emailInput()).toHaveJSProperty('required', true);
    await expect(login.emailInput()).toHaveJSProperty('type', 'email');
    await expect(login.passwordInput()).toHaveJSProperty('required', true);
    expect(await login.emailInput().evaluate(valid)).toBe(false); // empty
    await login.emailInput().fill('not-an-email');
    expect(await login.emailInput().evaluate(valid)).toBe(false); // malformed
    await login.emailInput().fill('ok@example.com');
    expect(await login.emailInput().evaluate(valid)).toBe(true); // well-formed
  });

  test('A-04 signup success → authed + redirect', async ({ page, signup, shell }) => {
    await signup.goto();
    await signup.signup('fresh@example.com', 'longenough8');
    await expect(page).toHaveURL(/\/$/);
    await expect(shell.nav).toBeVisible();
  });

  test('A-05 signup password min-length is enforced', async ({ page, signup }) => {
    await signup.goto();
    await signup.emailInput().fill('fresh@example.com');
    await signup.passwordInput().fill('short');
    await signup.submitButton().click();
    await expect(page).toHaveURL(/\/signup$/);
    expect(await signup.passwordInput().evaluate((el: HTMLInputElement) => el.checkValidity())).toBe(
      false,
    );
  });

  test('A-06 login ⇄ signup cross-links', async ({ page, login, signup }) => {
    await login.goto();
    await login.signupLink().click();
    await expect(page).toHaveURL(/\/signup$/);
    await signup.loginLink().click();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('A-07 guard redirects unauthenticated deep-link to /login', async ({ page, login, shell }) => {
    // NOTE: the shipped authGuard redirects to /login WITHOUT a return-url
    // (core/auth/auth.guard.ts), so after login the user lands on the default
    // route (/), not the originally-requested page. This asserts real behaviour.
    await page.goto('/portfolio');
    await expect(page).toHaveURL(/\/login$/);
    await login.login('e2e@example.com', 'e2e-password-123');
    await expect(page).toHaveURL(/\/$/);
    await expect(shell.nav).toBeVisible();
  });

  test('A-08 logout clears session', async ({ page, login, shell }) => {
    await login.goto();
    await login.login('e2e@example.com', 'e2e-password-123');
    await expect(shell.nav).toBeVisible();
    await shell.logout();
    await expect(page).toHaveURL(/\/login$/);
    expect(await accessToken(page)).toBeNull();
    await page.goBack();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('A-11 public info routes need no auth', async ({ page }) => {
    await page.goto('/info');
    await expect(page).toHaveURL(/\/info$/);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// WS-1 · Auth seam — these specs start AUTHENTICATED.
// ---------------------------------------------------------------------------
test.describe('WS-1 · Auth seam (authenticated)', () => {
  test.use({ storageState: STORAGE_STATE });

  test('A-09 interceptor 401 → refresh → retry replays the request', async ({ page, shell }) => {
    let portfolioCalls = 0;
    let refreshed = false;
    await page.route('**/api/portfolio/', async (route) => {
      portfolioCalls += 1;
      if (portfolioCalls === 1) {
        return route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'token expired' }),
        });
      }
      return route.fallback();
    });
    await page.route('**/api/auth/refresh/', async (route) => {
      refreshed = true;
      return route.fallback();
    });

    await page.goto('/portfolio');
    // The silent refresh + replay can lag app boot under parallel load.
    await expect.poll(() => refreshed, { timeout: 15_000 }).toBe(true);
    await expect.poll(() => portfolioCalls, { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
    await expect(page).toHaveURL(/\/portfolio$/);
    await expect(shell.nav).toBeVisible();
  });

  test('A-10 refresh failure forces logout', async ({ page }) => {
    await page.route('**/api/portfolio/', (route) =>
      route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"expired"}' }),
    );
    await page.route('**/api/auth/refresh/', (route) =>
      route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"dead"}' }),
    );
    await page.goto('/portfolio');
    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  });

  test('A-12 unknown route redirects to dashboard (** wildcard)', async ({ page, shell }) => {
    await page.goto('/this-route-does-not-exist');
    await expect(page).toHaveURL(/\/$/);
    await expect(shell.nav).toBeVisible();
  });
});
