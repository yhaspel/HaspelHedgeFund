import AxeBuilder from '@axe-core/playwright';
import { type Page, expect } from '@playwright/test';

/**
 * Run axe-core (4.11, lockstep with the Vitest a11y layer) in the real rendered
 * browser. Zero-violations gate, WCAG 2.0/2.1 A + AA. `exclude` carries the same
 * documented exceptions as the component-level baseline (§5.2).
 */
// No globally-disabled rules — the pre-existing DS debt (link-in-text-block,
// etc.) was remediated in the app, so the lane runs the full WCAG 2.0/2.1 A+AA
// ruleset. Per-call `exclude` still carries element-level false-positives.
const DISABLED_RULES: string[] = [];

export async function analyzeA11y(page: Page, exclude: string[] = []) {
  let builder = new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .disableRules(DISABLED_RULES);
  for (const sel of exclude) builder = builder.exclude(sel);
  return builder.analyze();
}

export async function expectNoA11yViolations(page: Page, exclude: string[] = []) {
  const results = await analyzeA11y(page, exclude);
  const summary = results.violations.map((v) => `${v.id} (${v.nodes.length})`);
  expect(summary, `axe violations: ${summary.join(', ')}`).toEqual([]);
}
