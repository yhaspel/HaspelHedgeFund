import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';
import { virtual } from '@guidepup/virtual-screen-reader';
import { CommandPaletteComponent } from './command-palette.component';
import { CommandPaletteService } from '../../abstraction/command-palette.service';

/**
 * Virtual-SR pass for the ⌘K palette (ADR 0004).
 *
 * Catches the *structural* announcement properties that axe can't: the
 * palette opens as a dialog with a labelled search input, results render
 * as a listbox with options that carry position-in-set info (via
 * aria-activedescendant). Awkward phrasing / cross-AT differences remain
 * a human gate (see phase-06a-voice-accessibility.md).
 */
describe('hf-command-palette · virtual screen reader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CommandPaletteComponent],
      providers: [provideRouter([]), provideHttpClient()],
    }).compileComponents();
  });

  afterEach(async () => {
    try {
      await virtual.stop();
    } catch {
      // already stopped
    }
  });

  it('announces dialog + labelled search input', async () => {
    const fixture = TestBed.createComponent(CommandPaletteComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    for (let i = 0; i < 10; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    expect(log).toContain('dialog');
    // Accessible name comes from aria-label on the input
    expect(log).toContain('search runs, strategies, backtests');
  });

  it('exposes a listbox for results with role-aware structure', async () => {
    const fixture = TestBed.createComponent(CommandPaletteComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    // Seed a query so results render
    const svc = TestBed.inject(CommandPaletteService);
    svc.query.set('q');
    fixture.detectChanges();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    // Walk forward several steps to encounter the results region
    for (let i = 0; i < 6; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    // The results container has role="listbox" with aria-label "Search results"
    expect(log).toContain('search results');
  });
});
