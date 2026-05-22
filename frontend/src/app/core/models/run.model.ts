export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

/** P2l: ad-hoc Run Console runs vs strategy-cycle-sourced runs. */
export type RunSource = 'adhoc' | 'strategy';

/** P2l: covers single-name + pair + sector + short-side actions emitted by
 * the council across all strategy flavors. */
export type DecisionAction =
  | 'buy'
  | 'sell'
  | 'hold'
  | 'open_short'
  | 'cover_short'
  | 'enter'
  | 'skip';

export interface ModelOption {
  id: string;
  name: string;
  tier: string;
  input_per_mtok: number;
  output_per_mtok: number;
}

export interface AgentMessage {
  id: number;
  agent_name: string;
  parsed_output: Record<string, unknown>;
  status: string;
  created_at: string;
}

export interface DissentingPersona {
  name: string;
  signal: 'bullish' | 'neutral' | 'bearish';
  confidence: number;
  thesis_summary: string;
}

export interface RiskOverrides {
  hard_caps_applied?: string[];
  max_position_pct_for_this_trade?: number;
  stop_loss_pct?: number | null;
  veto?: boolean;
  rationale?: string;
}

export interface DecisionRow {
  id: number;
  ticker: string;
  action: DecisionAction;
  confidence: number;
  rationale: string;
  dissenting_views: DissentingPersona[];
  target_quantity: string;
  target_weight_pct: string;
  risk_overrides: RiskOverrides;
  /** P2l: 'long' / 'short' / 'sector' / 'pair'. */
  side?: string;
  /** Signed % of NAV — negative for shorts, positive for longs. */
  target_weight_signed?: string;
  created_at: string;
}

export interface LLMCallRow {
  id: number;
  agent_name: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  latency_ms: number;
  created_at: string;
}

export interface StrategyBacklink {
  portfolio_target_id: number;
  portfolio_target_status: string;
  as_of_date: string | null;
  strategy_id: number;
  strategy_name: string;
  strategy_kind: string;
}

export interface RunSummary {
  id: number;
  tickers: string[];
  status: RunStatus;
  as_of_date: string;
  created_at: string;
  finished_at: string | null;
  total_cost_usd: string;
  /** P2l: ad-hoc vs strategy-cycle-sourced. */
  source?: RunSource;
  /** P2l: parent PortfolioTarget id for strategy-sourced runs. */
  portfolio_target?: number | null;
  /** P2l: strategy + cycle metadata for back-linking; null for ad-hoc. */
  strategy_backlink?: StrategyBacklink | null;
}

/** P01 review: per-source provenance entry for run outputs. */
export interface EvidenceItem {
  agent: string;
  label: string;
  provider: string;
  source: string;
  url: string;
  as_of: string | null;
  retrieved_at: string | null;
}

export interface RunEvidence {
  as_of?: string | null;
  providers?: Record<string, string>;
  items?: EvidenceItem[];
  generated_at?: string | null;
}

/** P02a review: explicit label for whether sizing is illustrative. */
export interface RunRiskContext {
  mode?: 'stub' | 'real' | 'research_only';
  portfolio_id?: number | null;
  stub_nav_usd?: string | null;
  cash_balance_usd?: string;
  notes?: string;
}

export interface RunDetail extends RunSummary {
  model_overrides: Record<string, string>;
  personas: string[];
  agent_versions: Record<string, string>;
  error_message: string;
  messages: AgentMessage[];
  decisions: DecisionRow[];
  llm_calls: LLMCallRow[];
  /** P01 review: source/provider/timestamp trail behind the council's claims. */
  evidence?: RunEvidence;
  /** P02a review: whether sizing was stub/real/research-only. */
  risk_context?: RunRiskContext;
}

export interface CreateRunRequest {
  tickers: string[];
  as_of_date: string;
  model_overrides?: Record<string, string>;
  personas?: string[];
}

export interface PersonaMeta {
  id: string;
  name: string;
  initials: string;
  tagline: string;
  description: string;
  monogramVariant: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;
  optional?: boolean;
}

export const ALL_PERSONAS: PersonaMeta[] = [
  {
    id: 'buffett',
    name: 'Warren Buffett',
    initials: 'WB',
    tagline: 'quality moat',
    description:
      'Long-term owner of high-quality businesses with durable competitive moats, honest managers, and predictable cash flows bought at a fair price.',
    monogramVariant: 1,
  },
  {
    id: 'munger',
    name: 'Charlie Munger',
    initials: 'CM',
    tagline: 'mental models',
    description:
      'Multidisciplinary mental-models thinker. Favors a small number of great businesses, inverts to find what would make a thesis fail, and avoids stupidity over chasing brilliance.',
    monogramVariant: 2,
  },
  {
    id: 'graham',
    name: 'Benjamin Graham',
    initials: 'BG',
    tagline: 'deep value',
    description:
      'Father of value investing. Demands a margin of safety: pays well below conservative intrinsic value, leans on net-net balance-sheet protection, and treats Mr. Market as a moody business partner.',
    monogramVariant: 3,
  },
  {
    id: 'wood',
    name: 'Cathie Wood',
    initials: 'CW',
    tagline: 'disruptive growth',
    description:
      'Thematic growth investor focused on disruptive innovation — AI, genomics, robotics, energy storage, blockchain. Tolerates volatility for long-duration upside and platform S-curves.',
    monogramVariant: 4,
  },
  {
    id: 'druckenmiller',
    name: 'Stanley Druckenmiller',
    initials: 'SD',
    tagline: 'macro conviction',
    description:
      'Top-down macro generalist. Sizes high-conviction positions against the liquidity and rates cycle, then concentrates capital when the setup is asymmetric.',
    monogramVariant: 5,
  },
  {
    id: 'burry',
    name: 'Michael Burry',
    initials: 'MB',
    tagline: 'contrarian short',
    description:
      'Contrarian deep-value investor and short-seller. Reads filings for hidden risk, fades crowded narratives, and is willing to be early and uncomfortable.',
    monogramVariant: 6,
    optional: true,
  },
  {
    id: 'damodaran',
    name: 'Aswath Damodaran',
    initials: 'AD',
    tagline: 'intrinsic valuation',
    description:
      'Valuation-first analyst. Builds explicit DCF and relative-value stories with disciplined assumptions on growth, margins, reinvestment, and risk premia.',
    monogramVariant: 7,
    optional: true,
  },
  {
    id: 'lynch',
    name: 'Peter Lynch',
    initials: 'PL',
    tagline: 'know what you own',
    description:
      'Bottom-up stock picker who buys what he understands. Looks for growth at a reasonable price, ten-baggers in plain sight, and clean balance sheets backing the story.',
    monogramVariant: 8,
    optional: true,
  },
];

export function personaById(id: string): PersonaMeta | undefined {
  return ALL_PERSONAS.find((p) => p.id === id);
}

export function personaColorVar(id: string): string {
  const p = personaById(id);
  return p ? `var(--c${p.monogramVariant})` : 'var(--text-3)';
}

export const PERSONA_IDS = new Set(ALL_PERSONAS.map((p) => p.id));
export const DEFAULT_PERSONA_IDS = ALL_PERSONAS.filter((p) => !p.optional).map((p) => p.id);
