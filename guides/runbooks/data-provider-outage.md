# Runbook: data-provider outage

Data providers are FMP (market data + fundamentals), SEC EDGAR (filings), FRED
(macro), and Tiingo (news). When one is down or rate-limiting, the app degrades to
the last values it persisted rather than hard-failing.

## Symptoms

- Screens show stale prices/fundamentals, or a "last known value" / stale badge.
- Runs complete but analysis reads old data.
- Operator alert: **"Data provider outage: <provider>"** (fired after repeated
  transport failures from one provider, throttled to one ping per hour).

## Diagnosis

```bash
# Health probe — is the instance up, and is it in offline mode?
curl -s http://localhost:8811/api/health/ | python -m json.tool

# Recent provider errors (JSON logs carry request_id/run_id):
docker compose -f infra/docker-compose.yml logs --tail=150 web worker \
  | grep -i "provider\|httpx\|offline\|429\|timeout"
```

Distinguish two cases:

- **Deliberate offline mode** (`OFFLINE_MODE=1`): every external provider is
  *fenced* on purpose — reads serve last-persisted DB rows and LLM calls run on
  the local model. This is expected; the outage alert is suppressed in this mode.
- **Real outage** while online: the provider is unreachable (network, provider
  down) or rate-limiting (HTTP 429). Reads degrade to the last persisted values.

## Remediation

- **Rate limits / transient outage:** wait it out — reads keep serving the last
  values. Free OpenRouter/Tiingo tiers rate-limit under load; a paid key or a
  slower cadence helps.
- **Bad or missing key:** check the provider key (Settings → Providers, or the
  `.env` fallback keys). A 401/403 is a key problem, not an outage.
- **EDGAR:** the SEC requires a real `EDGAR_USER_AGENT` (`Your Name you@example.com`);
  a missing/placeholder one gets throttled.
- **Genuinely offline / travelling:** set `OFFLINE_MODE=1` and restart — the app
  stays usable on the local model + last-seen data. See
  [../offline-mode.md](../offline-mode.md).

## Prevention

- Set real, non-rate-limited keys for the providers you depend on.
- Keep `EDGAR_USER_AGENT` set to a real contact.
- The operator outage alert is throttled (3 strikes in 15 min → one alert, then a
  1-hour cooldown), so a sustained outage pings you once, not continuously.
