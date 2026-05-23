/**
 * P3-prereq-4 — market-news signal store.
 *
 * The feed is paginated server-side (12 per page, capped at 48 total). The
 * store keeps a cumulative `items` list across pages so the News page can
 * grow as the user clicks "Load more". The chyron is a slice of the same
 * `items` list, so it lights up as soon as page 1 lands and never re-fetches.
 */
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, of, shareReplay, tap } from 'rxjs';

import { ApiClient } from '../core/api/api-client';
import {
  MarketNewsItem,
  NewsFeed,
  NewsPreferences,
  NewsPreferencesResponse,
  SentimentModelChoice,
} from '../core/models/news.model';

const FEED_TTL_MS = 30 * 1000;

@Injectable({ providedIn: 'root' })
export class NewsStore {
  private readonly api = inject(ApiClient);

  private readonly _items = signal<MarketNewsItem[]>([]);
  private readonly _meta = signal<NewsFeed | null>(null);
  private readonly _page = signal(0);
  private readonly _hasMore = signal(false);
  private readonly _totalAvailable = signal(0);

  private readonly _preferences = signal<NewsPreferences | null>(null);
  private readonly _sentimentChoices = signal<SentimentModelChoice[]>([]);
  private readonly _loading = signal(false);
  private readonly _loadingMore = signal(false);
  private readonly _refreshing = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _toast = signal<string | null>(null);

  private _feedFetchedAt = 0;
  private _feedInFlight: Observable<NewsFeed> | null = null;
  private _prefsInFlight: Observable<NewsPreferencesResponse> | null = null;

  readonly items = this._items.asReadonly();
  readonly meta = this._meta.asReadonly();
  readonly page = this._page.asReadonly();
  readonly hasMore = this._hasMore.asReadonly();
  readonly totalAvailable = this._totalAvailable.asReadonly();
  readonly preferences = this._preferences.asReadonly();
  readonly sentimentChoices = this._sentimentChoices.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly loadingMore = this._loadingMore.asReadonly();
  readonly refreshing = this._refreshing.asReadonly();
  readonly error = this._error.asReadonly();
  readonly toast = this._toast.asReadonly();

  /** Top N items for the global chyron — first page's first slice. */
  readonly chyronItems = computed(() => {
    const prefs = this._preferences();
    const meta = this._meta();
    const n = prefs?.chyron_item_count ?? meta?.chyron_item_count ?? 8;
    return this._items().slice(0, n);
  });

  /** Whether the chyron should render. */
  readonly chyronEnabled = computed(
    () =>
      (this._preferences()?.chyron_enabled ??
        this._meta()?.chyron_enabled ??
        false) &&
      this._items().length > 0,
  );

  readonly sentimentEnabled = computed(
    () =>
      this._meta()?.sentiment_enabled ??
      this._preferences()?.sentiment_enabled ??
      false,
  );

  /** Look an item up by id — used by /news?article=<id> deep-link. */
  itemById(id: number): MarketNewsItem | null {
    return this._items().find((it) => it.id === id) ?? null;
  }

  setError(message: string | null): void {
    this._error.set(message);
  }

  setToast(message: string | null): void {
    this._toast.set(message);
    if (message) {
      setTimeout(() => {
        if (this._toast() === message) this._toast.set(null);
      }, 4000);
    }
  }

  /** Load page 1 (TTL-cached + in-flight dedup). */
  loadFeed(): Observable<NewsFeed> {
    const now = Date.now();
    if (this._meta() && now - this._feedFetchedAt < FEED_TTL_MS) {
      return of(this._meta()!);
    }
    if (this._feedInFlight) return this._feedInFlight;

    this._loading.set(true);
    this._error.set(null);
    this._feedInFlight = this.api.get<NewsFeed>('/news/feed/?page=1').pipe(
      tap({
        next: (feed) => {
          this._applyPage(feed, /*append*/ false);
          this._loading.set(false);
          this._feedFetchedAt = Date.now();
          this._feedInFlight = null;
        },
        error: (err) => {
          const msg =
            err?.error?.detail || err?.message || 'Failed to load news.';
          this._error.set(msg);
          this._loading.set(false);
          this._feedInFlight = null;
        },
      }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    return this._feedInFlight;
  }

  /** Load the next page and append to the existing items list. */
  loadMore(): Observable<NewsFeed | null> {
    if (!this._hasMore() || this._loadingMore()) return of(null);
    const next = this._page() + 1;
    this._loadingMore.set(true);
    return this.api.get<NewsFeed>(`/news/feed/?page=${next}`).pipe(
      tap({
        next: (feed) => {
          this._applyPage(feed, /*append*/ true);
          this._loadingMore.set(false);
        },
        error: (err) => {
          const msg =
            err?.error?.detail || err?.message || 'Failed to load more news.';
          this._error.set(msg);
          this._loadingMore.set(false);
        },
      }),
    );
  }

  /** Force-refresh page 1 via ?refresh=1; resets pagination. */
  refresh(): Observable<NewsFeed> {
    this._refreshing.set(true);
    this._error.set(null);
    return this.api.get<NewsFeed>('/news/feed/?refresh=1&page=1').pipe(
      tap({
        next: (feed) => {
          this._applyPage(feed, /*append*/ false);
          this._feedFetchedAt = Date.now();
          this._refreshing.set(false);
        },
        error: (err) => {
          if (err?.status === 429) {
            this.setToast(
              err?.error?.detail ||
                'Refreshing too fast — try again in a moment.',
            );
          } else {
            const msg =
              err?.error?.detail || err?.message || 'Failed to refresh news.';
            this._error.set(msg);
          }
          this._refreshing.set(false);
        },
      }),
    );
  }

  private _applyPage(feed: NewsFeed, append: boolean): void {
    this._meta.set(feed);
    this._page.set(feed.page);
    this._hasMore.set(feed.has_more);
    this._totalAvailable.set(feed.total_available);
    if (append) {
      // Filter out any ids we already have (defensive — server slices cleanly).
      const existing = new Set(this._items().map((i) => i.id));
      const fresh = feed.items.filter((i) => !existing.has(i.id));
      this._items.set([...this._items(), ...fresh]);
    } else {
      this._items.set(feed.items);
    }
  }

  loadPreferences(): Observable<NewsPreferencesResponse> {
    if (this._prefsInFlight) return this._prefsInFlight;
    this._prefsInFlight = this.api
      .get<NewsPreferencesResponse>('/news/preferences/')
      .pipe(
        tap({
          next: (r) => {
            this._preferences.set(r.preferences);
            this._sentimentChoices.set(r.sentiment_model_choices);
            this._prefsInFlight = null;
          },
          error: () => {
            this._prefsInFlight = null;
          },
        }),
        shareReplay({ bufferSize: 1, refCount: false }),
      );
    return this._prefsInFlight;
  }

  savePreferences(
    patch: Partial<NewsPreferences>,
  ): Observable<NewsPreferencesResponse> {
    const prev = this._preferences();
    if (prev) this._preferences.set({ ...prev, ...patch });
    return this.api
      .put<NewsPreferencesResponse>('/news/preferences/', patch)
      .pipe(
        tap({
          next: (r) => {
            this._preferences.set(r.preferences);
            this._sentimentChoices.set(r.sentiment_model_choices);
            // Force the next feed-load to refetch (chyron/feed may change).
            this._feedFetchedAt = 0;
          },
          error: (err) => {
            if (prev) this._preferences.set(prev);
            const msg =
              err?.error?.detail ||
              err?.message ||
              'Failed to save settings.';
            this._error.set(msg);
          },
        }),
      );
  }
}
