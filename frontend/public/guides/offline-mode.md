# Offline Mode

The app runs with no internet connection — on a plane, in a coffee shop with dead
Wi-Fi, or fully air-gapped. Analysis runs use a **local AI model** (Ollama), and
every screen keeps working from the last data it saw.

"Offline" is not one state. Two levels are handled differently.

## The two offline levels

**L1 — internet down, local stack up.** Postgres, the Django backend, Celery,
and Ollama are all running; only the outside world is unreachable. The app is
**fully functional**: runs and strategy cycles execute on the local model at
`$0.00`, every screen shows the latest values already stored locally, and no
external request is attempted.

**L2 — backend unreachable.** The backend is down, crashed, or not started. The
browser is on its own: a service worker serves the app shell so a refresh still
boots, cached data fills the screens, and the app is **read-only**.

| What you can do | Online | L1 (no internet) | L2 (no backend) |
| --- | --- | --- | --- |
| Open the app / refresh a page | ✅ | ✅ | ✅ (from the service-worker cache) |
| See fund, portfolios, runs, news, screener | ✅ live | ✅ latest stored values | ✅ last-synced values (marked stale) |
| Stay logged in | ✅ | ✅ | ✅ (an existing session keeps working) |
| Log in / sign up | ✅ | ✅ | ❌ (backend down) |
| Run analysis, strategy cycles, backtests | ✅ | ✅ on the local model, $0 | ❌ |
| Refresh external data (prices, news, filings) | ✅ | ❌ paused — shows last stored | ❌ |
| Cloud AI models (Anthropic / OpenRouter) | ✅ | ❌ blocked — local model only | ❌ |
| Save orders, settings, watchlists | ✅ | ✅ | ❌ blocked (nothing is queued) |
| Email / Telegram notifications | ✅ | ❌ paused | ❌ |

Writes are never queued while offline — a queued order could execute later at a
different price. Blocked writes fail fast with a clear message.

## Turning offline mode on and off (L1)

Offline mode is an explicit switch, never auto-detected. In your `.env`:

```
OFFLINE_MODE=1
OFFLINE_LLM_MODEL=qwen2.5:7b
```

Then bring the stack up and start the local model:

```
docker compose -f infra/docker-compose.yml up
infra/ollama-start.sh --ensure-model
```

`ollama-start.sh --ensure-model` starts the Ollama daemon, pins single-request
concurrency (`OLLAMA_NUM_PARALLEL=1` — steady memory on a laptop), and pulls the
model only if it is missing and a pull can proceed. Turn offline mode off by
setting `OFFLINE_MODE=0` and restarting the stack.

You can also **simulate the read-only L2 experience** without touching your
network from **Settings → Data & News → Offline → "Simulate offline"**.

## Model prerequisites (local AI)

Offline runs use whichever Ollama model you set in `OFFLINE_LLM_MODEL`
(default `qwen2.5:7b`). Pick one that fits your machine:

| Model size | RAM | Verdict on a 24 GB laptop |
| --- | --- | --- |
| 3–4 B (e.g. `qwen2.5:3b`) | ~2–3 GB | Works, but JSON-structure slips too often — emergencies only |
| **7–8 B (`qwen2.5:7b`, default)** | ~5 GB | **Recommended** — proven on the full council |
| 13–14 B (`qwen2.5:14b`) | ~9 GB | Optional quality bump; watch memory pressure |
| 30–32 B (4-bit) | ~19 GB | Excluded — too tight next to Docker + browser |

A single-ticker council run on a 7 B model takes **minutes, not seconds**, and
requests queue rather than compete for memory. See the *Local Model Setup* guide
to install Ollama and pull a model.

## Service-worker refresh — a stale app after an update?

The service worker is what lets a refresh boot the app with the backend down. It
also caches the app itself, so after you `git pull` + rebuild you may need to
reload once for the new version. If a screen looks wrong or stuck:

- **Hard reload** (⇧⌘R / Ctrl-Shift-R) bypasses the cache for that load.
- Add **`?ngsw-bypass=true`** to the URL to bypass the service worker per-request.
- **DevTools → Application → Service workers → Unregister**, then **Clear
  storage**, resets a wedged client.
- When a new version is ready, the app shows a **"New version available — Reload"**
  prompt; accepting it swaps in the update.

## Using it from another device (LAN)

Service workers only register on `http://localhost` / `127.0.0.1` (a secure
context). Opening the app from a phone or another laptop over a plain-`http` LAN
address will skip the service worker, so the offline refresh won't work there.
This is a browser security rule, not an app limitation.

## Privacy — what's cached, and when it's cleared

Last-known API responses are stored **in your browser** (IndexedDB) so the app
can show them offline. This cache is scoped to your user and **cleared when you
log out**. It is not encrypted at rest — the same trust model as your saved
login on this device. On a shared machine, log out when you're done.
