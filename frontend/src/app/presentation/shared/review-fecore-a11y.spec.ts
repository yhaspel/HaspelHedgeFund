/**
 * Review (fecore) — structural a11y checks on the shared dialog primitives.
 *
 * `it.fails` = confirmed defect (assertion states the correct behaviour).
 */
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import axe from 'axe-core';
import { afterEach, describe, expect, it } from 'vitest';

import { CommandPaletteComponent } from './command-palette.component';
import { TickerComponent } from './ticker.component';

async function violations(node: Element, rules: string[]) {
  const result = await axe.run(node as HTMLElement, {
    runOnly: { type: 'rule', values: rules },
  });
  return result.violations;
}

@Component({
  standalone: true,
  imports: [TickerComponent],
  template: `
    <table>
      <tr>
        <td><hf-ticker ticker="AAPL" [disablePopover]="true" /> Apple Inc.</td>
        <td><hf-ticker ticker="MSFT" [disablePopover]="true" /> Microsoft</td>
      </tr>
    </table>
  `,
})
class NameColumnHarness {}

describe('review-fecore: shared dialog / ticker a11y', () => {
  afterEach(() => {
    document.querySelectorAll('[data-fecore-host]').forEach((n) => n.remove());
  });

  it.fails('F: the ⌘K dialog must have an accessible name (aria-labelledby points at a missing id)', async () => {
    TestBed.configureTestingModule({
      imports: [CommandPaletteComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(CommandPaletteComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const host = document.createElement('div');
    host.setAttribute('data-fecore-host', '');
    host.appendChild(fixture.nativeElement);
    document.body.appendChild(host);

    const dialog = host.querySelector('[role="dialog"]')!;
    const labelledBy = dialog.getAttribute('aria-labelledby')!;
    // modal.component.ts:72 defaults titleId to `hf-modal-title-N`; the palette
    // (command-palette.component.ts:103) never renders an element with that id.
    expect(document.getElementById(labelledBy)).not.toBeNull();
    expect(await violations(host, ['aria-dialog-name'])).toEqual([]);
  });

  it('evidence: with the popover disabled, hf-ticker still injects a focusable tab stop per cell', () => {
    TestBed.configureTestingModule({
      imports: [NameColumnHarness],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(NameColumnHarness);
    fixture.detectChanges();
    const stops = fixture.nativeElement.querySelectorAll('[tabindex="0"]');
    // ticker.component.ts:34 hard-codes tabindex="0"; on a 200-row screener /
    // ledger that is 200+ keyboard stops that open nothing and do nothing.
    expect(stops.length).toBe(2);
    stops.forEach((el: Element) => expect(el.getAttribute('role')).toBeNull());
  });
});
