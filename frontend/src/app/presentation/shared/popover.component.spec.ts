import { Component, ViewChild } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { PopoverAlign, PopoverComponent, PopoverPlacement } from './popover.component';

/**
 * Unit spec for hf-popover (WS-4.3 / DC-19).
 *
 * Covers the headless state machine — show / hide / toggle, the hover-out
 * grace timer, document-level Escape — and viewport-aware placement
 * (vertical flip + horizontal alignment fallback). The accessibility
 * surface is exercised separately in popover.component.a11y.spec.ts.
 */

@Component({
  standalone: true,
  imports: [PopoverComponent],
  template: `
    <hf-popover
      #pop
      [placement]="placement"
      [align]="align"
      [hoverCloseDelay]="hoverCloseDelay"
      [anchor]="anchor"
    >
      <span class="content">Popover content</span>
    </hf-popover>
  `,
})
class HarnessComponent {
  @ViewChild('pop', { static: true }) pop!: PopoverComponent;
  placement: PopoverPlacement = 'top';
  align: PopoverAlign = 'start';
  hoverCloseDelay = 80;
  anchor: HTMLElement | null = null;
}

function rect(top: number, left: number, width: number, height: number): DOMRect {
  return {
    top,
    left,
    width,
    height,
    bottom: top + height,
    right: left + width,
    x: left,
    y: top,
    toJSON: () => ({}),
  } as DOMRect;
}

function setViewport(w: number, h: number): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: h });
}

function stubAnchor(anchorRect: DOMRect): HTMLElement {
  const el = document.createElement('span');
  el.getBoundingClientRect = () => anchorRect;
  return el;
}

/** Build a harness, applying any setup, then run the first (and only) CD. */
function makeHarness(setup?: (h: HarnessComponent) => void): ComponentFixture<HarnessComponent> {
  const fixture = TestBed.createComponent(HarnessComponent);
  if (setup) setup(fixture.componentInstance);
  fixture.detectChanges();
  return fixture;
}

/**
 * Open the popover, render, stub the popover's getBoundingClientRect to a
 * known size, and run the measurement explicitly. Returns once
 * effectivePlacement / effectiveAlign reflect the stubbed measurements.
 */
function openAndMeasure(
  fixture: ComponentFixture<HarnessComponent>,
  popRect: DOMRect,
): void {
  const pop = fixture.componentInstance.pop;
  pop.show();
  fixture.detectChanges();
  const popEl = pop.popRef?.nativeElement;
  if (popEl) popEl.getBoundingClientRect = () => popRect;
  (pop as unknown as { measureAndFlip: () => void }).measureAndFlip();
}

describe('hf-popover · state machine', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
    }).compileComponents();
    setViewport(1280, 800);
  });

  it('starts closed', () => {
    const fixture = makeHarness();
    expect(fixture.componentInstance.pop.open()).toBe(false);
  });

  it('show() opens and hide() closes', () => {
    const fixture = makeHarness();
    const { pop } = fixture.componentInstance;
    pop.show();
    expect(pop.open()).toBe(true);
    pop.hide();
    expect(pop.open()).toBe(false);
  });

  it('toggle() flips open state', () => {
    const fixture = makeHarness();
    const { pop } = fixture.componentInstance;
    pop.toggle();
    expect(pop.open()).toBe(true);
    pop.toggle();
    expect(pop.open()).toBe(false);
  });

  it('maybeHide() debounces close so hovering into the popover keeps it open', () => {
    vi.useFakeTimers();
    try {
      const fixture = makeHarness();
      const { pop } = fixture.componentInstance;
      pop.show();
      expect(pop.open()).toBe(true);

      pop.maybeHide();
      pop.onPopEnter();
      vi.advanceTimersByTime(120);
      expect(pop.open()).toBe(true);

      pop.onPopLeave();
      vi.advanceTimersByTime(120);
      expect(pop.open()).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('emits openChange on show + hide', () => {
    const fixture = makeHarness();
    const { pop } = fixture.componentInstance;
    const events: boolean[] = [];
    pop.openChange.subscribe((v) => events.push(v));
    pop.show();
    pop.hide();
    expect(events).toEqual([true, false]);
  });

  it('closes on document Escape', () => {
    const fixture = makeHarness();
    const { pop } = fixture.componentInstance;
    pop.show();
    expect(pop.open()).toBe(true);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(pop.open()).toBe(false);
  });
});

describe('hf-popover · viewport-aware placement', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
    }).compileComponents();
    setViewport(1280, 800);
  });

  it('flips placement=top → bottom when the anchor is near the top edge', () => {
    const fixture = makeHarness((h) => {
      // Anchor 4px from top edge; popover wants 120 + 12 margin = no room above.
      h.anchor = stubAnchor(rect(4, 100, 80, 24));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 120));
    expect(fixture.componentInstance.pop.effectivePlacement()).toBe('bottom');
  });

  it('keeps placement=top when there is room above', () => {
    const fixture = makeHarness((h) => {
      h.anchor = stubAnchor(rect(400, 100, 80, 24));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 120));
    expect(fixture.componentInstance.pop.effectivePlacement()).toBe('top');
  });

  it('flips placement=bottom → top when the anchor is near the bottom edge', () => {
    const fixture = makeHarness((h) => {
      h.placement = 'bottom';
      h.anchor = stubAnchor(rect(770, 100, 80, 24));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 120));
    expect(fixture.componentInstance.pop.effectivePlacement()).toBe('top');
  });

  it('flips align=start → end when the trigger is near the right edge', () => {
    const fixture = makeHarness((h) => {
      h.anchor = stubAnchor(rect(400, 1240, 30, 24));
    });
    openAndMeasure(fixture, rect(0, 0, 320, 120));
    expect(fixture.componentInstance.pop.effectiveAlign()).toBe('end');
  });

  it('keeps align=start when there is room on the right', () => {
    const fixture = makeHarness((h) => {
      h.anchor = stubAnchor(rect(400, 100, 80, 24));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 120));
    expect(fixture.componentInstance.pop.effectiveAlign()).toBe('start');
  });

  it('flips align=end → start when the trigger is near the left edge', () => {
    const fixture = makeHarness((h) => {
      h.align = 'end';
      h.anchor = stubAnchor(rect(400, 10, 30, 24));
    });
    openAndMeasure(fixture, rect(0, 0, 320, 120));
    expect(fixture.componentInstance.pop.effectiveAlign()).toBe('start');
  });

  it('honors placement=right when there is room to the right', () => {
    const fixture = makeHarness((h) => {
      h.placement = 'right';
      h.anchor = stubAnchor(rect(400, 60, 30, 30));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 80));
    expect(fixture.componentInstance.pop.effectivePlacement()).toBe('right');
  });

  it('flips placement=right → left when the trigger is near the right edge', () => {
    const fixture = makeHarness((h) => {
      h.placement = 'right';
      // Anchor 30px wide at left=1240 with viewport 1280 — only ~10px to the right.
      h.anchor = stubAnchor(rect(400, 1240, 30, 30));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 80));
    expect(fixture.componentInstance.pop.effectivePlacement()).toBe('left');
  });

  it('honors placement=left when there is room to the left', () => {
    const fixture = makeHarness((h) => {
      h.placement = 'left';
      h.anchor = stubAnchor(rect(400, 1000, 30, 30));
    });
    openAndMeasure(fixture, rect(0, 0, 200, 80));
    expect(fixture.componentInstance.pop.effectivePlacement()).toBe('left');
  });
});
