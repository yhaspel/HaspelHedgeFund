import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { TickerComponent } from './ticker.component';
import { TickerHistoryStore } from '../../abstraction/ticker-history.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';

/**
 * Spec for hf-ticker — verifies the lazy-fetch contract and the
 * hover/focus popover-open behaviour. Positioning + ARIA wiring is
 * covered separately by popover.component.spec.ts.
 *
 *   • First hover/focus triggers exactly one profile + history fetch.
 *   • Subsequent hovers/focuses do NOT re-fetch (uses the store's cache).
 *   • Both pointer and keyboard interactions open the popover (parity).
 *   • `disablePopover` short-circuits everything (fetch + popover).
 */

class FakeProfileStore {
  readonly _bump = signal(0);
  fetchProfileCalls: string[] = [];
  fetchProfile(t: string) {
    this.fetchProfileCalls.push(t);
    return of(null);
  }
  // Stubbed methods the popover body component may invoke; unused here.
  profile(_t: string) { return null; }
  name(_t: string) { return null; }
}

class FakeHistoryStore {
  readonly _bump = signal(0);
  fetchCalls: { ticker: string; days: number }[] = [];
  fetch(ticker: string, days: number) {
    this.fetchCalls.push({ ticker, days });
    return of([] as number[]);
  }
  closes(_t: string): number[] | null { return null; }
}

@Component({
  standalone: true,
  imports: [TickerComponent],
  template: `<hf-ticker [ticker]="ticker" [disablePopover]="disablePopover"></hf-ticker>`,
})
class HarnessComponent {
  ticker = 'AAPL';
  disablePopover = false;
}

function makeHarness(setup?: (h: HarnessComponent) => void): {
  fixture: ComponentFixture<HarnessComponent>;
  profileStore: FakeProfileStore;
  historyStore: FakeHistoryStore;
  trigger: HTMLElement;
  ticker: TickerComponent;
} {
  const fixture = TestBed.createComponent(HarnessComponent);
  if (setup) setup(fixture.componentInstance);
  fixture.detectChanges();
  const profileStore = TestBed.inject(TickerProfileStore) as unknown as FakeProfileStore;
  const historyStore = TestBed.inject(TickerHistoryStore) as unknown as FakeHistoryStore;
  const trigger = fixture.nativeElement.querySelector('.hf-tk-sym') as HTMLElement;
  const ticker = fixture.debugElement.children[0].componentInstance as TickerComponent;
  return { fixture, profileStore, historyStore, trigger, ticker };
}

describe('hf-ticker · lazy fetch + popover triggers', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarnessComponent],
      providers: [
        { provide: TickerProfileStore, useClass: FakeProfileStore },
        { provide: TickerHistoryStore, useClass: FakeHistoryStore },
      ],
    }).compileComponents();
  });

  it('does not fetch until the user interacts with the trigger', () => {
    const { profileStore, historyStore } = makeHarness();
    expect(profileStore.fetchProfileCalls).toEqual([]);
    expect(historyStore.fetchCalls).toEqual([]);
  });

  it('fetches profile + history exactly once on first hover', () => {
    const { profileStore, historyStore, ticker } = makeHarness();
    ticker.onEnter();
    expect(profileStore.fetchProfileCalls).toEqual(['AAPL']);
    expect(historyStore.fetchCalls).toEqual([{ ticker: 'AAPL', days: 60 }]);
  });

  it('fetches exactly once when the user focuses via keyboard', () => {
    const { profileStore, historyStore, ticker } = makeHarness();
    ticker.onFocus();
    expect(profileStore.fetchProfileCalls).toEqual(['AAPL']);
    expect(historyStore.fetchCalls).toEqual([{ ticker: 'AAPL', days: 60 }]);
  });

  it('does NOT re-fetch on subsequent hovers/focuses', () => {
    const { profileStore, historyStore, ticker } = makeHarness();
    ticker.onEnter();
    ticker.onLeave();
    ticker.onEnter();
    ticker.onFocus();
    ticker.onBlur();
    ticker.onFocus();
    expect(profileStore.fetchProfileCalls).toEqual(['AAPL']);
    expect(historyStore.fetchCalls.length).toBe(1);
  });

  it('opens the popover on hover', () => {
    const { ticker } = makeHarness();
    expect(ticker.pop.open()).toBe(false);
    ticker.onEnter();
    expect(ticker.pop.open()).toBe(true);
  });

  it('opens the popover on keyboard focus (not just hover)', () => {
    const { ticker } = makeHarness();
    ticker.onFocus();
    expect(ticker.pop.open()).toBe(true);
  });

  it('schedules close on leave + blur (debounced via popover.maybeHide)', () => {
    vi.useFakeTimers();
    try {
      const { ticker } = makeHarness();
      ticker.onEnter();
      expect(ticker.pop.open()).toBe(true);
      ticker.onLeave();
      vi.advanceTimersByTime(200);
      expect(ticker.pop.open()).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('disablePopover short-circuits both fetch and popover', () => {
    const { profileStore, historyStore, ticker } = makeHarness((h) => { h.disablePopover = true; });
    ticker.onEnter();
    ticker.onFocus();
    expect(profileStore.fetchProfileCalls).toEqual([]);
    expect(historyStore.fetchCalls).toEqual([]);
    expect(ticker.pop.open()).toBe(false);
  });

  it('uppercases the ticker input', () => {
    const { ticker } = makeHarness((h) => { h.ticker = 'msft'; });
    expect(ticker.ticker).toBe('MSFT');
  });

  it('ignores onEnter/onFocus when ticker is empty', () => {
    const { profileStore, ticker } = makeHarness((h) => { h.ticker = ''; });
    ticker.onEnter();
    ticker.onFocus();
    expect(profileStore.fetchProfileCalls).toEqual([]);
  });

  it('renders the ticker symbol in the trigger', () => {
    const { trigger } = makeHarness();
    expect(trigger.textContent?.trim()).toBe('AAPL');
  });

  it('exposes role="button" + aria-describedby only when popover is enabled', () => {
    const { trigger, ticker, fixture } = makeHarness();
    expect(trigger.getAttribute('role')).toBe('button');
    expect(trigger.getAttribute('aria-describedby')).toBeNull();
    ticker.onEnter();
    fixture.detectChanges();
    expect(trigger.getAttribute('aria-describedby')).toMatch(/^hf-pop-/);
  });

  it('omits role="button" when disablePopover=true', () => {
    const { trigger } = makeHarness((h) => { h.disablePopover = true; });
    expect(trigger.getAttribute('role')).toBeNull();
  });
});
