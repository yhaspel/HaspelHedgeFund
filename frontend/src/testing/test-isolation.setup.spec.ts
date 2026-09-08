/**
 * Guard: the isolation setup file must actually be loaded.
 *
 * `frontend/vitest.config.ts` was dead code for a long time — with
 * `runnerConfig: true` the Angular builder searches only for
 * `vitest-base.config.*`, finds nothing, and passes `config: false`, which also
 * disables vitest's own config discovery. Anything wired up the wrong way is
 * therefore silently inert, which is exactly how two earlier attempts at this
 * flake fix were wasted. This asserts the setup ran.
 */
import { describe, expect, it } from 'vitest';

describe('unit-test isolation setup', () => {
  it('is loaded for every spec file', () => {
    const epoch = (globalThis as unknown as { __hfTestIsolationEpoch?: number })
      .__hfTestIsolationEpoch;
    expect(epoch, 'test-isolation.setup.ts did not run — check angular.json setupFiles').toBeGreaterThan(0);
  });

  it('gives this spec file a private IndexedDB', async () => {
    // The factory is swapped at setup-module scope, so this file starts with a
    // database of its own and cannot see `hf-offline` rows written by the offline
    // specs that share this worker. If the swap regressed, a worker that already
    // ran one of those files would surface their database here.
    const dbs = await indexedDB.databases();
    expect(
      dbs.map((d) => d.name),
      'this spec file can see another file\'s IndexedDB — per-file isolation regressed',
    ).toEqual([]);
  });
});
