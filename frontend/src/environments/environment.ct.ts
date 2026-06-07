// Phase 8 WS-18 — the `ct` build configuration: a production build that ALSO
// enables the dev-only `/__ct/:component` component-in-harness route, so the
// Playwright component lane can mount isolated widgets in a real browser. The
// real production build (environment.prod.ts) keeps ctHarness:false.
export const environment = {
  production: true,
  apiBaseUrl: '/api',
  ctHarness: true,
};
