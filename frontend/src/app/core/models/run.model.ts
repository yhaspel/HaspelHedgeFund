export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

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
  action: 'buy' | 'hold' | 'sell';
  confidence: number;
  rationale: string;
  dissenting_views: DissentingPersona[];
  target_quantity: string;
  target_weight_pct: string;
  risk_overrides: RiskOverrides;
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

export interface RunSummary {
  id: number;
  tickers: string[];
  status: RunStatus;
  as_of_date: string;
  created_at: string;
  finished_at: string | null;
  total_cost_usd: string;
}

export interface RunDetail extends RunSummary {
  model_overrides: Record<string, string>;
  personas: string[];
  agent_versions: Record<string, string>;
  error_message: string;
  messages: AgentMessage[];
  decisions: DecisionRow[];
  llm_calls: LLMCallRow[];
}

export interface CreateRunRequest {
  tickers: string[];
  as_of_date: string;
  model_overrides?: Record<string, string>;
  personas?: string[];
}

export const ALL_PERSONAS: { id: string; name: string; optional?: boolean }[] = [
  { id: 'buffett', name: 'Warren Buffett' },
  { id: 'munger', name: 'Charlie Munger' },
  { id: 'graham', name: 'Benjamin Graham' },
  { id: 'wood', name: 'Cathie Wood' },
  { id: 'druckenmiller', name: 'Stanley Druckenmiller' },
  { id: 'burry', name: 'Michael Burry', optional: true },
  { id: 'damodaran', name: 'Aswath Damodaran', optional: true },
  { id: 'lynch', name: 'Peter Lynch', optional: true },
];

export const PERSONA_IDS = new Set(ALL_PERSONAS.map((p) => p.id));
export const DEFAULT_PERSONA_IDS = ALL_PERSONAS.filter((p) => !p.optional).map((p) => p.id);
