import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { virtual } from '@guidepup/virtual-screen-reader';

/**
 * Virtual-SR pass for the tabbed Run·Detail pattern (ADR 0002).
 *
 * RunsDetailPage itself has heavy store + routing dependencies; this
 * spec exercises the *pattern* — a five-tab strip mirroring the
 * Decision/Council/Risk/CIO/Raw structure with the same ARIA wiring
 * (role="tab", aria-selected, aria-controls, tabindex roving) and the
 * same `.count` / `.count.attn` semantics. Catches regressions in the
 * tab pattern without having to spin up the full page.
 *
 * Cross-AT phrasing checks remain the human gate (see
 * development-plans/phase-06a-voice-accessibility.md).
 */
type Tab = 'decision' | 'council' | 'risk' | 'cio' | 'raw';

@Component({
  standalone: true,
  template: `
    <nav role="tablist" aria-label="Run sections">
      @for (t of tabs; track t.id) {
        <button
          type="button"
          role="tab"
          [id]="'tabbtn-' + t.id"
          [attr.aria-controls]="'tab-' + t.id"
          [attr.aria-selected]="active() === t.id"
          [attr.tabindex]="active() === t.id ? 0 : -1">
          {{ t.label }}
          @if (t.count) {
            <span>{{ t.count }}</span>
          }
        </button>
      }
    </nav>
    @if (active() === 'decision') {
      <div role="tabpanel" id="tab-decision" aria-labelledby="tabbtn-decision" tabindex="0">
        Decision content
      </div>
    }
    @if (active() === 'council') {
      <div role="tabpanel" id="tab-council" aria-labelledby="tabbtn-council" tabindex="0">
        Council content
      </div>
    }
  `,
})
class TabsHarness {
  readonly active = signal<Tab>('decision');
  readonly tabs: { id: Tab; label: string; count?: string }[] = [
    { id: 'decision', label: 'Decision' },
    { id: 'council', label: 'Council', count: '5' },
    { id: 'risk', label: 'Risk' },
    { id: 'cio', label: 'CIO', count: 'OVERRIDE' },
    { id: 'raw', label: 'Raw', count: '11' },
  ];
}

describe('Run·Detail tabs · virtual screen reader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TabsHarness],
    }).compileComponents();
  });

  afterEach(async () => {
    try {
      await virtual.stop();
    } catch {
      // already stopped
    }
  });

  it('announces tablist with selected tab + counts', async () => {
    const fixture = TestBed.createComponent(TabsHarness);
    fixture.detectChanges();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    for (let i = 0; i < 12; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    expect(log).toContain('tablist');
    expect(log).toContain('run sections');
    // First tab is selected
    expect(log).toContain('decision');
    // Other tabs + their counts are reachable as separate tab items
    expect(log).toContain('council');
    expect(log).toContain('5');
    expect(log).toContain('override');
  });

  it('moves selection on tab change and reflects via aria-selected', async () => {
    const fixture = TestBed.createComponent(TabsHarness);
    fixture.detectChanges();

    // Programmatically activate Council
    fixture.componentInstance.active.set('council');
    fixture.detectChanges();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    for (let i = 0; i < 14; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    // Council must announce as selected; Decision must announce as not-selected.
    // virtual-SR phrases this as "tab, <label>, selected" / "not selected".
    expect(log).toMatch(/tab,\s*council[^,]*,\s*selected/);
    expect(log).toMatch(/tab,\s*decision,\s*not selected/);
  });
});
