import { HttpErrorResponse } from '@angular/common/http';

/**
 * Turn any API failure into one line a human can act on.
 *
 * DRF answers a failed write in two shapes:
 *   • `{"detail": "Projected cost $6.10 exceeds your per-run ceiling $4.00 …"}`
 *   • `{"tickers": ["contains an empty entry"], "step_days": ["…"]}`  (field errors)
 *     — including the form-wide `non_field_errors` key.
 * Only the first has a `detail` key, so a page that reads `error.detail` alone
 * silently drops every serializer message and falls back to "Failed to …".
 * This flattens both (and `JSON.stringify` never reaches the user).
 */
export function apiErrorMessage(err: unknown, fallback: string): string {
  const body = err instanceof HttpErrorResponse ? err.error : (err as { error?: unknown })?.error;
  const flat = flattenErrorBody(body);
  if (flat) return flat;
  if (err instanceof HttpErrorResponse && err.status === 0) {
    return 'Could not reach the server. Check your connection and try again.';
  }
  return fallback;
}

/** Field-by-field messages for inline form errors: `{ ticker: 'must be …' }`. */
export function apiFieldErrors(err: unknown): Record<string, string> {
  const body = err instanceof HttpErrorResponse ? err.error : (err as { error?: unknown })?.error;
  if (!body || typeof body !== 'object' || Array.isArray(body)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
    if (key === 'detail') continue;
    const text = flattenValue(value);
    if (text) out[key] = text;
  }
  return out;
}

function flattenErrorBody(body: unknown): string {
  if (!body) return '';
  if (typeof body === 'string') {
    // A proxy/nginx HTML error page is noise, not a message.
    return body.trim().startsWith('<') ? '' : body.trim();
  }
  if (Array.isArray(body)) return flattenValue(body);
  if (typeof body !== 'object') return '';

  const record = body as Record<string, unknown>;
  const detail = flattenValue(record['detail']);
  if (detail) return detail;

  const parts: string[] = [];
  for (const [key, value] of Object.entries(record)) {
    const text = flattenValue(value);
    if (!text) continue;
    parts.push(key === 'non_field_errors' ? text : `${humanizeField(key)}: ${text}`);
  }
  return parts.join(' · ');
}

function flattenValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value.map(flattenValue).filter(Boolean).join(' ');
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => {
        const text = flattenValue(v);
        return text ? `${humanizeField(k)}: ${text}` : '';
      })
      .filter(Boolean)
      .join(' · ');
  }
  return '';
}

function humanizeField(key: string): string {
  const words = key.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
