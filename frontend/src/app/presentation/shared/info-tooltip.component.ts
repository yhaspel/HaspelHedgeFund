import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { PopoverComponent } from './popover.component';

/**
 * hf-info — small "!" icon with an accessible help tooltip. WS-4.3 / DC-19.
 *
 * Composes hf-popover for positioning, viewport-aware flipping, ARIA wiring,
 * and Escape dismissal. The component owns only the trigger styling and the
 * `text` input.
 */
@Component({
  selector: 'hf-info',
  standalone: true,
  imports: [PopoverComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="hf-info">
      <button
        type="button"
        class="hf-info-trigger"
        [attr.aria-label]="text()"
        [attr.aria-describedby]="pop.open() ? pop.popoverId : null"
        (mouseenter)="pop.show()"
        (mouseleave)="pop.maybeHide()"
        (focus)="pop.show()"
        (blur)="pop.maybeHide()"
      >!</button>
      <hf-popover #pop placement="top" align="start">
        <span class="hf-info-body">{{ text() }}</span>
      </hf-popover>
    </span>
  `,
  styles: [
    `
      .hf-info {
        position: relative;
        display: inline-block;
        vertical-align: middle;
        margin-left: 4px;
      }
      .hf-info-trigger {
        width: 14px;
        height: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        background: var(--surface-2);
        border: 1px solid var(--border-2);
        color: var(--text-2);
        font-size: 9px;
        font-weight: 700;
        line-height: 1;
        padding: 0;
        cursor: help;
        transition:
          background var(--dur-fast),
          color var(--dur-fast),
          border-color var(--dur-fast);
      }
      .hf-info-trigger:hover,
      .hf-info-trigger:focus {
        background: var(--acc-info);
        border-color: var(--acc-info);
        color: white;
        outline: none;
      }
      .hf-info-body {
        font-size: 11.5px;
        line-height: 16px;
        white-space: normal;
      }
    `,
  ],
})
export class InfoTooltipComponent {
  text = input<string>('');
}
