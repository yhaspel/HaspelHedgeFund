import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnDestroy,
  Output,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';

let _modalIdSeq = 0;

/**
 * hf-modal — shared accessible dialog shell. WS-3.2 / AR-05.
 *
 * Sets role="dialog" + aria-modal, moves focus to the first focusable
 * element on open, traps Tab/Shift+Tab inside the dialog, returns focus
 * to the previously focused trigger on close, and emits `(closed)` on
 * Escape or overlay click. Consumers project their full content (header,
 * body, footer) inside.
 *
 * Usage:
 *   <hf-modal *ngIf="open" titleText="Edit position" (closed)="onCancel()">
 *     <div class="card-hd"><span class="title">…</span></div>
 *     …
 *   </hf-modal>
 */
@Component({
  selector: 'hf-modal',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="modal-overlay" (click)="onOverlayClick($event)" #overlay>
      <div #dialog
           class="hf-modal-dialog"
           role="dialog"
           aria-modal="true"
           [attr.aria-labelledby]="titleId"
           tabindex="-1">
        <ng-content></ng-content>
      </div>
    </div>
  `,
  styles: [
    `
      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.6);
        z-index: var(--z-modal);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
      }
      .hf-modal-dialog {
        max-width: 100%;
        max-height: 90vh;
        outline: none;
        display: contents;
      }
    `,
  ],
})
export class ModalComponent implements AfterViewInit, OnDestroy {
  @Input() titleId = `hf-modal-title-${++_modalIdSeq}`;
  @Input() dismissOnOverlay = true;
  @Output() closed = new EventEmitter<void>();

  @ViewChild('dialog', { static: true }) dialogRef!: ElementRef<HTMLElement>;

  private previouslyFocused: HTMLElement | null = null;

  ngAfterViewInit(): void {
    this.previouslyFocused = (document.activeElement as HTMLElement) || null;
    queueMicrotask(() => this.focusFirstInside());
  }

  ngOnDestroy(): void {
    if (this.previouslyFocused && this.previouslyFocused.focus) {
      // Defer to next macrotask so any unmount-time re-renders don't steal focus.
      setTimeout(() => this.previouslyFocused?.focus(), 0);
    }
  }

  @HostListener('keydown', ['$event'])
  onKeydown(ev: KeyboardEvent): void {
    if (ev.key === 'Escape') {
      ev.stopPropagation();
      this.closed.emit();
      return;
    }
    if (ev.key !== 'Tab') return;
    const focusables = this.collectFocusables();
    if (!focusables.length) {
      ev.preventDefault();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (ev.shiftKey) {
      if (active === first || !active || !this.dialogRef.nativeElement.contains(active)) {
        ev.preventDefault();
        last.focus();
      }
    } else if (active === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  onOverlayClick(ev: MouseEvent): void {
    if (!this.dismissOnOverlay) return;
    // Only fire when the click landed on the overlay itself, not on dialog content.
    if (ev.target === ev.currentTarget) {
      this.closed.emit();
    }
  }

  private focusFirstInside(): void {
    const focusables = this.collectFocusables();
    if (focusables.length) {
      focusables[0].focus();
    } else {
      this.dialogRef.nativeElement.focus();
    }
  }

  private collectFocusables(): HTMLElement[] {
    const root = this.dialogRef.nativeElement;
    const selector = [
      'a[href]',
      'button:not([disabled])',
      'input:not([disabled]):not([type="hidden"])',
      'select:not([disabled])',
      'textarea:not([disabled])',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',');
    return Array.from(root.querySelectorAll<HTMLElement>(selector)).filter(
      (el) => el.offsetParent !== null || el === root,
    );
  }
}
