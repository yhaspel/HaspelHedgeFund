/**
 * Shared numeric display formatters (2026-07-03 app review §3.3).
 *
 * The three watchlist surfaces (dashboard card, screener panel, manager
 * page) each grew a private copy of price/Δ% formatting, and the manager's
 * copy shipped a raw-float Δ% bug. One source of truth instead.
 *
 * All formatters return an em-dash for null/undefined/non-finite input so
 * templates never render "NaN%" or "undefined".
 */

const DASH = '—';

/** 2-decimal price with thousands separators: 1234.5 → "1,234.50". */
export function formatPrice2dp(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return DASH;
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return DASH;
  return n.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * 2-decimal percentage: 1.2345678901 → "1.23%" (or "+1.23%" with
 * `signed`, for surfaces that don't already carry a ▲/▼ glyph).
 */
export function formatPct2(
  v: number | null | undefined,
  opts: { signed?: boolean } = {},
): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const sign = opts.signed && v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

/** Plain 2-decimal number (RVOL and friends): 1.5 → "1.50". */
export function formatNum2(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  return v.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Compact big-number: 1.23e12 → "1.23T", 4.5e9 → "4.50B", 6e6 → "6.00M". */
export function formatBigCompact(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return DASH;
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return DASH;
  if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + 'T';
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  return n.toLocaleString('en-US');
}

/**
 * The backend's ticker rule, mirrored client-side so the form can say what is
 * wrong before a round trip (apps/runs/serializers.py:175 —
 * `^[A-Z][A-Z0-9.\-]{0,15}$`, applied after strip + upper).
 */
export const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,15}$/;

/** Trim + uppercase, exactly as the serializer normalizes before matching. */
export function normalizeTicker(raw: string | null | undefined): string {
  return (raw ?? '').trim().toUpperCase();
}

/** null when valid, else the message to show under the field. */
export function tickerError(raw: string | null | undefined): string | null {
  const t = normalizeTicker(raw);
  if (!t) return 'Enter a ticker symbol.';
  if (/\s/.test(t)) return 'One symbol only — remove the spaces.';
  if (!TICKER_RE.test(t)) {
    return 'Use letters, digits, “.” or “-”, starting with a letter (max 16 characters).';
  }
  return null;
}

/**
 * Exposure fields (`target_gross_pct`, `target_net_pct`, PortfolioTarget's
 * `gross_pct` / `net_pct`) are FRACTIONS of NAV — 1.50 means 150%, not 1.5%.
 * Several surfaces printed the raw fraction under a "Gross / Net" heading (or
 * next to a "%"), so a 150%-gross book read as "1.50". Convert exactly once.
 */
export function formatExposurePct(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return DASH;
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return DASH;
  return `${(n * 100).toFixed(0)}%`;
}

/** Same conversion with one decimal, for cycle-level actuals. */
export function formatExposurePct1(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return DASH;
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return DASH;
  return `${(n * 100).toFixed(1)}%`;
}
