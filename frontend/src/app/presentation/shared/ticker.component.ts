import { ChangeDetectionStrategy, Component, ElementRef, HostListener, Input, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

import { TickerHistoryStore } from '../../abstraction/ticker-history.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { TickerPopoverComponent } from './ticker-popover.component';

let _popoverIdSeq = 0;

/**
 * hf-ticker — renders a ticker symbol with an accessible hover/focus
 * popover that lazy-loads the company name, trend sparkline, and the
 * latest market cap / P/E / EPS. WS-2 / R4.
 *
 * The popover opens on hover OR keyboard focus, can be dismissed with
 * Escape or by moving focus elsewhere, and is `aria-describedby` the
 * inline span so screen readers announce its content. The popover lives
 * inline in the document (no portal) — viewport-clamped via right/left
 * fallback when the trigger is near the right edge.
 *
 * `disablePopover` short-circuits the behaviour for cases where the
 * popover would be noise (e.g. the parent of a Name column cell — the
 * column already shows the name).
 */
@Component({
  selector: 'hf-ticker',
  standalone: true,
  imports: [CommonModule, TickerPopoverComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="hf-tk-wrap" #wrap>
      <span
        class="hf-tk-sym mono"
        tabindex="0"
        [attr.role]="disablePopover ? null : 'button'"
        [attr.aria-describedby]="open() ? popoverId : null"
        (mouseenter)="onEnter()"
        (mouseleave)="onLeave()"
        (focus)="onFocus()"
        (blur)="onBlur()"
      >{{ ticker }}</span>
      @if (open() && !disablePopover) {
        <span class="hf-tk-pop" [class.right]="alignRight()" aria-hidden="false">
          <hf-ticker-popover [ticker]="ticker" [id]="popoverId"></hf-ticker-popover>
        </span>
      }
    </span>
  `,
  styles: [
    `
      .hf-tk-wrap {
        display: inline-block;
        position: relative;
      }
      .hf-tk-sym {
        font-family: var(--font-mono);
        font-variant-numeric: tabular-nums;
        letter-spacing: var(--tracking-mono);
        cursor: default;
        outline: none;
        border-radius: 3px;
        /* WS-3.6: WCAG 2.2 §2.5.8 — give the focusable ticker chip a 24×24
           target footprint (vertical padding + min-height) without changing
           the visible glyph baseline. */
        display: inline-block;
        min-height: 24px;
        line-height: 24px;
        padding: 0 2px;
      }
      .hf-tk-sym:focus-visible {
        box-shadow: var(--focus-ring);
      }
      .hf-tk-pop {
        position: absolute;
        top: 100%;
        left: 0;
        margin-top: 6px;
        z-index: var(--z-tooltip);
        pointer-events: auto;
        animation: hf-tk-pop-in 120ms var(--ease-out-ui);
      }
      .hf-tk-pop.right {
        left: auto;
        right: 0;
      }
      @keyframes hf-tk-pop-in {
        from { opacity: 0; transform: translateY(-4px); }
        to { opacity: 1; transform: translateY(0); }
      }
    `,
  ],
})
export class TickerComponent {
  @Input() set ticker(v: string) {
    this._ticker = (v || '').toUpperCase();
  }
  get ticker(): string { return this._ticker; }
  private _ticker = '';

  @Input() disablePopover = false;

  @ViewChild('wrap', { static: true }) wrapRef?: ElementRef<HTMLElement>;

  private readonly profileStore = inject(TickerProfileStore);
  private readonly historyStore = inject(TickerHistoryStore);

  readonly popoverId = `hf-tk-pop-${++_popoverIdSeq}`;
  readonly open = signal(false);
  readonly alignRight = signal(false);
  private hovering = false;
  private focused = false;
  private hideTimer: number | null = null;
  private fetchedOnce = false;

  private show(): void {
    if (this.disablePopover || !this.ticker) return;
    if (this.hideTimer) { window.clearTimeout(this.hideTimer); this.hideTimer = null; }
    if (!this.open()) {
      // Decide alignment before opening to avoid a flicker.
      const el = this.wrapRef?.nativeElement;
      if (el) {
        const rect = el.getBoundingClientRect();
        const popWidth = 280;
        const overflowRight = rect.left + popWidth > window.innerWidth - 8;
        this.alignRight.set(overflowRight);
      }
      this.open.set(true);
      if (!this.fetchedOnce) {
        this.fetchedOnce = true;
        this.profileStore.fetchProfile(this.ticker).subscribe();
        this.historyStore.fetch(this.ticker, 60).subscribe();
      }
    }
  }

  private maybeHide(): void {
    if (this.hovering || this.focused) return;
    if (this.hideTimer) window.clearTimeout(this.hideTimer);
    this.hideTimer = window.setTimeout(() => this.open.set(false), 80);
  }

  onEnter(): void { this.hovering = true; this.show(); }
  onLeave(): void { this.hovering = false; this.maybeHide(); }
  onFocus(): void { this.focused = true; this.show(); }
  onBlur(): void { this.focused = false; this.maybeHide(); }

  @HostListener('keydown.escape')
  onEscape(): void {
    if (this.open()) {
      this.open.set(false);
      this.hovering = false;
      this.focused = false;
    }
  }
}
