import { type Page, type Locator } from '@playwright/test';

export class SignupPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/signup');
  }

  emailInput(): Locator {
    return this.page.getByLabel('Email');
  }
  passwordInput(): Locator {
    return this.page.getByLabel('Password');
  }
  submitButton(): Locator {
    return this.page.getByRole('button', { name: 'Sign up' });
  }
  error(): Locator {
    return this.page.getByRole('alert');
  }
  loginLink(): Locator {
    return this.page.getByRole('link', { name: 'Log in' });
  }

  async signup(email: string, password: string): Promise<void> {
    await this.emailInput().fill(email);
    await this.passwordInput().fill(password);
    await this.submitButton().click();
  }
}
