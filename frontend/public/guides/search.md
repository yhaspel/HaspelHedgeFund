# App-Wide Search

The app has a fast, keyboard-driven search that reaches across your whole workspace, plus a deeper text search on the Runs list. Knowing which to use saves a lot of clicking.

## The command palette (search anywhere)

Press **⌘K** (Mac) or **Ctrl+K** (Windows/Linux) from any page — or click the **Search runs, strategies, backtests…** box in the top bar — to open the **command palette**. Start typing and matching results appear instantly.

It searches three things:

- **Runs** — by run number, or by any **ticker** the run analysed.
- **Strategies** — by name, or by strategy type (its "kind").
- **Backtests** — by number or name, or by any ticker in the backtest's universe.

Navigate with the **arrow keys**, open the highlighted result with **Enter**, and dismiss the palette with **Esc** (you can also click a result). Searching is instant and case-insensitive, and it shows up to twenty matches at a time, so keep queries specific.

> [!NOTE]
> The palette is for jumping to an **entity** by its name, number, or ticker. It does **not** search the *contents* of a run's transcript, nor guides, settings, broker accounts, schedules, or the leaderboard. For transcript text, use the Runs search below.

### Searching usefully

- **By ticker.** Type `AAPL` to pull up every run and backtest that touched Apple — the quickest way to gather everything you've done on one name.
- **By name.** Type part of a strategy's name to jump straight to it, instead of scrolling the Strategies list.
- **By number.** If you know a run or backtest number, type it to go directly there.

## Searching inside runs (transcript search)

The **Runs** list has its own **Search transcripts…** box. Unlike the palette, this performs a deep **full-text** search across what was actually *said* in each run — the decisions, agent theses and rationales, risk summaries, and the news headlines the council saw. Use it to answer questions like *"which runs discussed a dividend cut?"* or *"where did an agent mention margins?"*, where the thing you remember is a phrase, not a ticker.

The Runs list also has quick filters next to it:

- **Source** — **All**, **Manual** (one-off analyses), or **Strategy** (candidate runs from a cycle).
- **Status** — **Any**, **Done**, **Running**, or **Failed**.

Combine them to narrow a long history fast — for example, only **Done**, **Strategy** runs that mention a term.

## A note on the other search boxes

Two more search inputs exist and are easy to confuse with the above:

- The **Guides** page (this library) has a search box that looks across guide titles, section headings, and glossary terms — handy when you can't remember which guide covers something.
- The **Strategies** and **Backtests** lists don't have their own search boxes; use the **command palette** to find a strategy or backtest by name or number.
