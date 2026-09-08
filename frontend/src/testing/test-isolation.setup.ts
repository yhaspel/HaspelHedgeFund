/**
 * Global unit-test isolation — runs ONCE PER SPEC FILE, before the spec module
 * is imported.
 *
 * The Angular unit-test builder starts vitest with `isolate: false`
 * (`@angular/build/src/builders/unit-test/runners/vitest/plugins.js:128`,
 * "align with the Karma/Jasmine experience"), so every spec file handled by a
 * worker shares one process, one `globalThis` and therefore ONE `fake-indexeddb`
 * database. That turns the offline api-cache into cross-file global state, in
 * both directions:
 *
 *  - rows VANISH: `auth.store.ts` logout fires `void clearScope(...)` without
 *    awaiting it, so a logout in one spec file can still be walking the store
 *    while a LATER file's test reads a row it just wrote — surfacing as
 *    "replays cache on status 0 …" / "replays cache on 503 …" failing on a
 *    cache miss;
 *  - rows APPEAR: a 2xx GET cached by one file (notably `/me/`) is still there
 *    for the next file — surfacing as "a status-0 GET never clears tokens …"
 *    failing because the interceptor replays a row that should not exist.
 *
 * Giving each spec file its own IDBFactory removes the shared substrate: a
 * straggler keeps a handle on the previous, now-orphaned factory and cannot
 * reach into this file's data.
 *
 * The swap MUST stay here at module scope (once per file). Doing it in a
 * `beforeEach` orphans the running file's own open connections and breaks
 * fake-indexeddb's per-factory transaction serialisation, which makes the flake
 * WORSE — that variant was tried and reverted.
 */
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, vi } from 'vitest';

// `fake-indexeddb/auto` installs the shared singleton; overriding it here gives
// this spec file a private database. The descriptor `auto` installs is
// writable/configurable, so this is safe on the first file too.
globalThis.indexedDB = new IDBFactory() as unknown as typeof globalThis.indexedDB;

// Proof-of-life. The builder silently ignores an unloadable vitest config, so a
// setup file that never runs would leave the suite green-but-unfixed. Asserted
// by test-isolation.setup.spec.ts.
(globalThis as unknown as { __hfTestIsolationEpoch?: number }).__hfTestIsolationEpoch =
  ((globalThis as unknown as { __hfTestIsolationEpoch?: number }).__hfTestIsolationEpoch ?? 0) + 1;

// vitest restores spies between files but never unstubs globals, so a
// `vi.stubGlobal('fetch', …)` in one spec otherwise leaks into the next.
afterEach(() => {
  vi.unstubAllGlobals();
});

