import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnDestroy,
  Output,
  ViewChild,
  computed,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { MarketNewsItem } from '../../core/models/news.model';

/**
 * hf-news-chyron — continuous right-to-left scrolling banner.
 *
 * Accessibility (load-bearing — WCAG 2.2.2):
 *  - visible pause/play button at the left of the bar
 *  - pause on hover and on keyboard focus within the bar
 *  - `prefers-reduced-motion`: marquee disabled; static horizontal strip
 *    of the same headlines renders instead
 *  - the animated, duplicated track is `aria-hidden`; an accompanying
 *    visually-hidden non-animated list carries semantics for assistive
 *    technology
 */
@Component({
  selector: 'hf-news-chyron',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="chyron"
      [class.paused]="paused() || prefersReducedMotion"
      [class.reduced-motion]="prefersReducedMotion"
      (mouseenter)="onEnter()"
      (mouseleave)="onLeave()"
      (focusin)="onFocusIn()"
      (focusout)="onFocusOut()"
    >
      <div class="cap" aria-hidden="true">MARKET NEWS</div>
      <button
        type="button"
        class="pause-btn"
        (click)="togglePaused()"
        [attr.aria-label]="paused() ? 'Resume scrolling news banner' : 'Pause scrolling news banner'"
        [attr.aria-pressed]="paused()"
      >
        <svg
          *ngIf="!paused()"
          width="14"
          height="14"
          aria-hidden="true"
        >
          <use href="/icons.svg#i-pause" />
        </svg>
        <svg
          *ngIf="paused()"
          width="14"
          height="14"
          aria-hidden="true"
        >
          <use href="/icons.svg#i-play" />
        </svg>
      </button>
      <div class="track-wrap" aria-hidden="true">
        <div
          #track
          class="track"
          [style.animationDuration.s]="animationSeconds()"
        >
          <ng-container *ngFor="let copy of copies; let copyIdx = index">
            <ng-container *ngFor="let item of items; let i = index">
              <button
                type="button"
                class="hl"
                tabindex="-1"
                (click)="open.emit(item)"
                [attr.data-key]="copyIdx + '-' + i"
              >
                <span
                  class="dot"
                  *ngIf="item.sentiment"
                  [attr.data-sent]="item.sentiment"
                  aria-hidden="true"
                ></span>
                <span class="text">{{ item.headline }}</span>
                <span class="sep" aria-hidden="true">•</span>
              </button>
            </ng-container>
          </ng-container>
        </div>
      </div>
      <ul class="sr-only-list">
        <li *ngFor="let item of items; let i = index">
          <button
            type="button"
            class="sr-hl"
            (click)="open.emit(item)"
            (focus)="onFocusIn()"
            (blur)="onFocusOut()"
          >
            <span class="sr-only">{{ srLabel(item) }}</span>
            <span class="sr-only" *ngIf="item.sentiment">sentiment {{ item.sentiment }}</span>
          </button>
        </li>
      </ul>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
        width: 100%;
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
      }
      .chyron {
        position: relative;
        display: flex;
        align-items: center;
        gap: 0;
        background: linear-gradient(
          to right,
          var(--surface-2) 0,
          var(--surface) 100%
        );
        border-top: 1px solid var(--border-2);
        border-bottom: 1px solid var(--border-2);
        height: 32px;
        width: 100%;
        max-width: 100%;
        min-width: 0;
        overflow: hidden;
        box-sizing: border-box;
      }
      .cap {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.16em;
        color: var(--text-2);
        background: var(--surface-2);
        padding: 0 12px;
        height: 100%;
        display: flex;
        align-items: center;
        border-right: 1px solid var(--border-2);
        flex-shrink: 0;
      }
      .pause-btn {
        flex-shrink: 0;
        width: 32px;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--surface-2);
        border: none;
        border-right: 1px solid var(--border-2);
        color: var(--text-2);
        cursor: pointer;
      }
      .pause-btn:hover {
        color: var(--text);
      }
      .pause-btn:focus-visible {
        outline: none;
        box-shadow: inset 0 0 0 2px var(--focus-ring-color, #4f8cff);
      }
      .track-wrap {
        flex: 1 1 0;
        min-width: 0;
        height: 100%;
        overflow: hidden;
        position: relative;
      }
      .track {
        display: flex;
        align-items: center;
        gap: 16px;
        white-space: nowrap;
        will-change: transform;
        height: 100%;
        animation-name: chyron-scroll;
        animation-timing-function: linear;
        animation-iteration-count: infinite;
        animation-duration: 60s;
      }
      .reduced-motion .track {
        animation: none !important;
        overflow-x: auto;
      }
      .paused .track {
        animation-play-state: paused;
      }
      .hl {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: transparent;
        border: none;
        font-size: 12.5px;
        color: var(--text);
        cursor: pointer;
        padding: 0;
        flex-shrink: 0;
      }
      .hl:hover .text {
        text-decoration: underline;
      }
      .text {
        flex-shrink: 0;
      }
      .dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
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
      .sep {
        color: var(--text-3);
        opacity: 0.6;
      }
      .sr-only-list {
        position: absolute;
        left: -10000px;
        top: auto;
        width: 1px;
        height: 1px;
        overflow: hidden;
      }
      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
      }
      @keyframes chyron-scroll {
        from {
          transform: translateX(0);
        }
        to {
          transform: translateX(-50%);
        }
      }
    `,
  ],
})
export class NewsChyronComponent implements AfterViewInit, OnDestroy {
  private _items: MarketNewsItem[] = [];

  @Input() set items(value: MarketNewsItem[]) {
    this._items = value || [];
  }
  get items(): MarketNewsItem[] {
    return this._items;
  }

  @Output() open = new EventEmitter<MarketNewsItem>();

  @ViewChild('track', { static: false }) trackRef?: ElementRef<HTMLDivElement>;

  // The track is duplicated and the -50% keyframe ensures the seam is invisible.
  copies = [0, 1];

  readonly paused = signal(false);
  prefersReducedMotion = false;

  private hoverCount = 0;
  private focusCount = 0;
  private motionMq?: MediaQueryList;

  ngAfterViewInit(): void {
    if (typeof window !== 'undefined' && 'matchMedia' in window) {
      this.motionMq = window.matchMedia('(prefers-reduced-motion: reduce)');
      this.prefersReducedMotion = this.motionMq.matches;
      // Listen for runtime changes (browser toggling settings).
      this.motionMq.addEventListener?.('change', this.onMotionChange);
    }
  }

  ngOnDestroy(): void {
    this.motionMq?.removeEventListener?.('change', this.onMotionChange);
  }

  private onMotionChange = (e: MediaQueryListEvent): void => {
    this.prefersReducedMotion = e.matches;
  };

  togglePaused(): void {
    this.paused.update((p) => !p);
  }

  onEnter(): void {
    this.hoverCount++;
    this.paused.set(true);
  }
  onLeave(): void {
    this.hoverCount = Math.max(0, this.hoverCount - 1);
    if (this.hoverCount === 0 && this.focusCount === 0) {
      this.paused.set(false);
    }
  }
  onFocusIn(): void {
    this.focusCount++;
    this.paused.set(true);
  }
  onFocusOut(): void {
    this.focusCount = Math.max(0, this.focusCount - 1);
    if (this.hoverCount === 0 && this.focusCount === 0) {
      this.paused.set(false);
    }
  }

  // Approximate ~120 px/s scroll: more items → longer duration so the
  // perceived speed stays comfortable.
  animationSeconds = computed(() => {
    const n = this._items.length || 1;
    return Math.max(30, n * 5);
  });

  srLabel(item: MarketNewsItem): string {
    return `${item.headline}. Source ${item.source || item.provider}.`;
  }
}
