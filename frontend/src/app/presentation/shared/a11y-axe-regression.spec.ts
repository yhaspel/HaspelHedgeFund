import { Component, ViewChild } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import axe from 'axe-core';
import { GrossNetMeterComponent } from './gross-net-meter.component';
import { PopoverComponent } from './popover.component';

/**
 * P4 WS-DA-9 — automated axe regression lock for the *structural* serious
 * rules that reached production after the popover/meter changes
 * (`aria-tooltip-name`, `aria-prohibited-attr`). These rules evaluate the
 * ARIA tree and accessible names, which run reliably under the unit-test DOM.
 *
 * Colour-contrast (WS-DA-2 / WS-DA-10) and the full per-route sweep need a real
 * browser (axe's contrast check requires layout/compositing that happy-dom +
 * jsdom do not provide); those remain a manual Chrome+axe pass — see
 * development-plans/ui/design-review/phase04-axe-post-remediation.md. This spec
 * is the in-CI guard for the regressions that are testable headlessly.
 */

const STRUCTURAL_RULES = [
  'aria-tooltip-name',
  'aria-prohibited-attr',
  'aria-required-attr',
  'aria-roles',
  'image-alt',
  'button-name',
];

async function axeStructural(node: Element) {
  const result = await axe.run(node as HTMLElement, {
    runOnly: { type: 'rule', values: STRUCTURAL_RULES },
  });
  return result.violations;
}

@Component({
  standalone: true,
  imports: [PopoverComponent],
  // A nav-button shape exactly like the real sidebar consumers: an
  // aria-labelled control with a closed hf-popover inside. Pre-WS-DA-1 the
  // static role="tooltip" leaked onto the empty host as an unnamed tooltip.
  template: `
    <a aria-label="Dashboard" href="#">
      <hf-popover #pop placement="right" size="compact" role="tooltip">Dashboard</hf-popover>
    </a>
  `,
})
class PopoverHostHarness {
  @ViewChild('pop', { static: true }) pop!: PopoverComponent;
}

@Component({
  standalone: true,
  imports: [GrossNetMeterComponent],
  template: `<hf-gross-net-meter [longPct]="60" [shortPct]="40" />`,
})
class MeterHarness {}

describe('P4 WS-DA-9 · axe structural-rule regression lock', () => {
  afterEach(() => {
    document.querySelectorAll('[data-axe-host]').forEach((n) => n.remove());
  });

  it('closed popover host exposes no empty role="tooltip" (WS-DA-1)', async () => {
    await TestBed.configureTestingModule({ imports: [PopoverHostHarness] }).compileComponents();
    const fixture = TestBed.createComponent(PopoverHostHarness);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    host.setAttribute('data-axe-host', '');
    document.body.appendChild(host);

    // The host-role strip (afterNextRender) must leave no role on <hf-popover>.
    const popoverHost = host.querySelector('hf-popover')!;
    expect(popoverHost.getAttribute('role')).toBeNull();

    const violations = await axeStructural(host);
    expect(violations.map((v) => v.id)).toEqual([]);
  });

  it('gross/net meter is a named image, not a roleless aria-label (WS-DA-3)', async () => {
    await TestBed.configureTestingModule({ imports: [MeterHarness] }).compileComponents();
    const fixture = TestBed.createComponent(MeterHarness);
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    host.setAttribute('data-axe-host', '');
    document.body.appendChild(host);

    const meter = host.querySelector('.meter')!;
    expect(meter.getAttribute('role')).toBe('img');
    expect(meter.getAttribute('aria-label')).toContain('Long 60%, Short 40%');

    const violations = await axeStructural(host);
    expect(violations.map((v) => v.id)).toEqual([]);
  });
});
