import { test, expect, UNAUTHENTICATED } from '../../fixtures';

/** WS-17 · Info & guides (`/info`, `/info/:slug`) — public, static content.
 *  IN-03 (glossary-term popover) is covered in the WS-18 component lane, since
 *  hf-term renders in component templates, not inside markdown guides. */
test.describe('WS-17 · Info & guides', () => {
  test('IN-01 info list renders guide links', async ({ info }) => {
    await info.goto();
    await expect(info.listHeading()).toBeVisible();
    await expect(info.guideLink('What This Application Is')).toBeVisible();
  });

  test('IN-02 info detail renders the guide via guide-shell', async ({ info }) => {
    await info.gotoGuide('welcome');
    await expect(info.detailHeading('What This Application Is')).toBeVisible();
  });

  test('IN-04 info routes render unauthenticated', async ({ page, info }) => {
    await info.goto();
    await expect(page).toHaveURL(/\/info$/);
    await expect(info.listHeading()).toBeVisible();
    await info.gotoGuide('byok');
    await expect(page).toHaveURL(/\/info\/byok$/);
    await expect(info.detailHeading(/BYOK/)).toBeVisible();
  });
  test.describe('unauthenticated', () => {
    test.use({ storageState: UNAUTHENTICATED });
    test('IN-04b info is reachable with no session', async ({ page, info }) => {
      await info.gotoGuide('welcome');
      await expect(page).toHaveURL(/\/info\/welcome$/);
      await expect(info.detailHeading('What This Application Is')).toBeVisible();
    });
  });

  test('IN-05 in-guide cross-links navigate between guides', async ({ page, info }) => {
    await info.gotoGuide('welcome');
    // welcome → next guide (how-to) via the footer nav link.
    await info.guideLink('How To: Runs, Backtests & Strategies').click();
    await expect(page).toHaveURL(/\/info\/how-to$/);
    await expect(info.detailHeading('How To: Runs, Backtests & Strategies')).toBeVisible();
  });
});
