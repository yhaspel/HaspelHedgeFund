import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { NewsStore } from '../../abstraction/news.store';
import { MarketNewsItem } from '../../core/models/news.model';
import { NewsTileComponent } from './news-tile.component';
import { NewsDetailModalComponent } from './news-detail.modal';

@Component({
  selector: 'hf-news-page',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    AppShellComponent,
    EmptyStateComponent,
    NewsTileComponent,
    NewsDetailModalComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'News'}]">
      <div class="news-page">
        <header class="page-head">
          <div>
            <div class="eyebrow">Market</div>
            <h1 class="mt-1.5">News</h1>
            <p class="subtitle">{{ rankingLine() }}</p>
          </div>
          <div class="head-right">
            <ng-container *ngIf="sentimentEnabled()">
              <div class="legend" aria-label="Sentiment legend">
                <span class="leg-item bullish"><span class="leg-dot"></span>Bullish</span>
                <span class="leg-item bearish"><span class="leg-dot"></span>Bearish</span>
                <span class="leg-item neutral"><span class="leg-dot"></span>Neutral</span>
              </div>
            </ng-container>
            <span class="updated mono" *ngIf="updatedAt()">Updated {{ updatedAt() }}</span>
            <button
              type="button"
              class="btn ghost"
              (click)="refresh()"
              [disabled]="store.refreshing()"
              data-test="news-refresh"
            >
              <ng-container *ngIf="!store.refreshing(); else loadingTxt">Refresh</ng-container>
              <ng-template #loadingTxt>Refreshing…</ng-template>
            </button>
          </div>
        </header>

        <p
          class="toast"
          role="status"
          aria-live="polite"
          *ngIf="store.toast() as t"
        >{{ t }}</p>

        <ng-container *ngIf="store.meta() as meta">
          <p class="warn" role="status" *ngIf="meta.sentiment_warning">
            {{ meta.sentiment_warning }}
          </p>
          <p class="warn subtle" *ngIf="meta.warnings.length">
            {{ providerWarningText(meta.warnings) }}
          </p>
        </ng-container>

        <ng-container *ngIf="store.loading() && !store.items().length; else loaded">
          <div class="grid skeletons">
            <div class="skel" *ngFor="let _ of [].constructor(8)"></div>
          </div>
        </ng-container>
        <ng-template #loaded>
          <ng-container *ngIf="store.items().length; else emptyTpl">
            <div class="grid">
              <hf-news-tile
                *ngFor="let item of store.items(); trackBy: trackById"
                [item]="item"
                [sentimentEnabled]="sentimentEnabled()"
                (open)="onOpen($event)"
              ></hf-news-tile>
            </div>
            <div class="more-row">
              <ng-container *ngIf="store.hasMore(); else noMore">
                <button
                  type="button"
                  class="btn ghost"
                  (click)="loadMore()"
                  [disabled]="store.loadingMore()"
                  data-test="news-load-more"
                >
                  <ng-container *ngIf="!store.loadingMore(); else loadingMoreTxt">
                    Load more news ({{ remainingCount() }} more)
                  </ng-container>
                  <ng-template #loadingMoreTxt>
                    <span class="dots" aria-hidden="true"><span></span><span></span><span></span></span>
                    <span aria-live="polite">{{ loadMoreStageLabel() }}</span>
                  </ng-template>
                </button>
              </ng-container>
              <ng-template #noMore>
                <span class="more-end mono">
                  Showing all {{ store.items().length }} stories ranked today.
                </span>
              </ng-template>
            </div>
          </ng-container>
          <ng-template #emptyTpl>
            <hf-empty-state
              [message]="emptyMessage()"
              [detail]="emptyDetail()"
            >
              <a
                *ngIf="store.meta()?.needs_keys"
                routerLink="/settings/models"
                class="btn primary mt-3"
              >Set your data keys</a>
            </hf-empty-state>
          </ng-template>
        </ng-template>
      </div>
      <hf-news-detail-modal
        *ngIf="selected() as sel"
        [item]="sel"
        (closed)="closeModal()"
      ></hf-news-detail-modal>
    </hf-app-shell>
  `,
  styles: [
    `
      .news-page {
        display: flex;
        flex-direction: column;
        gap: 16px;
        min-height: 60vh;
      }
      .page-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        flex-wrap: wrap;
      }
      .subtitle {
        margin-top: 4px;
        font-size: 12px;
        color: var(--text-3);
        max-width: 540px;
      }
      .head-right {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
      }
      .updated {
        font-size: 11px;
        color: var(--text-3);
      }
      .legend {
        display: inline-flex;
        gap: 10px;
        font-size: 11px;
        color: var(--text-2);
        align-items: center;
      }
      .leg-item {
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }
      .leg-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
      }
      .leg-item.bullish .leg-dot {
        background: var(--acc-long-fg, #16a34a);
      }
      .leg-item.bearish .leg-dot {
        background: var(--acc-short-fg, #dc2626);
      }
      .leg-item.neutral .leg-dot {
        background: var(--text-3);
      }
      .toast {
        margin: 0;
        padding: 8px 12px;
        background: var(--surface-2);
        border-left: 3px solid var(--text-2);
        border-radius: var(--r-6);
        font-size: 12.5px;
        color: var(--text);
      }
      .warn {
        margin: 0;
        padding: 8px 12px;
        background: rgba(220, 38, 38, 0.08);
        border-left: 3px solid var(--acc-short-fg, #dc2626);
        border-radius: var(--r-6);
        font-size: 12.5px;
        color: var(--text);
      }
      .warn.subtle {
        background: var(--surface-2);
        border-left-color: var(--border-2);
        color: var(--text-2);
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
      }
      .skel {
        height: 140px;
        background: var(--surface-2);
        border-radius: var(--r-6);
        opacity: 0.7;
        animation: pulse 1.4s ease-in-out infinite;
      }
      @keyframes pulse {
        0%, 100% { opacity: 0.4; }
        50% { opacity: 0.8; }
      }
      .more-row {
        display: flex;
        justify-content: center;
        padding: 12px 0 4px;
      }
      .more-end {
        font-size: 11px;
        color: var(--text-3);
      }
      .dots {
        display: inline-flex;
        gap: 3px;
        margin-right: 8px;
        vertical-align: middle;
      }
      .dots span {
        width: 4px;
        height: 4px;
        border-radius: 50%;
        background: currentColor;
        opacity: 0.35;
        animation: dot-bounce 1.1s ease-in-out infinite;
      }
      .dots span:nth-child(2) { animation-delay: 0.15s; }
      .dots span:nth-child(3) { animation-delay: 0.3s; }
      @keyframes dot-bounce {
        0%, 80%, 100% { opacity: 0.35; transform: translateY(0); }
        40% { opacity: 1; transform: translateY(-2px); }
      }
      @media (prefers-reduced-motion: reduce) {
        .dots span { animation: none; opacity: 0.7; }
      }
    `,
  ],
})
export class NewsPage implements OnInit {
  readonly store = inject(NewsStore);
  private readonly route = inject(ActivatedRoute);

  readonly selected = signal<MarketNewsItem | null>(null);
  readonly loadMoreStage = signal<'news' | 'sentiment'>('news');

  readonly sentimentEnabled = computed(() => this.store.sentimentEnabled());
  readonly loadMoreStageLabel = computed(() =>
    this.loadMoreStage() === 'sentiment'
      ? 'AI sentiment analysis…'
      : 'Loading news…',
  );
  readonly updatedAt = computed(() => {
    const meta = this.store.meta();
    if (!meta) return '';
    const t = new Date(meta.generated_at).getTime();
    if (Number.isNaN(t)) return '';
    const diff = Math.max(0, Math.floor((Date.now() - t) / 60000));
    if (diff < 1) return 'just now';
    if (diff < 60) return `${diff}m ago`;
    const hours = Math.floor(diff / 60);
    return `${hours}h ago`;
  });
  readonly rankingLine = computed(() =>
    this.store.meta()?.ranking_basis ||
    'Ranked by recency, breadth of coverage & source weight.',
  );
  readonly remainingCount = computed(() =>
    Math.max(
      0,
      (this.store.totalAvailable() || 0) - this.store.items().length,
    ),
  );

  constructor() {
    // When the items list lands or grows, try to honor a ?article= deep link.
    effect(() => {
      const articleId = Number(this.route.snapshot.queryParamMap.get('article'));
      if (!articleId) return;
      // Only set the modal once; subsequent appends shouldn't re-open it.
      // Read `selected` untracked so closing the modal doesn't retrigger this
      // effect and re-open it from the still-present ?article= query param.
      if (untracked(() => this.selected())) return;
      const found = this.store.itemById(articleId);
      if (found) this.selected.set(found);
    });

    // Stage the load-more label: "Loading news…" first, then flip to
    // "AI sentiment analysis…" since the backend runs sentiment synchronously
    // on each page request (see MarketNewsFeedView).
    effect((onCleanup) => {
      const loading = this.store.loadingMore();
      if (!loading) return;
      this.loadMoreStage.set('news');
      if (!untracked(() => this.sentimentEnabled())) return;
      const t = setTimeout(() => this.loadMoreStage.set('sentiment'), 700);
      onCleanup(() => clearTimeout(t));
    });
  }

  ngOnInit(): void {
    this.store.loadPreferences().subscribe();
    this.store.loadFeed().subscribe();
  }

  refresh(): void {
    this.store.refresh().subscribe();
  }

  loadMore(): void {
    this.store.loadMore().subscribe();
  }

  onOpen(item: MarketNewsItem): void {
    this.selected.set(item);
  }

  closeModal(): void {
    this.selected.set(null);
  }

  emptyMessage(): string {
    const meta = this.store.meta();
    if (meta?.needs_keys) return 'No news yet — set your FMP or Tiingo key';
    return 'No news available right now';
  }
  emptyDetail(): string | undefined {
    const meta = this.store.meta();
    if (meta?.needs_keys) {
      return 'The market-news page needs an FMP or Tiingo key to fetch headlines.';
    }
    return 'Try the Refresh button or check back in a few minutes.';
  }

  providerWarningText(warnings: string[]): string {
    const names: string[] = [];
    for (const w of warnings) {
      const colon = w.indexOf(':');
      if (colon > 0) {
        names.push(w.slice(0, colon).trim());
      }
    }
    if (!names.length) return '';
    if (names.length === 1) return `${capitalize(names[0])} unavailable — showing other providers only.`;
    return `${names.map(capitalize).join(' & ')} unavailable — feed may be incomplete.`;
  }

  trackById(_i: number, it: MarketNewsItem): number {
    return it.id;
  }
}

function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}
