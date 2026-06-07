import { type Page, type Locator } from '@playwright/test';

export class LoginPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/login');
  }

  emailInput(): Locator {
    return this.page.getByLabel('Email');
  }
  passwordInput(): Locator {
    return this.page.getByLabel('Password');
  }
  submitButton(): Locator {
    return this.page.getByRole('button', { name: 'Log in' });
  }
  error(): Locator {
    return this.page.getByRole('alert');
  }
  signupLink(): Locator {
    return this.page.getByRole('link', { name: 'Sign up' });
  }

  async fill(email: string, password: string): Promise<void> {
    await this.emailInput().fill(email);
    await this.passwordInput().fill(password);
  }

  async login(email: string, password: string): Promise<void> {
    await this.fill(email, password);
    await this.submitButton().click();
  }
}
