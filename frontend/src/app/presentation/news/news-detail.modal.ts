import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';

import { ModalComponent } from '../shared/modal.component';
import { TickerComponent } from '../shared/ticker.component';
import { MarketNewsItem } from '../../core/models/news.model';

/**
 * hf-news-detail-modal — full detail of one market-news story.
 *
 * Built on hf-modal (focus trap, Esc, focus return). Renders the image (if
 * any), the full summary, the sentiment block (when scored), `hf-ticker`
 * chips for each tagged symbol with an "Analyze in New Run" action, and an
 * external "Read full article" link.
 */
@Component({
  selector: 'hf-news-detail-modal',
  standalone: true,
  imports: [CommonModule, ModalComponent, TickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-modal (closed)="closed.emit()">
      <div class="card hf-news-detail">
        <div class="card-hd">
          <span class="title" [id]="'news-modal-title'">News article</span>
          <button type="button" class="icon-btn close" (click)="closed.emit()"
                  aria-label="Close">
            <svg width="14" height="14" aria-hidden="true">
              <use href="/icons.svg#i-x" />
            </svg>
          </button>
        </div>
        <div class="card-bd">
          <img
            *ngIf="item.image_url"
            [src]="item.image_url"
            class="img"
            alt=""
            loading="lazy"
            (error)="imgFailed = true"
            [hidden]="imgFailed"
          />
          <h2 class="headline">{{ item.headline }}</h2>
          <div class="meta">
            <span class="src">{{ item.source || item.provider }}</span>
            <span class="sep" aria-hidden="true">•</span>
            <span class="time mono">{{ formattedTime }}</span>
            <span *ngIf="item.cluster_size > 1" class="pill ml-2">
              {{ item.cluster_size }} sources
            </span>
          </div>
          <p class="summary" *ngIf="item.summary">{{ item.summary }}</p>
          <p class="summary muted" *ngIf="!item.summary">
            No preview text from the publisher; open the article for the full
            story.
          </p>

          <section *ngIf="item.sentiment" class="sentiment-block" [attr.data-sent]="item.sentiment">
            <div class="row">
              <span class="label">Sentiment:</span>
              <span class="pill sentiment" [attr.data-sent]="item.sentiment">
                {{ item.sentiment }}
              </span>
              <span class="score mono" *ngIf="item.sentiment_score !== null">
                {{ formatScore(item.sentiment_score) }}
              </span>
            </div>
            <p class="rationale" *ngIf="item.sentiment_rationale">
              {{ item.sentiment_rationale }}
            </p>
          </section>

          <section *ngIf="item.symbols?.length" class="tickers">
            <div class="label">Tagged tickers</div>
            <ul class="chip-list">
              <li *ngFor="let sym of item.symbols.slice(0, 8)">
                <span class="chip">
                  <hf-ticker [ticker]="sym"></hf-ticker>
                  <button type="button" class="btn ghost xs analyze"
                          (click)="analyzeInRun(sym)"
                          [attr.aria-label]="'Analyze ' + sym + ' in a new Run'">
                    Analyze
                  </button>
                </span>
              </li>
            </ul>
          </section>

          <section *ngIf="item.tags?.length" class="tags">
            <div class="label">Topics</div>
            <ul class="tag-list">
              <li *ngFor="let t of item.tags.slice(0, 8)" class="pill subtle">{{ t }}</li>
            </ul>
          </section>
        </div>
        <div class="card-ft">
          <a class="btn primary" [href]="item.url" target="_blank" rel="noopener noreferrer">
            Read full article
            <svg width="12" height="12" aria-hidden="true" class="ml-1">
              <use href="/icons.svg#i-ext" />
            </svg>
          </a>
          <button type="button" class="btn ghost" (click)="closed.emit()">Close</button>
        </div>
      </div>
    </hf-modal>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .hf-news-detail {
        max-width: 720px;
        width: min(720px, 92vw);
        max-height: 90vh;
        overflow: hidden;
        display: flex;
        flex-direction: column;
      }
      .card-bd {
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .img {
        width: 100%;
        max-height: 280px;
        object-fit: cover;
        border-radius: var(--r-6);
      }
      .headline {
        margin: 0;
        font-size: 18px;
        font-weight: 600;
        line-height: 1.3;
        color: var(--text);
      }
      .meta {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
        font-size: 12px;
        color: var(--text-3);
      }
      .summary {
        margin: 0;
        font-size: 13.5px;
        line-height: 1.5;
        color: var(--text-2);
      }
      .summary.muted {
        color: var(--text-3);
        font-style: italic;
      }
      .sentiment-block {
        padding: 10px 12px;
        background: var(--surface-2);
        border-radius: var(--r-6);
        border-left: 3px solid var(--border-2);
      }
      .sentiment-block[data-sent='bullish'] {
        border-left-color: var(--acc-long-fg, #16a34a);
      }
      .sentiment-block[data-sent='bearish'] {
        border-left-color: var(--acc-short-fg, #dc2626);
      }
      .sentiment-block .row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
      }
      .sentiment-block .label {
        font-size: 12px;
        color: var(--text-3);
      }
      .pill.sentiment {
        text-transform: capitalize;
        font-size: 11px;
        padding: 2px 8px;
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
        background: var(--surface);
        color: var(--text-3);
      }
      .score {
        font-size: 12px;
        color: var(--text-3);
      }
      .rationale {
        margin: 4px 0 0;
        font-size: 12.5px;
        color: var(--text-2);
      }
      .label {
        font-size: 11px;
        color: var(--text-3);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 6px;
      }
      .chip-list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 6px;
        background: var(--surface-2);
        border-radius: var(--r-6);
      }
      .analyze {
        font-size: 11px;
        padding: 1px 6px;
      }
      .tag-list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .pill.subtle {
        font-size: 10.5px;
        padding: 1px 7px;
        background: var(--surface-2);
        color: var(--text-3);
        border-radius: 999px;
      }
      .card-ft {
        display: flex;
        gap: 8px;
        justify-content: flex-end;
        padding: 12px 16px;
        border-top: 1px solid var(--border-2);
      }
      .close {
        background: transparent;
        border: none;
        cursor: pointer;
        padding: 4px 6px;
        color: var(--text-2);
      }
      .close:hover {
        color: var(--text);
      }
    `,
  ],
})
export class NewsDetailModalComponent {
  @Input({ required: true }) item!: MarketNewsItem;
  @Output() closed = new EventEmitter<void>();

  imgFailed = false;

  constructor(private readonly router: Router) {}

  analyzeInRun(symbol: string): void {
    this.router.navigate(['/runs/new'], {
      queryParams: { ticker: symbol.toUpperCase() },
    });
  }

  get formattedTime(): string {
    const d = new Date(this.item.published_at);
    if (Number.isNaN(d.getTime())) return this.item.published_at;
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  formatScore(score: number): string {
    if (score == null) return '';
    const sign = score >= 0 ? '+' : '';
    return `${sign}${score.toFixed(2)}`;
  }
}
