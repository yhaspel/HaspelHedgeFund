import {
  ChangeDetectionStrategy,
  Component,
  Input,
  ViewChild,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { TickerHistoryStore } from '../../abstraction/ticker-history.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { PopoverComponent } from './popover.component';
import { TickerPopoverComponent } from './ticker-popover.component';

/**
 * hf-ticker — renders a ticker symbol with an accessible hover/focus
 * popover that lazy-loads the company name, trend sparkline, and the
 * latest market cap / P/E / EPS. WS-2 / R4 / WS-4.3.
 *
 * Positioning, viewport flip, Escape, and ARIA wiring come from hf-popover.
 * This component owns the trigger styling, the lazy-fetch trigger on first
 * open, and the `disablePopover` short-circuit (used in Name-column cells
 * where the popover would be noise because the name already shows beside).
 */
@Component({
  selector: 'hf-ticker',
  standalone: true,
  imports: [CommonModule, PopoverComponent, TickerPopoverComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="hf-tk-wrap">
      <span
        class="hf-tk-sym mono"
        tabindex="0"
        [attr.role]="disablePopover ? null : 'button'"
        [attr.aria-describedby]="pop.open() && !disablePopover ? pop.popoverId : null"
        (mouseenter)="onEnter()"
        (mouseleave)="onLeave()"
        (focus)="onFocus()"
        (blur)="onBlur()"
      >{{ ticker }}</span>
      <hf-popover #pop placement="bottom" align="start" surface="bare">
        @if (pop.open() && !disablePopover && fetchedOnce) {
          <hf-ticker-popover [ticker]="ticker"></hf-ticker-popover>
        }
      </hf-popover>
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
        /* WS-3.6: WCAG 2.2 §2.5.8 — 24×24 hit target without changing baseline. */
        display: inline-block;
        min-height: 24px;
        line-height: 24px;
        padding: 0 2px;
      }
      .hf-tk-sym:focus-visible {
        box-shadow: var(--focus-ring);
      }
    `,
  ],
})
export class TickerComponent {
  @Input() set ticker(v: string) {
    this._ticker = (v || '').toUpperCase();
  }
  get ticker(): string {
    return this._ticker;
  }
  private _ticker = '';

  @Input() disablePopover = false;

  @ViewChild(PopoverComponent, { static: true }) pop!: PopoverComponent;

  private readonly profileStore = inject(TickerProfileStore);
  private readonly historyStore = inject(TickerHistoryStore);

  protected fetchedOnce = false;

  onEnter(): void {
    if (this.disablePopover || !this.ticker) return;
    this.lazyFetch();
    this.pop.show();
  }

  onLeave(): void {
    if (this.disablePopover) return;
    this.pop.maybeHide();
  }

  onFocus(): void {
    if (this.disablePopover || !this.ticker) return;
    this.lazyFetch();
    this.pop.show();
  }

  onBlur(): void {
    if (this.disablePopover) return;
    this.pop.maybeHide();
  }

  private lazyFetch(): void {
    if (this.fetchedOnce) return;
    this.fetchedOnce = true;
    this.profileStore.fetchProfile(this.ticker).subscribe();
    this.historyStore.fetch(this.ticker, 60).subscribe();
  }
}
