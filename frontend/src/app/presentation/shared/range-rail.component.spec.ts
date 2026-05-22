import { Component, ViewChild } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RangeRailComponent, RangeRailTone } from './range-rail.component';

@Component({
  standalone: true,
  imports: [RangeRailComponent],
  template: `
    <hf-range-rail
      #rr
      [min]="min"
      [max]="max"
      [value]="value"
      [bandLo]="bandLo"
      [bandHi]="bandHi"
      [tone]="tone"
      [format]="format"
      [ariaLabel]="ariaLabel">
    </hf-range-rail>
  `,
})
class HarnessComponent {
  @ViewChild('rr', { static: true }) rr!: RangeRailComponent;
  min = 0;
  max = 100;
  value: number | null = 50;
  bandLo?: number;
  bandHi?: number;
  tone: RangeRailTone = 'neutral';
  format: (v: number) => string = (v) => v.toFixed(2);
  ariaLabel?: string;
}

function makeHarness(setup?: (h: HarnessComponent) => void): ComponentFixture<HarnessComponent> {
  const fixture = TestBed.createComponent(HarnessComponent);
  if (setup) setup(fixture.componentInstance);
  fixture.detectChanges();
  return fixture;
}

describe('hf-range-rail · marker position', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HarnessComponent] }).compileComponents();
  });

  it('places the marker at 0% when value === min', () => {
    const fixture = makeHarness((h) => { h.min = 0; h.max = 100; h.value = 0; });
    expect(fixture.componentInstance.rr.markerPos).toBe(0);
  });

  it('places the marker at 100% when value === max', () => {
    const fixture = makeHarness((h) => { h.min = 0; h.max = 100; h.value = 100; });
    expect(fixture.componentInstance.rr.markerPos).toBe(100);
  });

  it('places the marker at 50% when value is at the midpoint', () => {
    const fixture = makeHarness((h) => { h.min = 10; h.max = 30; h.value = 20; });
    expect(fixture.componentInstance.rr.markerPos).toBe(50);
  });

  it('clamps values below min to 0%', () => {
    const fixture = makeHarness((h) => { h.min = 100; h.max = 200; h.value = 50; });
    expect(fixture.componentInstance.rr.markerPos).toBe(0);
  });

  it('clamps values above max to 100%', () => {
    const fixture = makeHarness((h) => { h.min = 100; h.max = 200; h.value = 999; });
    expect(fixture.componentInstance.rr.markerPos).toBe(100);
  });

  it('returns null when value is null', () => {
    const fixture = makeHarness((h) => { h.value = null; });
    expect(fixture.componentInstance.rr.markerPos).toBeNull();
  });

  it('returns 0 when min === max (degenerate range)', () => {
    const fixture = makeHarness((h) => { h.min = 5; h.max = 5; h.value = 5; });
    expect(fixture.componentInstance.rr.markerPos).toBe(0);
  });
});

describe('hf-range-rail · band position', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HarnessComponent] }).compileComponents();
  });

  it('returns null when bandLo or bandHi is undefined', () => {
    const fixture = makeHarness();
    expect(fixture.componentInstance.rr.bandPos).toBeNull();
  });

  it('spans bandLo% to bandHi% when both are provided', () => {
    const fixture = makeHarness((h) => {
      h.min = 0; h.max = 100; h.bandLo = 20; h.bandHi = 80;
    });
    const bp = fixture.componentInstance.rr.bandPos;
    expect(bp).toEqual({ left: 20, width: 60 });
  });

  it('swaps bandLo / bandHi if caller passes them in the wrong order', () => {
    const fixture = makeHarness((h) => {
      h.min = 0; h.max = 100; h.bandLo = 80; h.bandHi = 20;
    });
    const bp = fixture.componentInstance.rr.bandPos;
    expect(bp).toEqual({ left: 20, width: 60 });
  });

  it('clamps a band that overflows the rail extents', () => {
    const fixture = makeHarness((h) => {
      h.min = 0; h.max = 100; h.bandLo = -50; h.bandHi = 200;
    });
    const bp = fixture.componentInstance.rr.bandPos;
    expect(bp).toEqual({ left: 0, width: 100 });
  });
});

describe('hf-range-rail · labels + aria', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HarnessComponent] }).compileComponents();
  });

  it('applies the format callback to min/max labels', () => {
    const fixture = makeHarness((h) => {
      h.min = 100;
      h.max = 200;
      h.format = (v) => `$ ${v.toFixed(0)}`;
    });
    const labels = fixture.nativeElement.querySelectorAll('.labels span');
    expect(labels[0].textContent.trim()).toBe('$ 100');
    expect(labels[1].textContent.trim()).toBe('$ 200');
  });

  it('uses defaultAriaLabel when no ariaLabel is provided', () => {
    const fixture = makeHarness((h) => {
      h.min = 0; h.max = 1; h.value = 0.5; h.format = (v) => `${(v * 100).toFixed(0)}%`;
    });
    const rrEl = fixture.nativeElement.querySelector('.rr');
    expect(rrEl.getAttribute('aria-label')).toBe('Value 50% on range 0% to 100%');
  });

  it('overrides aria-label with the input when provided', () => {
    const fixture = makeHarness((h) => { h.ariaLabel = 'Target zone'; });
    const rrEl = fixture.nativeElement.querySelector('.rr');
    expect(rrEl.getAttribute('aria-label')).toBe('Target zone');
  });

  it('shows "—" for the value in the default aria label when value is null', () => {
    const fixture = makeHarness((h) => { h.value = null; });
    const rrEl = fixture.nativeElement.querySelector('.rr');
    expect(rrEl.getAttribute('aria-label')).toContain('Value —');
  });
});

describe('hf-range-rail · tone class', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HarnessComponent] }).compileComponents();
  });

  it('applies the long tone class', () => {
    const fixture = makeHarness((h) => { h.tone = 'long'; });
    const rail = fixture.nativeElement.querySelector('.rail');
    expect(rail.classList.contains('long')).toBe(true);
  });

  it('applies the short tone class', () => {
    const fixture = makeHarness((h) => { h.tone = 'short'; });
    const rail = fixture.nativeElement.querySelector('.rail');
    expect(rail.classList.contains('short')).toBe(true);
  });

  it('omits all tone classes for neutral', () => {
    const fixture = makeHarness((h) => { h.tone = 'neutral'; });
    const rail = fixture.nativeElement.querySelector('.rail');
    expect(rail.classList.contains('long')).toBe(false);
    expect(rail.classList.contains('short')).toBe(false);
    expect(rail.classList.contains('hold')).toBe(false);
  });
});
