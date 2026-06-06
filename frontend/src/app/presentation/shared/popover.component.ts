import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  Injector,
  OnDestroy,
  ViewChild,
  afterNextRender,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';

export type PopoverPlacement = 'top' | 'bottom' | 'left' | 'right' | 'auto';
export type PopoverAlign = 'start' | 'end' | 'center' | 'auto';
export type PopoverRole = 'tooltip' | 'dialog';

let _popoverIdSeq = 0;

/**
 * hf-popover — shared accessible floating-surface primitive. WS-4.3 / DC-19.
 *
 * Owns the popover surface (border, shadow, radius, z-index), viewport-aware
 * placement (top/bottom/left/right with axis flip on overflow), ARIA role,
 * lazy mounting (content renders only while open), and Escape dismissal.
 *
 * Consumer wraps the trigger and pop in a `position: relative` element and
 * wires the events:
 *
 *   <span class="wrap" style="position:relative">
 *     <button
 *       (mouseenter)="pop.show()" (mouseleave)="pop.maybeHide()"
 *       (focus)="pop.show()"      (blur)="pop.maybeHide()"
 *       [attr.aria-describedby]="pop.open() ? pop.popoverId : null">
 *       Trigger
 *     </button>
 *     <hf-popover #pop placement="top" align="start">
 *       Popover content
 *     </hf-popover>
 *   </span>
 *
 * The component is intentionally headless on the trigger side — consumers
 * keep full control of their trigger DOM and styling.
 */
@Component({
  selector: 'hf-popover',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (open()) {
      <span
        class="hf-pop"
        #pop
        [id]="popoverId"
        [attr.role]="role()"
        [class.fixed]="strategy() === 'fixed'"
        [class.placement-top]="strategy() !== 'fixed' && effectivePlacement() === 'top'"
        [class.placement-bottom]="strategy() !== 'fixed' && effectivePlacement() === 'bottom'"
        [class.placement-left]="strategy() !== 'fixed' && effectivePlacement() === 'left'"
        [class.placement-right]="strategy() !== 'fixed' && effectivePlacement() === 'right'"
        [class.align-start]="strategy() !== 'fixed' && effectiveAlign() === 'start'"
        [class.align-end]="strategy() !== 'fixed' && effectiveAlign() === 'end'"
        [class.align-center]="strategy() !== 'fixed' && effectiveAlign() === 'center'"
        [class.surface-bare]="surface() === 'bare'"
        [class.size-compact]="size() === 'compact'"
        [style.top.px]="strategy() === 'fixed' ? fixedTop() : null"
        [style.left.px]="strategy() === 'fixed' ? fixedLeft() : null"
        (mouseenter)="onPopEnter()"
        (mouseleave)="onPopLeave()"
      >
        <ng-content></ng-content>
      </span>
    }
  `,
  styles: [
    `
      :host {
        position: static;
        display: contents;
      }
      .hf-pop {
        position: absolute;
        z-index: var(--z-tooltip);
        min-width: 180px;
        max-width: min(320px, 90vw);
        padding: 10px 12px;
        background: var(--surface-3);
        color: var(--text);
        border: 1px solid var(--border-2);
        border-radius: var(--r-6);
        box-shadow: var(--shadow-2);
        font-size: var(--fs-12);
        line-height: var(--lh-13);
        font-family: var(--font-sans);
        font-weight: 400;
        letter-spacing: normal;
        text-transform: none;
        text-align: left;
        white-space: pre-line;
        animation: hf-pop-fade-in 120ms var(--ease-out-ui);
      }
      /* Fixed strategy: position against the viewport (JS sets top/left) so the
         popover escapes any ancestor overflow clip. Placement/align classes are
         not applied in this mode, so no calc-offsets or transforms interfere. */
      .hf-pop.fixed { position: fixed; }
      /* The 'bare' surface lets rich content (ticker popover, glossary card)
         bring its own padding/background — hf-popover then only provides
         positioning and z-index/role wiring. */
      .hf-pop.surface-bare {
        background: transparent;
        border: 0;
        padding: 0;
        box-shadow: none;
        min-width: 0;
        max-width: none;
        white-space: normal;
      }
      /* The 'compact' size targets one-line label tooltips (sidebar nav,
         icon-only buttons). Shrinks padding + drops min-width + locks to
         nowrap so the surface hugs the label. */
      .hf-pop.size-compact {
        min-width: 0;
        padding: 4px 8px;
        white-space: nowrap;
        font-size: var(--fs-12);
        border-radius: var(--r-4);
      }
      /* Vertical placements (top/bottom) — alignment runs horizontally. */
      .hf-pop.placement-top {
        bottom: calc(100% + 8px);
        top: auto;
      }
      .hf-pop.placement-bottom {
        top: calc(100% + 8px);
        bottom: auto;
      }
      .hf-pop.placement-top.align-start,
      .hf-pop.placement-bottom.align-start {
        left: 0;
        right: auto;
      }
      .hf-pop.placement-top.align-end,
      .hf-pop.placement-bottom.align-end {
        left: auto;
        right: 0;
      }
      .hf-pop.placement-top.align-center,
      .hf-pop.placement-bottom.align-center {
        left: 50%;
        right: auto;
        transform: translateX(-50%);
      }
      /* Horizontal placements (left/right) — alignment runs vertically. */
      .hf-pop.placement-left {
        right: calc(100% + 8px);
        left: auto;
      }
      .hf-pop.placement-right {
        left: calc(100% + 8px);
        right: auto;
      }
      .hf-pop.placement-left.align-start,
      .hf-pop.placement-right.align-start {
        top: 0;
        bottom: auto;
      }
      .hf-pop.placement-left.align-end,
      .hf-pop.placement-right.align-end {
        top: auto;
        bottom: 0;
      }
      .hf-pop.placement-left.align-center,
      .hf-pop.placement-right.align-center {
        top: 50%;
        bottom: auto;
        transform: translateY(-50%);
      }
      @keyframes hf-pop-fade-in {
        from {
          opacity: 0;
        }
        to {
          opacity: 1;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .hf-pop {
          animation: none;
        }
      }
    `,
  ],
})
export class PopoverComponent implements OnDestroy {
  /** Preferred placement; all values flip on the same axis if they don't fit. */
  placement = input<PopoverPlacement>('top');
  /**
   * Cross-axis alignment.
   * - Vertical placements (top/bottom): `start` = left, `end` = right.
   * - Horizontal placements (left/right): `start` = top, `end` = bottom.
   * All values fall back to the opposite end on overflow.
   */
  align = input<PopoverAlign>('start');
  /** ARIA role applied to the popover surface. */
  role = input<PopoverRole>('tooltip');
  /** Visual surface — `card` (default) draws the popover chrome; `bare` is positioning-only. */
  surface = input<'card' | 'bare'>('card');
  /** Size variant — `compact` is a single-line label tooltip (no min-width, smaller padding). */
  size = input<'default' | 'compact'>('default');
  /** Positioning strategy. `fixed` positions against the viewport (JS-set top/left)
   *  so the popover escapes ancestor overflow clipping — e.g. the sidebar's
   *  `.nav-scroll` (overflow-x: hidden), which otherwise clips a right-placed tip. */
  strategy = input<'absolute' | 'fixed'>('absolute');
  /** Delay before hiding on hover-out, so the cursor can cross the gap into the popover. */
  hoverCloseDelay = input(80);
  /** Viewport edge margin used by the flip math. */
  edgeMargin = input(12);
  /** Override the element measured for flip decisions. Defaults to host parent. */
  anchor = input<HTMLElement | null>(null);
  /** When true, document-level click outside the trigger/popover closes it. */
  dismissOnOutsideClick = input(false);

  openChange = output<boolean>();

  @ViewChild('pop', { static: false }) popRef?: ElementRef<HTMLElement>;

  readonly popoverId = `hf-pop-${++_popoverIdSeq}`;
  readonly open = signal(false);
  readonly effectivePlacement = signal<'top' | 'bottom' | 'left' | 'right'>('top');
  readonly effectiveAlign = signal<'start' | 'end' | 'center'>('start');
  // Viewport coordinates for strategy="fixed" (null until measured / in absolute mode).
  readonly fixedTop = signal<number | null>(null);
  readonly fixedLeft = signal<number | null>(null);

  private readonly hostEl = inject(ElementRef<HTMLElement>);
  private readonly injector = inject(Injector);
  private hideTimer: number | null = null;
  private popHovered = false;

  constructor() {
    // P4 WS-DA-1: the ARIA role belongs only on the floating `.hf-pop` surface
    // (bound in the template). Consumers historically passed role="tooltip" on
    // the host, which Angular renders as a static attribute on the always-
    // present, empty host element — an unnamed `role="tooltip"` node that axe
    // flags (aria-tooltip-name). Strip it so it can never leak, regardless of
    // what call-sites pass.
    afterNextRender(
      () => this.hostEl.nativeElement.removeAttribute('role'),
      { injector: this.injector },
    );
  }

  show(): void {
    this.clearHideTimer();
    if (this.open()) return;
    this.open.set(true);
    this.openChange.emit(true);
    // Measure after Angular's next render so the popover element exists and
    // we mutate placement signals outside the active CD cycle (avoids
    // NG0100 under zoneless dev-mode).
    afterNextRender(
      () => (this.strategy() === 'fixed' ? this.measureFixed() : this.measureAndFlip()),
      { injector: this.injector },
    );
  }

  hide(): void {
    this.clearHideTimer();
    if (!this.open()) return;
    this.open.set(false);
    this.openChange.emit(false);
    this.popHovered = false;
  }

  maybeHide(): void {
    if (this.popHovered) return;
    this.clearHideTimer();
    this.hideTimer = window.setTimeout(() => {
      if (!this.popHovered && this.open()) {
        this.open.set(false);
        this.openChange.emit(false);
      }
    }, this.hoverCloseDelay());
  }

  toggle(): void {
    if (this.open()) this.hide();
    else this.show();
  }

  ngOnDestroy(): void {
    this.clearHideTimer();
  }

  onPopEnter(): void {
    this.popHovered = true;
    this.clearHideTimer();
  }

  onPopLeave(): void {
    this.popHovered = false;
    this.maybeHide();
  }

  @HostListener('document:keydown.escape')
  onDocEscape(): void {
    if (this.open()) this.hide();
  }

  @HostListener('document:click', ['$event'])
  onDocClick(ev: MouseEvent): void {
    if (!this.open() || !this.dismissOnOutsideClick()) return;
    const target = ev.target as Node | null;
    const anchorEl = this.getAnchor();
    const popEl = this.popRef?.nativeElement ?? null;
    if (!target) return;
    if (anchorEl?.contains(target)) return;
    if (popEl?.contains(target)) return;
    this.hide();
  }

  private clearHideTimer(): void {
    if (this.hideTimer !== null) {
      window.clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
  }

  private getAnchor(): HTMLElement | null {
    return this.anchor() ?? (this.hostEl.nativeElement.parentElement as HTMLElement | null);
  }

  /**
   * The rect the popover must stay inside: the viewport, intersected with
   * every scroll/clip ancestor (overflow != visible) between the anchor
   * and the document root. A popover inside an `overflow:auto`/`hidden`
   * container — e.g. a table wrapper — is visually clipped by that
   * container, so the flip math has to account for it, not just the
   * viewport. Returns the bare viewport when there is no clip ancestor
   * (the common case), so placement is unchanged for those popovers.
   */
  private clipBounds(anchorEl: HTMLElement): {
    top: number;
    left: number;
    right: number;
    bottom: number;
  } {
    let top = 0;
    let left = 0;
    let right = window.innerWidth;
    let bottom = window.innerHeight;
    let el: HTMLElement | null = anchorEl.parentElement;
    while (el && el !== document.body && el !== document.documentElement) {
      const cs = getComputedStyle(el);
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const r = el.getBoundingClientRect();
        top = Math.max(top, r.top);
        left = Math.max(left, r.left);
        right = Math.min(right, r.right);
        bottom = Math.min(bottom, r.bottom);
      }
      el = el.parentElement;
    }
    return { top, left, right, bottom };
  }

  private measureAndFlip(): void {
    const popEl = this.popRef?.nativeElement;
    const anchorEl = this.getAnchor();
    if (!popEl || !anchorEl) return;

    const desiredPlacement = this.placement();
    const desiredAlign = this.align();
    const margin = this.edgeMargin();

    const anchorRect = anchorEl.getBoundingClientRect();
    const popRect = popEl.getBoundingClientRect();
    // Stay inside the viewport *and* every scroll/clip ancestor (e.g. a
    // table wrapper with overflow:auto). Measuring against the viewport
    // alone lets the flip logic think a popover "fits below" when the
    // container actually clips it — the bottom-of-list bug. WS-4.3.
    const bounds = this.clipBounds(anchorEl);

    const fitsAbove = anchorRect.top - popRect.height - margin >= bounds.top;
    const fitsBelow = anchorRect.bottom + popRect.height + margin <= bounds.bottom;
    const fitsLeft = anchorRect.left - popRect.width - margin >= bounds.left;
    const fitsRight = anchorRect.right + popRect.width + margin <= bounds.right;

    let placement: 'top' | 'bottom' | 'left' | 'right';
    if (desiredPlacement === 'auto') {
      // Prefer above, then below, then right, then left.
      if (fitsAbove) placement = 'top';
      else if (fitsBelow) placement = 'bottom';
      else if (fitsRight) placement = 'right';
      else if (fitsLeft) placement = 'left';
      else placement = 'top';
    } else if (desiredPlacement === 'top') {
      placement = fitsAbove || !fitsBelow ? 'top' : 'bottom';
    } else if (desiredPlacement === 'bottom') {
      placement = fitsBelow || !fitsAbove ? 'bottom' : 'top';
    } else if (desiredPlacement === 'left') {
      placement = fitsLeft || !fitsRight ? 'left' : 'right';
    } else {
      placement = fitsRight || !fitsLeft ? 'right' : 'left';
    }
    this.effectivePlacement.set(placement);

    let align: 'start' | 'end' | 'center';
    if (desiredAlign === 'center') {
      align = 'center';
    } else if (placement === 'top' || placement === 'bottom') {
      // Horizontal alignment for vertical placements.
      const startOverflow = anchorRect.left + popRect.width + margin > bounds.right;
      const endOverflow = anchorRect.right - popRect.width - margin < bounds.left;
      if (desiredAlign === 'start' || desiredAlign === 'auto') {
        align = startOverflow && !endOverflow ? 'end' : 'start';
      } else {
        align = endOverflow && !startOverflow ? 'start' : 'end';
      }
    } else {
      // Vertical alignment for horizontal placements.
      const startOverflow = anchorRect.top + popRect.height + margin > bounds.bottom;
      const endOverflow = anchorRect.bottom - popRect.height - margin < bounds.top;
      if (desiredAlign === 'start' || desiredAlign === 'auto') {
        align = startOverflow && !endOverflow ? 'end' : 'start';
      } else {
        align = endOverflow && !startOverflow ? 'start' : 'end';
      }
    }
    this.effectiveAlign.set(align);
  }

  /**
   * Position the popover against the viewport for `strategy="fixed"`. Mirrors the
   * placement/align of the absolute path but sets explicit top/left, and flips
   * against the viewport — a fixed element escapes ancestor overflow clipping
   * (e.g. the sidebar's `.nav-scroll`), so the clip-ancestor math doesn't apply.
   */
  private measureFixed(): void {
    const popEl = this.popRef?.nativeElement;
    const anchorEl = this.getAnchor();
    if (!popEl || !anchorEl) return;

    const a = anchorEl.getBoundingClientRect();
    const p = popEl.getBoundingClientRect();
    const gap = 8;
    const margin = this.edgeMargin();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const fitsRight = a.right + gap + p.width + margin <= vw;
    const fitsLeft = a.left - gap - p.width - margin >= 0;
    const fitsAbove = a.top - gap - p.height - margin >= 0;
    const fitsBelow = a.bottom + gap + p.height + margin <= vh;

    const desired = this.placement();
    let place: 'top' | 'bottom' | 'left' | 'right';
    if (desired === 'right') place = fitsRight || !fitsLeft ? 'right' : 'left';
    else if (desired === 'left') place = fitsLeft || !fitsRight ? 'left' : 'right';
    else if (desired === 'bottom') place = fitsBelow || !fitsAbove ? 'bottom' : 'top';
    else if (desired === 'top') place = fitsAbove || !fitsBelow ? 'top' : 'bottom';
    else place = fitsRight ? 'right' : fitsBelow ? 'bottom' : fitsAbove ? 'top' : 'left';
    this.effectivePlacement.set(place);

    const align = this.align();
    let top: number;
    let left: number;
    if (place === 'left' || place === 'right') {
      left = place === 'right' ? a.right + gap : a.left - gap - p.width;
      if (align === 'start') top = a.top;
      else if (align === 'end') top = a.bottom - p.height;
      else top = a.top + a.height / 2 - p.height / 2; // center / auto
    } else {
      top = place === 'bottom' ? a.bottom + gap : a.top - gap - p.height;
      if (align === 'end') left = a.right - p.width;
      else if (align === 'center') left = a.left + a.width / 2 - p.width / 2;
      else left = a.left; // start / auto
    }

    // Keep the surface within the viewport.
    left = Math.max(margin, Math.min(left, vw - p.width - margin));
    top = Math.max(margin, Math.min(top, vh - p.height - margin));
    this.fixedLeft.set(Math.round(left));
    this.fixedTop.set(Math.round(top));
  }
}
