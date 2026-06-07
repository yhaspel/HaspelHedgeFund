import type { Page } from '@playwright/test';
import { FROZEN_TIME } from '../setup/env';

/**
 * Freeze wall-clock time without pausing timers. `setFixedTime` (not `install`)
 * keeps the zoneless Angular app's `setInterval`/rAF running — we only need
 * `Date.now()`/`new Date()` pinned so relative times and "today" defaults are
 * deterministic for assertions and screenshots (§2.6).
 */
export async function freezeClock(page: Page): Promise<void> {
  await page.clock.setFixedTime(FROZEN_TIME);
}

/** Injected stylesheet that kills animations/transitions for the visual lane. */
export const NO_ANIMATIONS_CSS = `
  *, *::before, *::after {
    animation-duration: 0s !important;
    animation-delay: 0s !important;
    transition-duration: 0s !important;
    transition-delay: 0s !important;
    scroll-behavior: auto !important;
  }
`;
