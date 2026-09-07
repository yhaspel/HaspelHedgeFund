import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AuthStore } from '../../abstraction/auth.store';
import { ModelsStore } from '../../abstraction/models.store';
import { User } from '../../core/models/user.model';
import { SettingsModelsPage } from './settings-models.page';

/**
 * WP F1 — `POST /api/models/fetch/` and `/api/models/verify-pricing/` are
 * staff-only (403 for everyone else), but Settings › Models offered both
 * buttons to every user and rendered the 403 as a bare "Fetch failed."
 *
 * `/me/` does not carry `is_staff` yet (see Cross-WP requests), so the gate is
 * built to degrade: flag absent → buttons stay, and the 403 is explained;
 * flag false → buttons are not rendered at all.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

function build(user: User | null, fetchResult = of({ models: [] })): Cmp {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      {
        provide: ModelsStore,
        useValue: {
          models: signal([]).asReadonly(),
          defaultsMap: signal({}).asReadonly(),
          prefs: signal(null).asReadonly(),
          loadAll: () => of({}),
          loadModels: () => of({ models: [] }),
          fetchOpenRouterModels: () => fetchResult,
          verifyPricing: () => fetchResult,
        },
      },
      { provide: AuthStore, useValue: { user: signal(user).asReadonly(), logout: vi.fn() } },
    ],
  });
  return TestBed.runInInjectionContext(() => new SettingsModelsPage());
}

const staff = (is_staff?: boolean): User => ({
  id: 1,
  email: 'a@b.com',
  date_joined: '2026-01-01',
  ...(is_staff === undefined ? {} : { is_staff }),
});

describe('fix-f1 · Settings › Models staff-only catalog actions', () => {
  it('hides the catalog actions from a user /me/ says is not staff', () => {
    expect(build(staff(false)).canManageCatalog()).toBe(false);
  });

  it('keeps them for staff', () => {
    expect(build(staff(true)).canManageCatalog()).toBe(true);
  });

  it('keeps them when /me/ does not expose the flag (nothing is hidden by mistake)', () => {
    expect(build(staff()).canManageCatalog()).toBe(true);
    expect(build(null).canManageCatalog()).toBe(true);
  });

  it('explains a 403 as "staff only" rather than "Fetch failed."', () => {
    const page = build(
      staff(),
      throwError(() => ({ status: 403, error: { detail: 'You do not have permission.' } })) as never,
    );
    page.fetchOpenRouter();
    expect(page.fetchMsg()).toContain('Only staff accounts');
    expect(page.fetchHasIssues()).toBe(true);
  });

  it('still shows the server message for a non-403 failure', () => {
    const page = build(
      staff(),
      throwError(() => ({ status: 502, error: { detail: 'OpenRouter is down' } })) as never,
    );
    page.verifyAllOpenRouter();
    expect(page.verifyMsg()).toBe('OpenRouter is down');
  });
});
