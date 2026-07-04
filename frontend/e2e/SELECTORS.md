# E2E selectors catalogue

Policy: ADR 0020.
**Order:** `getByRole(name)` → `getByLabel`/`getByPlaceholder` → `getByTestId`.
Add a `data-testid` only when role/name can't reach an element; record it here in
the **same PR** that adds the test. Banned: CSS/XPath, nth-child, class chains,
copy-matching.

## Stable role/name anchors (no test-id needed)

| Surface | Anchor |
| --- | --- |
| Sidebar | `getByRole('navigation', { name: 'Primary' })` → `getByRole('link', { name })` |
| Skip link | `getByRole('link', { name: 'Skip to main content' })` |
| Theme toggle | `getByRole('button', { name: /Switch to (light\|dark) theme/ })` |
| ⌘K trigger | `getByRole('button', { name: /Open command palette/ })` |
| Log out | `getByRole('button', { name: 'Log out' })` |
| Login/Signup | `getByLabel('Email')`, `getByLabel('Password')`, `getByRole('button', { name: 'Log in' \| 'Sign up' })` |
| Modals | `getByRole('dialog', { name })` |
| Page H1 | `getByRole('heading', { level: 1 })` |

## Pre-existing `data-testid`s (graph editor, backtests-new, schedules)

These shipped before Phase 8 and are reused by the suite:

`bt-graph-select`, `bt-run-graph`, `sched-graph-select`, `no-own`, `new-graph-btn`,
`new-graph-name`, `node-*`, `palette-*`, `inspector-model`, `inspector-delete`,
`validation-pill`, `warnings`, `tier-select`, `tier-model-select`, `save-btn`,
`export-btn`, `import-btn`, `versions-btn`, `diff-a`, `diff-b`, `diff-result`,
`load-v*`.

> Run `grep -rho 'data-testid="[^"]*"' src/app | sort -u` for the live list.

## Backfilled for E2E (Phase 8)

Added by the workstreams as needed. Each row names the test that required it.

| `data-testid` | Element | Required by |
| --- | --- | --- |
| _(none yet)_ | | |
