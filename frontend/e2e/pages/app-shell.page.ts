import { type Page, type Locator } from '@playwright/test';

/**
 * The authed chrome rendered on every protected route by `hf-app-shell`:
 * the Primary sidebar, skip-link, ⌘K trigger, theme toggle, profile link and
 * Log out. Selectors are role/name-first (they double as a11y assertions).
 */
export class AppShellPage {
  readonly nav: Locator;
  readonly main: Locator;

  constructor(public readonly page: Page) {
    this.nav = page.getByRole('navigation', { name: 'Primary' });
    this.main = page.locator('main');
  }

  /** A sidebar destination by its accessible name (aria-label), e.g. "Runs". */
  navLink(name: string): Locator {
    return this.nav.getByRole('link', { name, exact: true });
  }

  async navigateVia(name: string): Promise<void> {
    await this.navLink(name).click();
  }

  skipLink(): Locator {
    return this.page.getByRole('link', { name: 'Skip to main content' });
  }

  themeToggle(): Locator {
    return this.page.getByRole('button', { name: /Switch to (light|dark) theme/ });
  }

  paletteTrigger(): Locator {
    return this.page.getByRole('button', { name: /Open command palette/ });
  }

  profileEmailLink(): Locator {
    return this.page.getByRole('link', { name: 'Your profile' });
  }

  logoutButton(): Locator {
    return this.page.getByRole('button', { name: 'Log out' });
  }

  async openPaletteByShortcut(): Promise<void> {
    // The shell binds both Ctrl+K and ⌘K (window:keydown); we use Ctrl+K because
    // headless Chromium on macOS swallows Meta+K as a browser shortcut. Focus a
    // neutral in-page element first so the keydown has a target that bubbles to
    // window (after goto, nothing is focused).
    await this.page.locator('#main-content').focus();
    await this.page.keyboard.press('Control+KeyK');
  }

  async toggleTheme(): Promise<void> {
    await this.themeToggle().click();
  }

  async logout(): Promise<void> {
    await this.logoutButton().click();
  }

  /** The applied theme, read from `<html data-theme>`. */
  theme(): Promise<string | null> {
    return this.page.evaluate(() => document.documentElement.dataset['theme'] ?? null);
  }

  storedTheme(): Promise<string | null> {
    return this.page.evaluate(() => localStorage.getItem('hf.theme'));
  }
}
