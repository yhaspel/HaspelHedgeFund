import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ModalComponent } from './modal.component';

/**
 * Behavioural spec for hf-modal (WS-3.2 / AR-05) — focus trap, Escape,
 * overlay click, focus return. The virtual-SR pass lives in
 * modal.component.a11y.spec.ts.
 */
@Component({
  standalone: true,
  imports: [ModalComponent],
  template: `
    <button type="button" #opener (click)="open.set(true)" id="opener">Open</button>
    @if (open()) {
      <hf-modal titleId="t" [dismissOnOverlay]="dismissOnOverlay" (closed)="onClosed()">
        <h2 id="t">Title</h2>
        <button type="button" id="first">First</button>
        <button type="button" id="middle">Middle</button>
        <button type="button" id="last">Last</button>
      </hf-modal>
    }
  `,
})
class HarnessComponent {
  open = signal(false);
  dismissOnOverlay = true;
  closedCount = 0;
  onClosed(): void {
    this.closedCount++;
    this.open.set(false);
  }
}

function tab(el: Element | null, shift = false): KeyboardEvent {
  const ev = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, bubbles: true, cancelable: true });
  (el ?? document.body).dispatchEvent(ev);
  return ev;
}

function escape(el: Element | null): KeyboardEvent {
  const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  (el ?? document.body).dispatchEvent(ev);
  return ev;
}

describe('hf-modal · focus + Escape', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HarnessComponent] }).compileComponents();
  });

  function openModal() {
    const fixture = TestBed.createComponent(HarnessComponent);
    document.body.appendChild(fixture.nativeElement);
    fixture.detectChanges();
    const opener = fixture.nativeElement.querySelector('#opener') as HTMLButtonElement;
    opener.focus();
    expect(document.activeElement).toBe(opener);
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    // ngAfterViewInit schedules the initial focus via queueMicrotask
    return new Promise<{ fixture: ReturnType<typeof TestBed.createComponent<HarnessComponent>>; opener: HTMLButtonElement }>(
      (resolve) => queueMicrotask(() => resolve({ fixture, opener })),
    );
  }

  it('moves focus into the dialog on open (first focusable button)', async () => {
    const { fixture } = await openModal();
    const first = fixture.nativeElement.querySelector('#first');
    expect(document.activeElement).toBe(first);
  });

  it('emits (closed) when Escape is pressed inside the dialog', async () => {
    const { fixture } = await openModal();
    const dialog = fixture.nativeElement.querySelector('[role="dialog"]');
    escape(dialog);
    expect(fixture.componentInstance.closedCount).toBe(1);
  });

  it('Tab from the last focusable wraps to the first', async () => {
    const { fixture } = await openModal();
    const last = fixture.nativeElement.querySelector('#last') as HTMLButtonElement;
    last.focus();
    expect(document.activeElement).toBe(last);

    const ev = tab(last);
    expect(ev.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(fixture.nativeElement.querySelector('#first'));
  });

  it('Shift+Tab from the first focusable wraps to the last', async () => {
    const { fixture } = await openModal();
    const first = fixture.nativeElement.querySelector('#first') as HTMLButtonElement;
    first.focus();
    expect(document.activeElement).toBe(first);

    const ev = tab(first, true);
    expect(ev.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(fixture.nativeElement.querySelector('#last'));
  });

  it('emits (closed) on overlay click but not on inner-content click', async () => {
    const { fixture } = await openModal();
    const overlay = fixture.nativeElement.querySelector('.modal-overlay') as HTMLElement;
    // Click the inner dialog → should NOT close
    const dialog = fixture.nativeElement.querySelector('[role="dialog"]') as HTMLElement;
    dialog.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(fixture.componentInstance.closedCount).toBe(0);
    // Click the overlay directly → should close
    const ev = new MouseEvent('click', { bubbles: true });
    Object.defineProperty(ev, 'target', { value: overlay });
    Object.defineProperty(ev, 'currentTarget', { value: overlay });
    overlay.dispatchEvent(ev);
    expect(fixture.componentInstance.closedCount).toBe(1);
  });

  it('does not close on overlay click when dismissOnOverlay=false', async () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.componentInstance.dismissOnOverlay = false;
    document.body.appendChild(fixture.nativeElement);
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    await new Promise<void>((resolve) => queueMicrotask(() => resolve()));
    const overlay = fixture.nativeElement.querySelector('.modal-overlay') as HTMLElement;
    const ev = new MouseEvent('click', { bubbles: true });
    Object.defineProperty(ev, 'target', { value: overlay });
    Object.defineProperty(ev, 'currentTarget', { value: overlay });
    overlay.dispatchEvent(ev);
    expect(fixture.componentInstance.closedCount).toBe(0);
  });

  it('returns focus to the opener after the modal closes', async () => {
    const { fixture, opener } = await openModal();
    expect(document.activeElement).not.toBe(opener);
    const dialog = fixture.nativeElement.querySelector('[role="dialog"]');
    escape(dialog);
    fixture.detectChanges();
    // Modal sets timeout(0) to restore focus
    await new Promise<void>((resolve) => setTimeout(resolve, 5));
    expect(document.activeElement).toBe(opener);
  });

  it('emits (closed) only once per Escape press', async () => {
    const { fixture } = await openModal();
    const dialog = fixture.nativeElement.querySelector('[role="dialog"]');
    escape(dialog);
    expect(fixture.componentInstance.closedCount).toBe(1);
    // After consumer closes the modal, dispatching a second Escape on a fresh
    // open instance shouldn't double-count from the previous one.
  });
});
