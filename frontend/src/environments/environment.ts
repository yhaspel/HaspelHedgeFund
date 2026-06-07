export const environment = {
  production: false,
  apiBaseUrl: 'http://localhost:8811/api',
  // `/__ct/:component` component-in-harness route (Phase 8 WS-18 / ADR 0020).
  // Off by default (and in prod); the `ct` build config swaps in
  // environment.ct.ts (ctHarness:true) for the Playwright component lane only.
  ctHarness: false,
};
