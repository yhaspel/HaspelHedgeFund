# Local Model Setup with Ollama

The app can run Large Language Models on **your own computer** instead of (or alongside) a paid cloud provider, through a free tool called **Ollama**. Local models cost nothing per run, never leave your machine, and have no rate limits or daily quotas. The trade-off: you need decent hardware, and small local models reason less well than the frontier cloud models.

Use local models for development, smoke-testing the pipeline, or any time you want to iterate without spending. Many users keep cloud keys for "real" runs and a local model for everything else.

This guide walks through the whole loop: install Ollama, pull a model, connect it to the app, select it for an agent, run with it, stop it, and remove it.

> [!NOTE]
> "Local" models (this guide) are not the same thing as the **free** models the `dev` cost tier picks. The `dev` tier uses free models hosted on **OpenRouter** — still cloud-served, just at zero per-token price. Local models, by contrast, run *on your hardware* with no internet involved at inference time.

## Before you start — hardware check

Ollama loads the model into memory. As a rough rule of thumb:

- **3–4 B model:** ~2–3 GB of memory. Runs on almost anything modern. Best for the quickest discovery checks; can be flaky on the structured-JSON output the agents emit.
- **7–8 B model:** ~5 GB. **The reliable floor** for agent runs. Comfortable on 16 GB RAM and trivial on 24 GB+.
- **13–14 B model:** ~9 GB. Better reasoning, still comfortable on 24 GB.
- **30 B / 32 B model (4-bit quantised):** ~19 GB. Tight on 24 GB; great on 32 GB+.

Apple Silicon (M-series) uses unified memory and the Metal GPU — exceptionally well-suited to Ollama. Other GPUs (NVIDIA / AMD) also work; the Ollama installer figures the backend out automatically.

You do **not** need an Ollama account. There is no signup. Models are pulled from a public library, and the daemon runs entirely on your own machine.

## Install Ollama

**macOS:**

```
brew install ollama
```

Or download the standalone app from `ollama.com/download`.

**Linux:**

```
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** download the installer from `ollama.com/download`.

## Pull a model

Start the daemon first. On macOS via brew:

```
brew services start ollama
```

(On Linux: `systemctl start ollama` if installed via the script. On Windows: the installed app starts the daemon for you. On any platform you can also run `ollama serve` in a terminal — that runs the daemon in the foreground until you Ctrl-C.)

Then pull a model. **Recommended for first run: `qwen2.5:7b`** — a 7 B instruct model, ~4.7 GB on disk, strong at the structured outputs the agents need:

```
ollama pull qwen2.5:7b
```

Other safe picks of similar size: `llama3.1:8b` (~4.7 GB) or `mistral:7b` (~4.4 GB). For low-RAM machines you can try a 3 B model like `qwen2.5:3b`, with the caveat that structured-output failures get more frequent.

Verify the pull worked:

```
ollama run qwen2.5:7b "Reply with exactly: ok"
```

Should print something close to `ok` after a second or two of warmup.

## Connect the app to Ollama

1. Open **Settings → Models** in the app.
2. In the **Provider keys** card, find the **Ollama host** field.
3. Paste `http://localhost:11434` and press **Save provider keys**.
4. The **Available models** table at the bottom of the page refreshes; your pulled model(s) appear there with the **local** tier and a **FREE** badge. Their `available` flag is green when the daemon is reachable.

If your model does not appear, see [Troubleshooting](#troubleshooting) below.

## Select a local model

You can pick a local model in three places, depending on what you want it to do:

**Per agent, in the Model panel.** On the **New Run** or **New Backtest** page, click **Expand** on the Models card and use a per-agent dropdown to choose your local model for one agent — or for every agent. Local models always appear in these dropdowns regardless of which cost tier (preset) is active; tier scoping does not hide them.

**As the default for every agent, in Settings.** In **Settings → Models**, the **Default model (applies to every agent)** select assigns one model to all agents at once. Pick your local model there and every subsequent run uses it unless you override per agent. This is the fastest way to make the whole council run locally.

**Via the `hybrid` preset.** When the app has discovered a local model, the `hybrid` cost tier automatically routes the analytical agents (fundamentals, technicals, valuation, sentiment), the macro agent, and the news-digest agent to that local model — while keeping the personas and orchestration (Risk Manager, Portfolio Manager, CIO) on the cloud frontier models. This is the recommended day-to-day setup: frontier judgement on the costly decisions, free local compute everywhere else.

## Run with a local model

Once an agent has been pointed at a local model, runs proceed normally. What to expect:

- **Cost is `$0.00`** for every LLM call routed to the local model — the run detail page and the backtest cost ledger show this explicitly. Cloud agents still incur their usual cost.
- **Latency depends on your hardware.** A 7–8 B model on Apple Silicon answers in a few seconds per call. On slower machines it can be tens of seconds. Long backtests over many tickers can take a while.
- **Quality is the trade-off.** Small local models miss subtleties that the frontier models catch, particularly on long-context reasoning. For a backtest whose result you care about, validate the final configuration on a frontier model.

On a run's detail page, each LLM call row shows the provider — confirm `ollama` for the agents you routed there.

## Stop and free memory

There are three levels — pick what fits.

**Unload the model from memory** (frees the ~5 GB the weights take; the daemon stays up at ~30 MB):

```
ollama stop qwen2.5:7b
```

Ollama also auto-unloads idle models after about five minutes, so walking away does the same thing on its own.

**Stop the daemon** (nothing listens on `:11434` until you start it again):

```
brew services stop ollama
```

On Linux: `systemctl stop ollama`. On Windows: quit the Ollama app from the system tray. If you started with `ollama serve` in a foreground terminal, Ctrl-C in that terminal.

**Per run.** You do not need to stop anything between runs. The daemon and the loaded model are reusable across as many runs as you like.

## Remove a model

To delete a pulled model and reclaim its disk space:

```
ollama rm qwen2.5:7b
```

To list everything you currently have pulled:

```
ollama list
```

To uninstall Ollama entirely (macOS via brew):

```
brew uninstall ollama
```

On other platforms, follow that platform's standard uninstall path. Ollama keeps pulled models in a system data directory; uninstall typically leaves them behind, so `ollama rm` first if you also want the disk space back.

## Troubleshooting

**The model does not appear in the app's "Available models" table after I set the host.** Check, in order:

- The daemon is running: `curl http://localhost:11434/api/tags` should return JSON with a `models` array. If it errors, start the daemon (`brew services start ollama`).
- You did actually pull a model: `ollama list` should show at least one row. If empty, run an `ollama pull` first.
- The host field in Settings exactly matches `http://localhost:11434` — no trailing slash, scheme included, no quotes.
- The discovery is cached for 60 seconds per host. After changing the host, refresh the Settings page once.

**My run does not use the local model even though I selected it.** Open the run's detail page and check the LLM-call rows: the `provider` column tells you which adapter actually ran the call. If it says something other than `ollama`, the override did not get through — typically because a higher-precedence setting won (the Settings per-agent default, or the active preset's override). Re-pick your local model in the Model panel and re-submit.

**Hybrid preset is not routing to my local model.** Two things to check:

1. Your `ollama_host` is set and the local model appears in the **Available models** table (do the steps above first).
2. You are running an **ad-hoc Run** with the hybrid preset, not a **strategy cycle**. In `dev` settings, hybrid strategy cycles are intentionally downgraded to the `dev` preset to protect the budget — set `LLM_DEFAULT_PRESET=hybrid` (the production default) to exercise hybrid-local on strategy cycles.

**The model is very slow.** Larger models are slower; try a smaller tag (e.g. drop from 14 B to 7 B). Make sure no other heavy program is using GPU/RAM at the same time. On Apple Silicon, check Activity Monitor for memory pressure — if the system is paging, the model will crawl.

**Out-of-memory error.** The model is too big for your machine. Try a smaller tag (e.g. drop from 14 B to 7 B, or 7 B to 3 B). On laptops with 8 GB of RAM, stick to 3–4 B models.

**Where are pulled models stored on disk?** macOS: `~/.ollama/models`. Linux: `/usr/share/ollama/.ollama/models`. Useful when sizing free disk space before pulling a big model.
