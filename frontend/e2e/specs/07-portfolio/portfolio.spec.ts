import { test, expect } from '../../fixtures';
import portfolio from '../../fixtures/data/portfolio.json';

/** WS-7 · Portfolios & Manual Book (incl. the PF-06 whole-share guardrail). */
test.describe('WS-7 · Portfolios & Manual Book', () => {
  test('PF-01 hub lists books + card → Manual book', async ({ page, portfolio: pf }) => {
    await pf.gotoHub();
    await expect(pf.hubHeading()).toBeVisible();
    await pf.manualBookLink().first().click();
    await expect(page).toHaveURL(/\/portfolio$/);
  });

  test('PF-02 @mobile positions table renders rows', async ({ portfolio: pf }) => {
    await pf.goto();
    await expect(pf.heading()).toBeVisible();
    await expect(pf.positionsTable()).toBeVisible();
    await expect(pf.positionRow('AMAT')).toBeVisible();
  });

  test('PF-04 enter-position modal opens with labelled fields', async ({ portfolio: pf }) => {
    await pf.goto();
    await pf.addPositionButton().click();
    await expect(pf.enterModal()).toBeVisible();
    await expect(pf.page.getByRole('heading', { name: /Enter position/ })).toBeVisible();
    await expect(pf.tickerInput()).toBeVisible();
    await expect(pf.sizeInput()).toBeVisible();
  });

  test('PF-05 validation — confirm gated on ticker/qty/price', async ({ portfolio: pf }) => {
    await pf.goto();
    await pf.addPositionButton().click();
    await expect(pf.confirmPosition()).toBeDisabled(); // empty
    await pf.tickerInput().fill('AAPL');
    await pf.priceInput().fill('100');
    await pf.sizeInput().fill('0'); // non-positive
    await expect(pf.confirmPosition()).toBeDisabled();
    await pf.sizeInput().fill('5');
    await expect(pf.confirmPosition()).toBeEnabled();
  });

  test('PF-06 whole-share enforcement — fractional rejected in whole mode', async ({ portfolio: pf }) => {
    await pf.goto();
    await pf.addPositionButton().click();
    // Default mode is "Whole shares": the Size input constrains to integers (step=1).
    await expect(pf.quantityModeButton('Whole shares')).toHaveClass(/active/);
    await expect(pf.sizeInput()).toHaveAttribute('step', '1');
    await pf.sizeInput().fill('2.5');
    expect(
      await pf.sizeInput().evaluate((el: HTMLInputElement) => el.validity.stepMismatch),
    ).toBe(true);
    // Fractional mode lifts the whole-share constraint.
    await pf.quantityModeButton('Fractional').click();
    await expect(pf.sizeInput()).toHaveAttribute('step', '0.000001');
    expect(
      await pf.sizeInput().evaluate((el: HTMLInputElement) => el.validity.stepMismatch),
    ).toBe(false);
  });

  test('PF-07 submit a valid position closes the modal', async ({ portfolio: pf }) => {
    await pf.goto();
    await pf.addPositionButton().click();
    await pf.tickerInput().fill('AAPL');
    await pf.sizeInput().fill('5');
    await pf.priceInput().fill('100');
    await pf.confirmPosition().click();
    await expect(pf.enterModal()).toBeHidden();
  });

  test('PF-08 cancel/Esc closes without mutating', async ({ page, portfolio: pf }) => {
    await pf.goto();
    await pf.addPositionButton().click();
    await expect(pf.enterModal()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(pf.enterModal()).toBeHidden();
  });

  test('PF-09 cash-adjust modal opens with deposit/amount', async ({ portfolio: pf }) => {
    await pf.goto();
    await pf.cashButton().click();
    await expect(pf.cashModalTitle()).toBeVisible();
    await expect(pf.depositRadio()).toBeVisible();
    await pf.amountInput().fill('1000');
    await expect(pf.amountInput()).toHaveValue('1000');
  });

  test('PF-11 empty book state', async ({ portfolio: pf, apiMock }) => {
    apiMock.override('GET', '/portfolio/', { json: { ...portfolio, positions: [] } });
    await pf.goto();
    await expect(pf.emptyPositions()).toBeVisible();
  });
});
