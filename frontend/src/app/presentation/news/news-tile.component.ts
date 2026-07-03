import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';

import { MarketNewsItem, Sentiment } from '../../core/models/news.model';
import { languageName } from './language-name';

/**
 * hf-news-tile — one ranked-cluster representative.
 *
 * The whole tile is a button (click + Enter/Space both emit `(open)`).
 * The sentiment accent (left border + dot) is absent when sentiment is
 * off or the row is unscored — colour is never the sole signal, so the
 * sentiment-label `pill` and `aria-label` always carry the word too.
 */
@Component({
  selector: 'hf-news-tile',
  standalone: true,
  imports: [],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      class="tile card"
      [class.sentiment-bullish]="showSentiment && item.sentiment === 'bullish'"
      [class.sentiment-bearish]="showSentiment && item.sentiment === 'bearish'"
      [class.sentiment-neutral]="showSentiment && item.sentiment === 'neutral'"
      (click)="open.emit(item)"
      [attr.aria-label]="ariaLabel()"
    >
      <header class="hd">
        @if (showSentiment && item.sentiment) {
          <span class="dot" [attr.data-sent]="item.sentiment" aria-hidden="true"></span>
        }
        <h3 class="headline">{{ item.headline }}</h3>
      </header>
      @if (item.summary) {
        <p class="summary">{{ item.summary }}</p>
      }
      <footer class="ft">
        <span class="src">{{ item.source || item.provider }}</span>
        <span class="sep" aria-hidden="true">•</span>
        <span class="time mono">{{ relativeTime }}</span>
        @if (showSentiment && item.sentiment) {
          <span class="sep" aria-hidden="true">•</span>
          <span class="pill sentiment" [attr.data-sent]="item.sentiment">{{ item.sentiment }}</span>
        }
        @if (item.translated_from) {
          <span class="sep" aria-hidden="true">•</span>
          <span class="pill xlate" [attr.aria-label]="'auto-translated from ' + translatedLabel"
            >Auto-translated from {{ translatedLabel }}</span
          >
        }
        @if (item.symbols?.length) {
          <span class="sep" aria-hidden="true">•</span>
          <span class="syms mono">{{ item.symbols.slice(0, 3).join(' ') }}</span>
        }
      </footer>
    </button>
  `,
  styles: [
    `
      :host {
        display: block;
        height: 100%;
      }
      .tile {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 14px 16px;
        text-align: left;
        width: 100%;
        height: 100%;
        background: var(--surface);
        border: 1px solid var(--border-2);
        border-left: 3px solid var(--border-2);
        color: var(--text);
        cursor: pointer;
        transition:
          border-color 80ms ease,
          transform 80ms ease,
          box-shadow 80ms ease;
      }
      .tile:hover {
        border-color: var(--border-2);
        box-shadow: var(--shadow-1);
      }
      .tile:focus-visible {
        outline: none;
        box-shadow: var(--focus-ring);
      }
      .sentiment-bullish {
        border-left-color: var(--acc-long-fg, #16a34a);
      }
      .sentiment-bearish {
        border-left-color: var(--acc-short-fg, #dc2626);
      }
      .sentiment-neutral {
        border-left-color: var(--text-3);
      }
      .hd {
        display: flex;
        align-items: flex-start;
        gap: 8px;
      }
      .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-top: 6px;
        background: var(--text-3);
        flex-shrink: 0;
      }
      .dot[data-sent='bullish'] {
        background: var(--acc-long-fg, #16a34a);
      }
      .dot[data-sent='bearish'] {
        background: var(--acc-short-fg, #dc2626);
      }
      .dot[data-sent='neutral'] {
        background: var(--text-3);
      }
      .headline {
        margin: 0;
        font-size: 14px;
        font-weight: 500;
        line-height: 1.35;
        color: var(--text);
        display: -webkit-box;
        -webkit-line-clamp: 2;
        line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .summary {
        margin: 0;
        font-size: 12.5px;
        line-height: 1.4;
        color: var(--text-2);
        display: -webkit-box;
        -webkit-line-clamp: 3;
        line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .ft {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        font-size: 11px;
        color: var(--text-3);
        margin-top: auto;
      }
      .sep {
        opacity: 0.6;
      }
      .pill.sentiment {
        text-transform: capitalize;
        font-size: 10.5px;
        padding: 1px 7px;
        border-radius: 999px;
      }
      .pill.sentiment[data-sent='bullish'] {
        background: rgba(22, 163, 74, 0.16);
        color: var(--acc-long-fg, #16a34a);
      }
      .pill.sentiment[data-sent='bearish'] {
        background: rgba(220, 38, 38, 0.16);
        color: var(--acc-short-fg, #dc2626);
      }
      .pill.sentiment[data-sent='neutral'] {
        background: var(--surface-2);
        color: var(--text-3);
      }
      .syms {
        font-size: 10.5px;
        color: var(--text-2);
      }
      .pill.xlate {
        font-size: 10.5px;
        padding: 1px 7px;
        border-radius: 999px;
        background: var(--surface-2);
        color: var(--text-2);
      }
    `,
  ],
})
export class NewsTileComponent {
  @Input({ required: true }) item!: MarketNewsItem;
  @Input() sentimentEnabled = true;
  @Output() open = new EventEmitter<MarketNewsItem>();

  get showSentiment(): boolean {
    return this.sentimentEnabled;
  }

  get translatedLabel(): string {
    return languageName(this.item.translated_from);
  }

  get relativeTime(): string {
    const when = new Date(this.item.published_at).getTime();
    const diffMs = Date.now() - when;
    const minutes = Math.max(0, Math.floor(diffMs / 60000));
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }

  ariaLabel(): string {
    const sent = this.showSentiment && this.item.sentiment ? ` — ${this.item.sentiment}` : '';
    return `${this.item.headline}${sent}`;
  }
}
