import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { GLOSSARY_TERMS } from '../../core/models/glossary-terms';
import { PopoverComponent } from './popover.component';

/**
 * hf-term — inline term with an accessible popover showing a short definition
 * and a "Full definition →" link to the glossary anchor. WS-4.3 / DC-19.
 *
 * Composes hf-popover for positioning, viewport flip, ARIA wiring, Escape
 * and outside-click dismissal. Hover-out close is delayed (220ms) so the
 * cursor can cross the gap into the popover and click the "Full definition"
 * link without the popover collapsing.
 *
 * WCAG 2.2 AA 1.4.13 — hoverable, dismissible, persistent (Escape, blur,
 * outside-click, and the trigger toggle all hide cleanly).
 */
@Component({
  selector: 'hf-term',
  standalone: true,
  imports: [RouterLink, PopoverComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="hf-term-wrap">
      <button
        type="button"
        class="hf-term-trigger"
        [attr.aria-expanded]="pop.open()"
        [attr.aria-describedby]="pop.open() ? pop.popoverId : null"
        (click)="pop.toggle()"
        (mouseenter)="pop.show()"
        (mouseleave)="pop.maybeHide()"
        (focus)="pop.show()"
        (blur)="pop.maybeHide()"
      ><ng-content></ng-content></button>
      <hf-popover
        #pop
        placement="bottom"
        align="start"
        role="tooltip"
        [hoverCloseDelay]="220"
        [dismissOnOutsideClick]="true"
      >
        @if (term(); as t) {
          <span class="hf-term-content">
            <span class="hf-term-pop-term">{{ t.term }}</span>
            <span class="hf-term-pop-def">{{ t.short }}</span>
            <a
              class="hf-term-pop-link"
              [routerLink]="['/info', 'glossary']"
              [fragment]="t.anchor"
              (click)="pop.hide()"
            >Full definition →</a>
          </span>
        }
      </hf-popover>
    </span>
  `,
  styles: [
    `
      .hf-term-wrap {
        position: relative;
        display: inline;
      }
      .hf-term-trigger {
        /* Inline-flow control inside body text — WCAG 2.2 §2.5.8 inline
           exception applies, but axe doesn't honour it, so the vertical
           hit area is padded to ≥24px while baseline alignment is kept. */
        display: inline-block;
        padding: 2px 1px;
        margin: 0;
        background: transparent;
        border: 0;
        color: inherit;
        font: inherit;
        text-align: inherit;
        cursor: help;
        border-bottom: 1px dotted var(--text-3);
        line-height: inherit;
        vertical-align: baseline;
        min-height: 24px;
      }
      .hf-term-trigger:hover,
      .hf-term-trigger:focus {
        color: var(--acc-info-fg);
        border-bottom-color: var(--acc-info);
        outline: none;
      }
      .hf-term-trigger:focus-visible {
        box-shadow: var(--focus-ring);
        border-radius: var(--r-2);
      }
      .hf-term-content {
        display: flex;
        flex-direction: column;
        gap: 6px;
        white-space: normal;
      }
      .hf-term-pop-term {
        font-weight: 600;
        font-size: var(--fs-13);
        color: var(--text);
      }
      .hf-term-pop-def {
        color: var(--text-2);
      }
      .hf-term-pop-link {
        align-self: flex-start;
        color: var(--acc-info-fg);
        font-weight: 500;
      }
      .hf-term-pop-link:hover {
        text-decoration: underline;
      }
    `,
  ],
})
export class GlossaryTermComponent {
  key = input.required<string>();
  readonly term = computed(() => GLOSSARY_TERMS[this.key()]);
}
