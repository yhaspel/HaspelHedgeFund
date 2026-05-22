import { Component, ViewChild } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { virtual } from '@guidepup/virtual-screen-reader';
import { PopoverComponent } from './popover.component';

/**
 * Virtual-SR pass for hf-popover (WS-4.3 / DC-19).
 *
 * Asserts the structural a11y properties: while open, the popover surface
 * carries role="tooltip" and the trigger's aria-describedby resolves to
 * the popover's content, so a screen reader announces the body when the
 * trigger has focus. Awkward-phrasing / cross-AT differences remain a
 * human-only gate (see phase-06a-voice-accessibility.md).
 */

@Component({
  standalone: true,
  imports: [PopoverComponent],
  template: `
    <span class="wrap" style="position:relative">
      <button
        type="button"
        #trigger
        [attr.aria-describedby]="pop.open() ? pop.popoverId : null"
        (focus)="pop.show()"
        (blur)="pop.maybeHide()"
      >Trigger label</button>
      <hf-popover #pop placement="bottom" role="tooltip">
        Important context
      </hf-popover>
    </span>
  `,
})
class HarnessComponent {
  @ViewChild('pop', { static: true }) pop!: PopoverComponent;
}

describe('hf-popover · virtual screen reader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
    }).compileComponents();
  });

  afterEach(async () => {
    try {
      await virtual.stop();
    } catch {
      // already stopped
    }
  });

  it('announces tooltip role and reachable content when open', async () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();
    fixture.componentInstance.pop.show();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    // Walk forward — the trigger, then the tooltip body.
    for (let i = 0; i < 6; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    expect(log).toContain('trigger label');
    expect(log).toContain('tooltip');
    expect(log).toContain('important context');
  });

  it('does not announce content when closed', async () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    for (let i = 0; i < 6; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    expect(log).toContain('trigger label');
    expect(log).not.toContain('important context');
  });
});
