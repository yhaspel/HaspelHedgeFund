export type RunStatus = 'queued' | 'running' | 'done' | 'failed';

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

export interface DecisionRow {
  id: number;
  ticker: string;
  action: 'buy' | 'hold' | 'sell';
  confidence: number;
  rationale: string;
  dissenting_views: string[];
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
  error_message: string;
  messages: AgentMessage[];
  decisions: DecisionRow[];
  llm_calls: LLMCallRow[];
}

export interface CreateRunRequest {
  tickers: string[];
  as_of_date: string;
  model_overrides?: Record<string, string>;
}
