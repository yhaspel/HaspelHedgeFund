import { describe, expect, it } from 'vitest';

import { STRATEGY_KIND_GUIDE } from './info.model';
import {
  STRATEGY_KIND_CLI_ONLY,
  STRATEGY_KIND_DESCRIPTIONS,
  STRATEGY_KIND_OPTIONS,
  StrategyKind,
  strategyKindLabel,
} from './strategy.model';

/**
 * WAVE 3 F2 item 5 — the three missing strategy kinds.
 *
 * `trend`, `sector_momentum` and `news_sentiment` (plus the `xsec_long_short`
 * scaffolding kind) exist in `PortfolioStrategy.KIND_CHOICES` server-side but
 * were absent from the frontend union, so a fund card holding one printed the
 * raw slug — "sector_momentum" — where a name belonged.
 *
 * The codes below are the exact ones in `backend/apps/portfolios/models.py`.
 */

const BACKEND_KINDS: StrategyKind[] = [
  'long_only',
  'short_only',
  'long_short',
  'market_neutral',
  'concentrated_long',
  'sector_rotation',
  'global_macro',
  'risk_parity',
  'pairs',
  'trend',
  'sector_momentum',
  'news_sentiment',
  'xsec_long_short',
];

describe('wave3-f2 · strategy kinds', () => {
  it('describes every kind the backend can store', () => {
    for (const kind of BACKEND_KINDS) {
      expect(STRATEGY_KIND_DESCRIPTIONS[kind], `missing description for ${kind}`).toBeTruthy();
    }
    expect(Object.keys(STRATEGY_KIND_DESCRIPTIONS).sort()).toEqual([...BACKEND_KINDS].sort());
  });

  it('names every stored kind — never renders a raw slug', () => {
    for (const kind of BACKEND_KINDS) {
      const label = strategyKindLabel(kind);
      expect(label, `label for ${kind}`).not.toBe(kind);
      expect(label).not.toContain('_');
    }
    expect(strategyKindLabel('sector_momentum')).toBe('Deterministic sector momentum');
    expect(strategyKindLabel('trend')).toBe('Deterministic trend (TSMOM)');
    expect(strategyKindLabel('news_sentiment')).toBe(
      'News-sentiment single-name (council overlay)',
    );
  });

  it('falls back to the raw value only for an unknown kind, and "—" for nothing', () => {
    expect(strategyKindLabel('a_brand_new_kind')).toBe('a_brand_new_kind');
    expect(strategyKindLabel(null)).toBe('—');
    expect(strategyKindLabel('')).toBe('—');
  });

  it('offers the three deterministic kinds for creation (the serializer accepts them)', () => {
    const offered = STRATEGY_KIND_OPTIONS.map((o) => o.value);
    expect(offered).toContain('trend');
    expect(offered).toContain('sector_momentum');
    expect(offered).toContain('news_sentiment');
  });

  it('does NOT offer the scaffolding kind — the §9 gate refuses to arm it', () => {
    const offered = STRATEGY_KIND_OPTIONS.map((o) => o.value);
    expect(offered).not.toContain('xsec_long_short');
    expect(STRATEGY_KIND_CLI_ONLY).toContain('xsec_long_short');
    // …but it is still labelled wherever an existing one shows up.
    expect(strategyKindLabel('xsec_long_short')).toContain('scaffolding');
  });

  it('keeps every offered option labelled and unique', () => {
    const values = STRATEGY_KIND_OPTIONS.map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
    for (const opt of STRATEGY_KIND_OPTIONS) expect(opt.label.length).toBeGreaterThan(3);
  });

  it('only links a guide for kinds that actually have an /info topic', () => {
    // A guide slug with no topic behind it would 404; the form hides the link.
    expect(STRATEGY_KIND_GUIDE['trend']).toBeUndefined();
    expect(STRATEGY_KIND_GUIDE['sector_momentum']).toBeUndefined();
    expect(STRATEGY_KIND_GUIDE['news_sentiment']).toBeUndefined();
    expect(STRATEGY_KIND_GUIDE['long_short']).toBe('strategy-long-short');
  });
});
