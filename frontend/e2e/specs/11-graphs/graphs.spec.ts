import { test, expect } from '../../fixtures';

/** WS-11 · Agent graph editor (`/graphs`, `/graphs/:id/edit`).
 *  Deep canvas interactions (drag/edge-draw) belong to the WS-18 component lane;
 *  this covers list, create, editor render + toolbar. */
test.describe('WS-11 · Agent graphs', () => {
  test('G-01 graphs list renders own graphs + templates', async ({ graphs }) => {
    await graphs.goto();
    await expect(graphs.heading()).toBeVisible();
    await expect(graphs.templateCard()).toBeVisible();
  });

  test('G-02 create graph opens the editor', async ({ page, graphs }) => {
    await graphs.goto();
    await graphs.newGraphButton().click();
    await graphs.newGraphName().fill('E2E Graph');
    await graphs.createSubmit().click();
    await expect(page).toHaveURL(/\/graphs\/\d+\/edit$/);
  });

  test('G-04 editor renders the canvas + validation pill', async ({ graphs }) => {
    await graphs.gotoEdit(7);
    await expect(graphs.canvas()).toBeVisible();
    await expect(graphs.validationPill()).toBeVisible();
  });

  test('G-10 editor exposes the save control', async ({ graphs }) => {
    await graphs.gotoEdit(7);
    await expect(graphs.saveButton()).toBeVisible();
    await expect(graphs.versionsButton()).toBeVisible();
  });
});
