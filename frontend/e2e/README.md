# Playwright E2E (Phase 8)

End-to-end + component-in-harness tests for the Angular frontend. Two lanes
(ADR 0019), role/name-first selectors (ADR 0020).

## Lanes

| Lane | Flag | Network | Runs |
| --- | --- | --- | --- |
| **A — mocked** (default) | `E2E_LANE=mock` | every `/api/**` answered from `fixtures/data/*.json` | every PR |
| **B — live smoke** | `E2E_LANE=live` | real seeded `docker-compose` stack, LLM/broker stubbed | nightly + pre-release |

## Quick start

```bash
# from frontend/ — the dev stack on :4111 is reused automatically if up
pnpm e2e                 # Lane A, chromium
pnpm e2e:headed          # watch it run
pnpm e2e:ui              # Playwright UI (pick/debug tests)
pnpm e2e:report          # open the last HTML report
pnpm e2e:routes          # fail if a source /api endpoint has no mock
pnpm e2e:visual          # visual-regression project
pnpm e2e:axe             # in-browser axe project
pnpm e2e:update-snapshots  # regenerate visual baselines (review the diff!)
pnpm e2e:live            # Lane B (@smoke) against the live stack
```

In **Lane A** the `webServer` builds + statically serves the `ct` bundle on
`:4111`; locally a running server on `:4111` is reused (`reuseExistingServer`).
That bundle uses the relative `/api`, so the mock glob `**/api/**` is build-agnostic.

In **Lane B** the compose `frontend` service (`ng serve`) owns `:4111`, so
Playwright starts **no** webServer. The dev build talks to an absolute
`apiBaseUrl` (`:8811`) and `ng serve` does not proxy `/api`, so the live login in
`global-setup` must hit the API directly — set `E2E_API_BASE=http://localhost:8811`
(the CI `lane-b` job does this). Lane B greps `@smoke` (six provider-independent
journeys validated against `seed_e2e`; see [COVERAGE.md](./COVERAGE.md)).

## Layout

```
e2e/
  playwright.config.ts     projects, reporters, webServer
  setup/                   global-setup (writes storageState), env.ts
  fixtures/
    index.ts               custom `test` (apiMock + clock + Page Objects)
    api-mock.ts            **/api/** router + the endpoint registry
    data/*.json            versioned fixtures, captured from the live stack
  pages/ components/       Page Objects (no raw locators in specs)
  flows/                   reusable multi-step journeys
  specs/00-smoke … 19-fund functional tests
  visual/  axe/            cross-cutting quality lanes
  utils/                   clock, check-fixtures.mjs
```

## Adding a test

1. **Find selectors role/name-first** (`getByRole`, `getByLabel`). Add a
   `data-testid` only when role/name can't reach it — in the same PR — and list
   it in `SELECTORS.md`.
2. **Put locators/intent in a Page Object**, not the spec.
3. **Need an endpoint?** It's probably already in `api-mock.ts`. If new, add a
   route (and a fixture if it returns data) — `pnpm e2e:routes` enforces this.
4. **Edge cases** via per-test overrides:
   ```ts
   apiMock.override('GET', '/runs/', { json: [] });           // empty
   apiMock.override('GET', '/macro/snapshot/', { status: 500 }); // error
   apiMock.override('GET', '/runs/', { json: [...], delayMs: 800 }); // skeleton
   ```
5. **Auth:** authed by default (shared `storageState`). For unauthenticated specs
   add `test.use({ storageState: UNAUTHENTICATED })`.

## Capturing / refreshing fixtures

Fixtures are real responses from the seeded stack, scrubbed of PII. To refresh,
log in to `:8811`, GET the endpoint, trim large arrays, scrub emails, and drop
the JSON in `fixtures/data/`. Keep them small.

## Visual baselines

Rendering differs across OS, so baselines are the **Linux** ones generated in CI
(`playwright-ci.yml` → update-visual-baselines `workflow_dispatch`) and committed
under `visual/__screenshots__/`. Locally `pnpm e2e:visual` will report diffs;
don't commit macOS baselines.
