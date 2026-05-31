export type ModelTier = 'frontier' | 'fast_cheap' | 'hosted_open' | 'local';

export interface ModelEntry {
  id: string;
  provider: string;
  display_name: string;
  tier: ModelTier;
  context_window: number;
  supports_caching: boolean;
  supports_structured_output: boolean;
  supports_long_context: boolean;
  price_in_per_mtok: string | number | null;
  price_out_per_mtok: string | number | null;
  notes: string;
  available: boolean;
  is_free?: boolean;
  last_verified_at?: string | null;
  last_verified_note?: string;
}

export interface VerifyPricingResult {
  model_id: string;
  ok: boolean;
  note: string;
  upstream_price_in_per_mtok: string | null;
  upstream_price_out_per_mtok: string | null;
  db_price_in_per_mtok: string | null;
  db_price_out_per_mtok: string | null;
}

export interface VerifyPricingResponse {
  results: VerifyPricingResult[];
  models: ModelEntry[];
}

export interface PresetResponse {
  preset: string;
  overrides: Record<string, string>;
  menu?: string[];
}

export interface FetchModelsExcluded {
  slug: string;
  reason: string;
}

export interface FetchModelsResponse {
  synced: string[];
  created: string[];
  deactivated: string[];
  excluded: FetchModelsExcluded[];
  fetched_at: string | null;
  models: ModelEntry[];
}

export interface AgentInfo {
  id: string;
  default_model: string;
  recommended_tier: ModelTier;
  group: 'persona' | 'analyst' | 'context' | 'orchestration' | 'other';
}

export const GROUP_LABEL: Record<string, string> = {
  persona: 'Personas',
  analyst: 'Analyst agents',
  context: 'Context agents',
  orchestration: 'Risk · Portfolio · CIO',
  other: 'Other',
};

export interface AgentsResponse {
  agents: AgentInfo[];
  presets: string[];
}

export interface ModelPreferences {
  preset: string;
  per_agent_defaults: Record<string, string>;
  cost_ceiling_per_run_usd: string | number | null;
}

export interface ProviderKeyStatus {
  anthropic: 'set' | 'unset';
  openrouter: 'set' | 'unset';
  openai: 'set' | 'unset';
  ollama_host: string;
  fmp: 'set' | 'unset';
  tiingo: 'set' | 'unset';
  fred: 'set' | 'unset';
  resend: 'set' | 'unset';
}

export const PRESET_NAMES = ['dev', 'research', 'quality', 'frugal', 'hybrid'] as const;
export type PresetName = (typeof PRESET_NAMES)[number];

export const AGENT_DISPLAY: Record<string, string> = {
  buffett: 'Buffett', munger: 'Munger', graham: 'Graham', wood: 'Wood',
  druckenmiller: 'Druckenmiller', burry: 'Burry', damodaran: 'Damodaran', lynch: 'Lynch',
  fundamentals: 'Fundamentals', technicals: 'Technicals',
  valuation: 'Valuation', sentiment: 'Sentiment',
  macro: 'Macro', news_digest: 'News digest',
  risk_manager: 'Risk Manager', portfolio_manager: 'Portfolio Manager', cio: 'CIO',
};

// Per-agent typical token estimates per single-ticker council pass. These are
// rough — recalibrate from observed LLMCall data each release.
export const AGENT_TOKEN_ESTIMATES: Record<string, { in: number; out: number }> = {
  buffett: { in: 10000, out: 1500 },
  munger: { in: 10000, out: 1500 },
  graham: { in: 10000, out: 1500 },
  wood: { in: 10000, out: 1500 },
  druckenmiller: { in: 10000, out: 1500 },
  burry: { in: 10000, out: 1500 },
  damodaran: { in: 10000, out: 1500 },
  lynch: { in: 10000, out: 1500 },
  fundamentals: { in: 7000, out: 800 },
  technicals: { in: 7000, out: 800 },
  valuation: { in: 8000, out: 1000 },
  sentiment: { in: 5000, out: 600 },
  macro: { in: 5000, out: 1000 },
  news_digest: { in: 15000, out: 1500 },
  risk_manager: { in: 15000, out: 1500 },
  portfolio_manager: { in: 20000, out: 1500 },
  cio: { in: 12000, out: 1000 },
};

export function estimateAgentCost(
  agentId: string,
  modelId: string | undefined,
  models: ModelEntry[],
): number {
  if (!modelId) return 0;
  const m = models.find((x) => x.id === modelId);
  if (!m) return 0;
  const est = AGENT_TOKEN_ESTIMATES[agentId] ?? { in: 5000, out: 500 };
  const pin = Number(m.price_in_per_mtok ?? 0);
  const pout = Number(m.price_out_per_mtok ?? 0);
  return (est.in * pin + est.out * pout) / 1_000_000;
}

export function estimateRunCost(
  agents: string[],
  overrides: Record<string, string>,
  defaults: Record<string, string>,
  models: ModelEntry[],
): number {
  return agents.reduce((acc, a) => {
    const mid = overrides[a] ?? defaults[a];
    return acc + estimateAgentCost(a, mid, models);
  }, 0);
}
