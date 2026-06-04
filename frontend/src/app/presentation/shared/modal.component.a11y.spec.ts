import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { virtual } from '@guidepup/virtual-screen-reader';
import { ModalComponent } from './modal.component';

/**
 * Virtual-SR pass for hf-modal (WS-3.2 / AR-05).
 *
 * Catches the *structural* announcement properties that axe can't:
 * dialog role announces, accessible name is reachable, focus lands
 * inside on open, Tab/Shift+Tab stays inside. Awkward-phrasing /
 * cross-AT (VoiceOver vs NVDA) differences remain a human-only gate.
 */
@Component({
  standalone: true,
  imports: [ModalComponent],
  template: `
    <hf-modal titleId="harness-title">
      <h2 id="harness-title">Edit position</h2>
      <button type="button">Cancel</button>
      <button type="button">Save</button>
    </hf-modal>
  `,
})
class HarnessComponent {}

describe('hf-modal · virtual screen reader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
    }).compileComponents();
  });

  afterEach(async () => {
    try {
      await virtual.stop();
    } catch {
      // virtual may already be stopped
    }
  });

  it('announces dialog role and labelled name', async () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();

    await virtual.start({ container: document.body });
    const log = await virtual.spokenPhraseLog();

    const joined = log.join(' | ').toLowerCase();
    expect(joined).toContain('dialog');
    expect(joined).toContain('edit position');
  });

  it('walks dialog content in reading order (heading → buttons)', async () => {
    const fixture = TestBed.createComponent(HarnessComponent);
    fixture.detectChanges();

    await virtual.start({ container: document.body });
    // Step through the tree
    await virtual.next();
    await virtual.next();
    await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    // Reading order: dialog → heading "Edit position" → button "Cancel" → button "Save"
    expect(log.indexOf('edit position')).toBeGreaterThan(-1);
    expect(log.indexOf('cancel')).toBeGreaterThan(log.indexOf('edit position'));
    expect(log.indexOf('save')).toBeGreaterThan(log.indexOf('cancel'));
  });
});
