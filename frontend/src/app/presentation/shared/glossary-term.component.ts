import {
  Component,
  ElementRef,
  HostListener,
  ViewChild,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { GLOSSARY_TERMS } from '../../core/models/glossary-terms';

let nextId = 0;

/**
 * Inline term with an accessible popover showing a short definition and a
 * "Full definition →" link to the glossary anchor.
 *
 * WCAG 2.2 AA 1.4.13 — hoverable, dismissible, persistent.
 */
@Component({
  selector: 'hf-term',
  standalone: true,
  imports: [RouterLink],
  template: `
    <span class="hf-term-wrap">
      <button
        #trigger
        type="button"
        class="hf-term-trigger"
        [attr.aria-expanded]="open()"
        [attr.aria-describedby]="open() ? popId : null"
        (click)="toggle()"
        (mouseenter)="onTriggerEnter()"
        (mouseleave)="onTriggerLeave()"
        (focus)="setOpen(true)"
        (blur)="onBlur($event)"
        (keydown.escape)="setOpen(false); $event.stopPropagation()"
      ><ng-content></ng-content></button>
      @if (open() && term(); as t) {
        <span
          class="hf-term-pop"
          [id]="popId"
          role="tooltip"
          (mouseenter)="onPopEnter()"
          (mouseleave)="onPopLeave()"
        >
          <span class="hf-term-pop-term">{{ t.term }}</span>
          <span class="hf-term-pop-def">{{ t.short }}</span>
          <a class="hf-term-pop-link" [routerLink]="['/info', 'glossary']" [fragment]="t.anchor"
             (click)="setOpen(false)">
            Full definition →
          </a>
        </span>
      }
    </span>
  `,
  styles: [`
    .hf-term-wrap {
      position: relative;
      display: inline;
    }
    .hf-term-trigger {
      /* Inline-flow control inside body text — WCAG 2.2 §2.5.8 inline
         exception applies, but axe doesn't honour it, so we bump the
         vertical hit area to ≥24px via padding while keeping baseline
         alignment so surrounding text doesn't reflow. */
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
    .hf-term-pop {
      position: absolute;
      z-index: var(--z-tooltip);
      left: 0;
      top: calc(100% + 6px);
      min-width: 220px;
      max-width: 320px;
      padding: 10px 12px;
      background: var(--surface-3);
      color: var(--text);
      border: 1px solid var(--border-2);
      border-radius: var(--r-6);
      box-shadow: var(--shadow-2);
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: var(--fs-12);
      line-height: var(--lh-13);
      font-family: var(--font-sans);
      letter-spacing: 0;
      text-transform: none;
      font-weight: 400;
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
  `],
})
export class GlossaryTermComponent {
  key = input.required<string>();

  @ViewChild('trigger') triggerEl?: ElementRef<HTMLButtonElement>;
  private readonly host = inject(ElementRef<HTMLElement>);

  readonly popId = `hf-term-pop-${++nextId}`;
  readonly open = signal(false);
  readonly term = computed(() => GLOSSARY_TERMS[this.key()]);

  private hoverCloseTimer: number | null = null;

  setOpen(v: boolean) {
    this.open.set(v);
  }

  toggle() {
    this.open.update((v) => !v);
  }

  onTriggerEnter() {
    this.cancelHoverClose();
    this.setOpen(true);
  }

  onTriggerLeave() {
    this.scheduleHoverClose();
  }

  onPopEnter() {
    this.cancelHoverClose();
  }

  onPopLeave() {
    this.scheduleHoverClose();
  }

  onBlur(e: FocusEvent) {
    // Keep open if focus moved into the popover (e.g. tabbing to the link).
    const next = e.relatedTarget as HTMLElement | null;
    if (next && this.host.nativeElement.contains(next)) return;
    this.setOpen(false);
  }

  @HostListener('document:keydown.escape')
  onDocEscape() {
    if (this.open()) this.setOpen(false);
  }

  @HostListener('document:click', ['$event'])
  onDocClick(e: MouseEvent) {
    if (!this.open()) return;
    const t = e.target as Node | null;
    if (t && !this.host.nativeElement.contains(t)) this.setOpen(false);
  }

  private scheduleHoverClose() {
    this.cancelHoverClose();
    this.hoverCloseTimer = window.setTimeout(() => {
      this.setOpen(false);
    }, 220);
  }

  private cancelHoverClose() {
    if (this.hoverCloseTimer !== null) {
      window.clearTimeout(this.hoverCloseTimer);
      this.hoverCloseTimer = null;
    }
  }
}
