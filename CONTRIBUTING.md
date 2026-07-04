# Contributing

Thanks for your interest! HaspelHedgeFund is provided **as-is**, maintained on a
best-effort basis, with **no support SLA**.

- **Issues and PRs are welcome.** For a non-trivial change, open an issue first to
  discuss the approach.
- **Tests must pass without real API keys.** The suite runs on recorded cassettes,
  sqlite test settings, and eager Celery tasks — never introduce a test that needs
  a live network call or a real key, and never commit real keys.
- **Run the checks before pushing:**
  - Backend: `cd backend && uv run pytest` and `uv run ruff check .`
  - Frontend: `cd frontend && pnpm test --watch=false && pnpm build`
  - All hooks: `pre-commit run --all-files` (ruff, prettier, frontend gates, gitleaks).
- **Conventional Commits** are enforced on commit messages (see
  `.pre-commit-config.yaml`).
- **No secrets, ever.** A `gitleaks` hook + CI job scan for leaked credentials.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](./LICENSE).
