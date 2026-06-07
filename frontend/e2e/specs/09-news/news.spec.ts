import { test, expect } from '../../fixtures';
import newsFeed from '../../fixtures/data/news-feed.json';

/** WS-9 · News (feed grid / detail modal / global chyron). */
test.describe('WS-9 · News', () => {
  test('NW-01 @mobile feed grid renders tiles with sentiment + source', async ({ news }) => {
    await news.goto();
    await expect(news.heading()).toBeVisible();

    // Tiles are labelled buttons; one seeded headline + its source are visible.
    await expect(news.tile(/Air New Zealand plans for elevated fuel costs/)).toBeVisible();
    await expect(news.sourceText('reuters.com')).toBeVisible();

    // Sentiment is enabled in the fixture → pills render on the tiles.
    await expect(news.sentimentPill('bearish').first()).toBeVisible();
    await expect(news.sentimentPill('bullish').first()).toBeVisible();
  });

  test('NW-02 tile → detail modal shows full content', async ({ news }) => {
    await news.goto();
    await news.tile(/Pfizer's monthly obesity shot/).click();

    await expect(news.detailDialog()).toBeVisible();
    // The modal carries the full (un-clamped) publisher summary.
    await expect(news.detailSummary(/similar side-effect profile as rival Novo Nordisk/)).toBeVisible();
    await expect(news.readFullArticle()).toBeVisible();
  });

  test('NW-03 detail "Analyze" CTA routes to /runs/new with the prefilled ticker', async ({
    news,
    page,
  }) => {
    await news.goto();
    // This story is tagged PFE; the modal exposes a per-ticker Analyze action.
    await news.tile(/Pfizer's monthly obesity shot/).click();
    await expect(news.detailDialog()).toBeVisible();

    await news.analyzeButton('PFE').click();
    await expect(page).toHaveURL(/\/runs\/new\?ticker=PFE/);
  });

  test('NW-04 chyron renders with headlines', async ({ news }) => {
    await news.goto();
    await expect(news.chyron()).toBeVisible();
    // First chyron slice = first feed items; assert a seeded headline shows.
    await expect(news.chyronHeadline(/Air New Zealand plans for elevated fuel costs/)).toBeVisible();
    await expect(news.chyronPauseToggle()).toBeVisible();
  });

  test('NW-05 chyron pause control + hover auto-pause (WCAG 2.2.2)', async ({ news }) => {
    await news.goto();
    const toggle = news.chyronPauseToggle();
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');

    // Hovering the marquee pauses motion (WCAG 2.2.2) — the container gets the
    // `paused` class and the track's CSS animation halts.
    await news.chyron().hover();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect(news.chyron()).toHaveClass(/paused/);
    await expect(news.chyronTrack()).toHaveCSS('animation-play-state', 'paused');
  });

  test.describe('reduced motion', () => {
    test('NW-06 chyron honours prefers-reduced-motion (no auto-scroll)', async ({ news, page }) => {
      // Emulate reduced-motion BEFORE navigation so the chyron's matchMedia read
      // at init returns true and it adds the `.reduced-motion` class.
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await news.goto();
      await expect(news.chyron()).toBeVisible();
      // .reduced-motion .track sets `animation: none` → static strip.
      await expect(news.chyron()).toHaveClass(/reduced-motion/);
      await expect(news.chyronTrack()).toHaveCSS('animation-name', 'none');
      // Headlines are still present, just not animated.
      await expect(news.chyronHeadline(/Air New Zealand plans for elevated fuel costs/)).toBeVisible();
    });
  });

  test('NW-07 auto-translation badge + "View original" toggle', async ({ news, apiMock }) => {
    // Promote a translated (non-English) item to the top of the feed.
    const translated = {
      ...newsFeed.items[0],
      id: 999001,
      language: 'de',
      translated_from: 'de',
      headline: 'Bundesbank signals caution on rate path',
      summary: 'The German central bank flagged a measured approach to easing.',
      original_headline: 'Bundesbank signalisiert Vorsicht beim Zinspfad',
      original_summary: 'Die Bundesbank signalisierte ein behutsames Vorgehen bei der Lockerung.',
    };
    apiMock.override('GET', '/news/feed/', {
      json: { ...newsFeed, items: [translated, ...newsFeed.items] },
    });

    await news.goto();
    await news.tile(/Bundesbank signals caution on rate path/).click();
    await expect(news.detailDialog()).toBeVisible();

    // Badge resolves the code → "German".
    await expect(news.translatedBadge()).toContainText('Auto-translated from German');

    // Toggle swaps the translated headline for the original (German) text.
    await expect(news.detailDialog().getByText('Bundesbank signals caution on rate path')).toBeVisible();
    await news.viewOriginalToggle().click();
    await expect(
      news.detailDialog().getByText('Bundesbank signalisiert Vorsicht beim Zinspfad'),
    ).toBeVisible();
  });

  test('NW-08 empty feed → empty state; 500 → graceful empty-state affordance', async ({
    news,
    apiMock,
  }) => {
    // Empty list.
    apiMock.override('GET', '/news/feed/', {
      json: { ...newsFeed, items: [], total_available: 0, has_more: false, chyron_enabled: false },
    });
    await news.goto();
    await expect(news.emptyState()).toBeVisible();
    await expect(news.emptyDetail()).toBeVisible();

    // 500: the page has no dedicated error template — it degrades to the same
    // empty state with the Refresh affordance still present (the recovery path).
    apiMock.override('GET', '/news/feed/', { status: 500 });
    await news.goto();
    await expect(news.emptyState()).toBeVisible();
    await expect(news.refreshButton()).toBeVisible();
  });
});
