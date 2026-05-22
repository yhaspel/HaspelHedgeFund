import { TestBed } from '@angular/core/testing';
import { virtual } from '@guidepup/virtual-screen-reader';
import { PersonaCardComponent } from './persona-card.component';
import { PersonaMeta } from '../../core/models/run.model';

const PERSONA: PersonaMeta = {
  id: 'buffett',
  name: 'Warren Buffett',
  tagline: 'quality moat',
  initials: 'WB',
  monogramVariant: 1,
  description: 'Long-term owner of high-quality businesses.',
} as PersonaMeta;

/**
 * Virtual-SR pass for hf-persona-card (WS-3 a11y polish).
 *
 * The mono-tile is `aria-hidden` (decorative); the persona's name +
 * tagline must reach AT via the checkbox accessible name. Catches
 * regressions where someone removes the aria-label or restructures
 * the visually-hidden input.
 */
describe('hf-persona-card · virtual screen reader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PersonaCardComponent],
    }).compileComponents();
  });

  afterEach(async () => {
    try {
      await virtual.stop();
    } catch {
      // already stopped
    }
  });

  it('announces checkbox with name + tagline; tile is hidden from AT', async () => {
    const fixture = TestBed.createComponent(PersonaCardComponent);
    fixture.componentRef.setInput('persona', PERSONA);
    fixture.componentRef.setInput('selected', true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    document.body.appendChild(fixture.nativeElement);
    await virtual.start({ container: document.body });
    // Walk the tree — virtual-SR's `start` only announces the container; we
    // need `next()` to step through descendants like a real SR user would.
    for (let i = 0; i < 8; i++) await virtual.next();
    const log = (await virtual.spokenPhraseLog()).join(' | ').toLowerCase();

    // Accessible name combines persona name + tagline (per the aria-label binding)
    expect(log).toContain('warren buffett');
    expect(log).toContain('quality moat');
    expect(log).toContain('checkbox');

    // Decorative monogram initials must NOT be announced
    // (aria-hidden="true" on the <span class="mono-tile ..."> wrapping "WB")
    expect(log).not.toContain('wb');
  });
});
