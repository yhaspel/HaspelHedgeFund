import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { apiErrorMessage, apiFieldErrors } from './api-error';

/**
 * WP F1 — one shared flattener for DRF failures.
 *
 * Before this, pages read `err?.error?.detail` (which DRF field errors do not
 * have) or `JSON.stringify(err.error)` (which dumps `{"step_days":["…"]}` at
 * the user). Both hid the one sentence that says what to change.
 */
const err = (status: number, body: unknown) =>
  new HttpErrorResponse({ status, statusText: 'x', error: body, url: '/api/x' });

describe('fix-f1 · apiErrorMessage', () => {
  it('prefers a plain {detail}', () => {
    expect(
      apiErrorMessage(
        err(400, { detail: 'Projected cost $6.10 exceeds your per-run ceiling $4.00 (Settings › Models).' }),
        'nope',
      ),
    ).toBe('Projected cost $6.10 exceeds your per-run ceiling $4.00 (Settings › Models).');
  });

  it('flattens a single DRF field error', () => {
    expect(apiErrorMessage(err(400, { tickers: ['contains an empty entry'] }), 'nope')).toBe(
      'Tickers: contains an empty entry',
    );
  });

  it('flattens several field errors, and inlines non_field_errors', () => {
    const msg = apiErrorMessage(
      err(400, {
        step_days: ['must be >= 1'],
        starting_cash: ['must be > 0'],
        non_field_errors: ['master window too short for one fold'],
      }),
      'nope',
    );
    expect(msg).toContain('Step days: must be >= 1');
    expect(msg).toContain('Starting cash: must be > 0');
    expect(msg).toContain('master window too short for one fold');
    expect(msg).not.toContain('Non field errors');
    expect(msg).not.toContain('{');
  });

  it('handles a bare string body and a list body', () => {
    expect(apiErrorMessage(err(400, 'plain message'), 'nope')).toBe('plain message');
    expect(apiErrorMessage(err(400, ['a', 'b']), 'nope')).toBe('a b');
  });

  it('ignores an HTML error page from a proxy and falls back', () => {
    expect(apiErrorMessage(err(502, '<html><body>Bad Gateway</body></html>'), 'fallback')).toBe(
      'fallback',
    );
  });

  it('explains a status-0 (network) failure rather than echoing the fallback', () => {
    expect(apiErrorMessage(err(0, null), 'fallback')).toContain('Could not reach the server');
  });

  it('falls back for an empty body and for a non-HTTP throw', () => {
    expect(apiErrorMessage(err(500, null), 'fallback')).toBe('fallback');
    expect(apiErrorMessage(new Error('boom'), 'fallback')).toBe('fallback');
  });

  it('accepts the plain {status, error} shape the stores throw in tests', () => {
    expect(apiErrorMessage({ status: 409, error: { detail: 'autopilot halted' } }, 'nope')).toBe(
      'autopilot halted',
    );
  });

  it('apiFieldErrors maps field → message and skips detail', () => {
    expect(
      apiFieldErrors(err(400, { detail: 'ignored', ticker: ['bad symbol'], qty: ['too big'] })),
    ).toEqual({ ticker: 'bad symbol', qty: 'too big' });
    expect(apiFieldErrors(err(400, 'plain'))).toEqual({});
  });
});
