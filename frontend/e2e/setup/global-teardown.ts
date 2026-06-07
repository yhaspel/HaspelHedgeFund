import { type FullConfig } from '@playwright/test';

/** Reserved for Lane B stack cleanup. The storageState file is git-ignored and
 *  intentionally left in place for post-run debugging. */
export default async function globalTeardown(_config: FullConfig): Promise<void> {
  // no-op
}
