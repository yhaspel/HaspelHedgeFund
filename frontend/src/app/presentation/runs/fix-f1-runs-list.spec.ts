import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthStore } from '../../abstraction/auth.store';
import { RunsStore } from '../../abstraction/runs.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { RunStatus, RunSummary } from '../../core/models/run.model';
import { RunsListPage } from './runs-list.page';

/**
 * WP F1 — two runs-list defects with no proof spec of their own:
 *
 *  1. a failed GET /runs/ rendered "You haven't started any runs yet." — a 5xx
 *     was indistinguishable from an empty account and there was no retry;
 *  2. the whole table row navigated on click but was not focusable and had no
 *     keyboard activation or accessible name, so the list was unusable without
 *     a mouse.
 */
function mkRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: 1,
    tickers: ['AAPL'],
    status: 'done' as RunStatus,
    as_of_date: '2026-05-22',
    created_at: '2026-05-22T10:00:00Z',
    finished_at: '2026-05-22T10:05:00Z',
    total_cost_usd: '0.0500',
    source: 'adhoc',
    ...overrides,
  };
}

class FakeRunsStore {
  readonly _runs = signal<RunSummary[]>([]);
  readonly runs = this._runs.asReadonly();
  readonly runsCount = signal(0).asReadonly();
  listRuns = vi.fn((_opts?: unknown) => of(this._runs()));
}

function build(store: FakeRunsStore) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [RunsListPage],
    providers: [
      provideRouter([]),
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: RunsStore, useValue: store },
      { provide: TickerProfileStore, useValue: { fetchNames: () => of({}) } },
      { provide: AuthStore, useValue: { user: signal(null), logout: () => undefined } },
    ],
  });
  const fixture = TestBed.createComponent(RunsListPage);
  // Keep the real Router (routerLink in the template needs it) and watch the
  // one call the row makes.
  const navigate = vi
    .spyOn(TestBed.inject(Router), 'navigate')
    .mockImplementation(() => Promise.resolve(true));
  return { fixture, page: fixture.componentInstance, navigate };
}

describe('fix-f1 · RunsListPage error state', () => {
  let store: FakeRunsStore;

  beforeEach(() => {
    store = new FakeRunsStore();
  });

  it('renders an error card with the server message instead of the empty state', () => {
    store.listRuns = vi.fn(() =>
      throwError(() => ({ status: 503, error: { detail: 'runs service unavailable' } })),
    );
    const { fixture, page } = build(store);
    fixture.detectChanges();

    expect(page.loadError()).toBe('runs service unavailable');
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('[data-test="error-state"]')).not.toBeNull();
    expect(el.textContent).not.toContain("You haven't started any runs yet");
  });

  it('retries the fetch and clears the error on success', () => {
    let fail = true;
    store.listRuns = vi.fn(() =>
      fail
        ? throwError(() => ({ status: 500, error: { detail: 'boom' } }))
        : of(store._runs()),
    );
    const { fixture, page } = build(store);
    fixture.detectChanges();
    expect(page.loadError()).toBe('boom');

    fail = false;
    page.reload();
    fixture.detectChanges();
    expect(page.loadError()).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="error-state"]')).toBeNull();
  });

  it('shows the true empty state when the fetch succeeds with no rows', () => {
    const { fixture, page } = build(store);
    fixture.detectChanges();
    expect(page.loadError()).toBeNull();
    expect(fixture.nativeElement.textContent).toContain("You haven't started any runs yet");
  });
});

describe('fix-f1 · RunsListPage keyboard-reachable rows', () => {
  it('gives every clickable row a tab stop, a link role and an accessible name', () => {
    const store = new FakeRunsStore();
    store._runs.set([mkRun({ id: 42, tickers: ['AAPL', 'MSFT'], status: 'failed' as RunStatus })]);
    const { fixture, page } = build(store);
    fixture.detectChanges();

    const row = fixture.nativeElement.querySelector('[data-test="run-row-42"]') as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.getAttribute('tabindex')).toBe('0');
    expect(row.getAttribute('role')).toBe('link');
    expect(row.getAttribute('aria-label')).toBe('Run #42, AAPL, MSFT, failed, 2026-05-22');
    expect(page.rowLabel(mkRun({ id: 7, tickers: [] }))).toContain('no tickers');
  });

  it('opens the run on Enter and on Space', () => {
    const store = new FakeRunsStore();
    store._runs.set([mkRun({ id: 42 })]);
    const { page, navigate } = build(store);
    const row = mkRun({ id: 42 });

    page.onRowKeydown(new KeyboardEvent('keydown', { key: 'Enter' }), row);
    page.onRowKeydown(new KeyboardEvent('keydown', { key: ' ' }), row);
    expect(navigate).toHaveBeenCalledTimes(2);
    expect(navigate).toHaveBeenCalledWith(['/runs', 42]);
  });

  it('ignores other keys and keys aimed at a control inside the row', () => {
    const store = new FakeRunsStore();
    store._runs.set([mkRun({ id: 42, status: 'failed' as RunStatus })]);
    const { fixture, page, navigate } = build(store);
    fixture.detectChanges();
    const row = mkRun({ id: 42 });

    page.onRowKeydown(new KeyboardEvent('keydown', { key: 'a' }), row);
    expect(navigate).not.toHaveBeenCalled();

    // Enter on the nested "Rerun" button must not also navigate to the row.
    const btn = fixture.nativeElement.querySelector('[data-test="rerun-42"]') as HTMLElement;
    expect(btn).not.toBeNull();
    const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
    Object.defineProperty(ev, 'target', { value: btn });
    Object.defineProperty(ev, 'currentTarget', { value: btn.closest('tr') });
    page.onRowKeydown(ev, row);
    expect(navigate).not.toHaveBeenCalled();
  });
});
