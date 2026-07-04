# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** through GitHub's private
vulnerability reporting: the repository's **Security → Report a vulnerability**
(GitHub Security Advisories). **Do not open a public issue** for a security
problem.

There is **no bounty program** — this is provided as-is, educational software —
but reports are appreciated and will be addressed on a best-effort basis.

## Scope notes

- The app is **bring-your-own-key** and designed for **local / trusted-network
  self-hosting**. It ships with signup open (`AllowAny`), so **do not expose an
  instance to the public internet as-is**. Set real `DJANGO_SECRET_KEY`,
  `JWT_SIGNING_KEY`, and `FIELD_ENCRYPTION_KEY` values (and `DJANGO_ENV=prod`)
  before any non-local deployment.
- Trading is **paper-only by design**; live-broker auto-execution is blocked in
  code.
- Secrets (BYO provider keys, broker credentials) are stored encrypted at rest
  with a dedicated `FIELD_ENCRYPTION_KEY`, decoupled from `SECRET_KEY` (see the
  README). Losing `FIELD_ENCRYPTION_KEY` is unrecoverable by design — back it up.
- A `gitleaks` pre-commit hook and CI job scan for accidentally committed
  credentials.
