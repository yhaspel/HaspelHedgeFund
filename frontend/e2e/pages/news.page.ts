import { type Page, type Locator } from "@playwright/test";

/**
 * Page Object for /news (market-news feed grid + detail modal) and the global
 * news chyron (rendered by the app-shell on every page when enabled).
 */
export class NewsPage {
  constructor(public readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto('/news');
  }

  // ---- page header ----
  heading(): Locator {
    return this.page.getByRole('heading', { level: 1, name: 'News' });
  }
  refreshButton(): Locator {
    return this.page.getByRole('button', { name: 'Refresh' });
  }

  // ---- feed grid (tiles are <button> with the headline as accessible name) ----
  /**
   * A feed tile by (substring) headline — the whole tile is a labelled button.
   * Scoped to the `.grid` so it never collides with the chyron's
   * visually-hidden screen-reader headline buttons that share the same text.
   */
  feedGrid(): Locator {
    return this.page.locator('.grid');
  }
  tile(headline: string | RegExp): Locator {
    return this.feedGrid().getByRole('button', { name: headline });
  }
  /** Source text inside the feed (e.g. "reuters.com"). */
  sourceText(name: string | RegExp): Locator {
    return this.page.getByText(name).first();
  }
  /** A sentiment pill on a tile, e.g. "bearish" / "bullish" / "neutral". */
  sentimentPill(sentiment: string): Locator {
    return this.page.locator('.tile .pill.sentiment').filter({ hasText: sentiment });
  }

  // ---- empty state ----
  emptyState(): Locator {
    return this.page.getByText('No news available right now');
  }
  emptyDetail(): Locator {
    return this.page.getByText('Try the Refresh button or check back in a few minutes.');
  }

  // ---- detail modal (hf-modal → role=dialog). The modal does NOT forward a
  // titleId to hf-modal, so the dialog has no accessible name; on /news it is
  // the only dialog, so an unnamed getByRole('dialog') is unambiguous. ----
  detailDialog(): Locator {
    return this.page.getByRole('dialog');
  }
  /** Full summary text rendered in the open modal. */
  detailSummary(text: string | RegExp): Locator {
    return this.detailDialog().getByText(text);
  }
  /** External "Read full article" link in the modal footer. */
  readFullArticle(): Locator {
    return this.detailDialog().getByRole('link', { name: 'Read full article' });
  }
  /** Per-symbol "Analyze <SYM> in a new Run" action in the modal. */
  analyzeButton(symbol: string): Locator {
    return this.detailDialog().getByRole('button', {
      name: `Analyze ${symbol} in a new Run`,
    });
  }
  /** Auto-translation badge in the modal ("Auto-translated from <lang>"). */
  translatedBadge(): Locator {
    return this.detailDialog().getByText(/Auto-translated from/);
  }
  /** "View original" / "View translation" toggle in the modal. */
  viewOriginalToggle(): Locator {
    return this.detailDialog().getByRole('button', { name: /View (original|translation)/ });
  }
  closeButton(): Locator {
    return this.detailDialog().getByRole('button', { name: 'Close', exact: true });
  }

  // ---- chyron (rendered by app-shell; track-wrap is aria-hidden so the
  // animated region is reached via its stable class — no role/label exists) ----
  chyron(): Locator {
    return this.page.locator('.chyron').first();
  }
  chyronTrack(): Locator {
    return this.page.locator('.chyron .track').first();
  }
  /** A headline button inside the animated chyron track. */
  chyronHeadline(text: string | RegExp): Locator {
    return this.page.locator('.chyron .track .hl').filter({ hasText: text }).first();
  }
  /** Visible pause/resume control (aria-label flips with state). */
  chyronPauseToggle(): Locator {
    return this.page.getByRole('button', {
      name: /(Pause|Resume) scrolling news banner/,
    });
  }
}
