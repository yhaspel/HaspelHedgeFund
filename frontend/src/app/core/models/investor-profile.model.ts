// P3-prereq-5 — investor-profile API types.

export type AnalysisStatus = 'pending' | 'running' | 'done' | 'failed';
export type ResponseSource = 'questionnaire' | 'tuned';
export type RiskBand =
  | 'conservative'
  | 'moderate_conservative'
  | 'moderate'
  | 'moderate_aggressive'
  | 'aggressive';
export type HorizonBand = 'short' | 'medium' | 'long';
export type PatienceBand = 'low' | 'medium' | 'high';
export type StrategyKind =
  | 'long_only'
  | 'short_only'
  | 'long_short'
  | 'market_neutral'
  | 'concentrated_long'
  | 'sector_rotation'
  | 'global_macro'
  | 'risk_parity'
  | 'pairs';
export type StrategyFit = 'strong' | 'good' | 'consider';

export interface StrategyRecommendation {
  kind: StrategyKind;
  fit: StrategyFit;
  rationale: string;
}

export interface ProfileAnalysis {
  investor_type: string;
  risk_band: RiskBand;
  horizon_band: HorizonBand;
  patience_band: PatienceBand;
  behavioral_traits: string[];
  key_constraints: string[];
  summary: string;
  insights: string[];
  recommended_strategies: StrategyRecommendation[];
  agent_brief: string;
}

export interface QuestionnaireResponse {
  id: number;
  source: ResponseSource;
  derived_from: number | null;
  schema_version: number;
  answers: Record<string, unknown>;
  created_at: string;
  model_id: string;
  analysis_status: AnalysisStatus;
  analyzed_at: string | null;
  error_message: string;
  analysis: ProfileAnalysis | Record<string, never>;
  profile_summary: string;
  agent_brief: string;
  investor_type: string;
}

export interface QuestionnaireHistoryItem {
  id: number;
  source: ResponseSource;
  created_at: string;
  model_id: string;
  analysis_status: AnalysisStatus;
  investor_type: string;
}

export interface InvestorProfileState {
  apply_to_runs: boolean;
  nudge_dismiss_count: number;
  nudge_last_dismissed_at: string | null;
  updated_at: string;
}

export interface NudgeInfo {
  due: boolean;
  form: 'modal' | 'banner' | null;
}

export interface ProfileBundleUser {
  id: number;
  email: string;
  joined_at: string | null;
}

export interface ProfileBundleLatest {
  id: number;
  status: AnalysisStatus;
  source: ResponseSource;
  created_at: string;
  error_message: string;
}

export interface ProfileBundle {
  user: ProfileBundleUser;
  has_questionnaire: boolean;
  active: QuestionnaireResponse | null;
  latest: ProfileBundleLatest | null;
  state: InvestorProfileState;
  nudge: NudgeInfo;
  schema_version: number;
}

export interface QuestionnaireQuestion {
  id: string;
  label: string;
  type: 'single' | 'multi' | 'text' | 'tickers';
  required: boolean;
  options?: string[];
  max_len?: number;
  max_select?: number;
  hint?: string;
}

export interface QuestionnaireSection {
  id: string;
  title: string;
  questions: QuestionnaireQuestion[];
}

export interface QuestionnaireSchema {
  schema_version: number;
  sections: QuestionnaireSection[];
}

export const STRATEGY_KIND_LABEL: Record<StrategyKind, string> = {
  long_only: 'Long-only',
  short_only: 'Short-only',
  long_short: 'Long / Short',
  market_neutral: 'Market-neutral',
  concentrated_long: 'Concentrated long-only',
  sector_rotation: 'Sector / Theme rotation',
  global_macro: 'Global macro (ETF)',
  risk_parity: 'Risk-parity',
  pairs: 'Pairs trading',
};

export const STRATEGY_KIND_GUIDE_SLUG: Record<StrategyKind, string> = {
  long_only: 'long-only',
  short_only: 'short-only',
  long_short: 'long-short',
  market_neutral: 'market-neutral',
  concentrated_long: 'concentrated-long',
  sector_rotation: 'sector-rotation',
  global_macro: 'global-macro',
  risk_parity: 'risk-parity',
  pairs: 'pairs-trading',
};

export const RISK_BAND_LABEL: Record<RiskBand, string> = {
  conservative: 'Conservative',
  moderate_conservative: 'Moderately conservative',
  moderate: 'Moderate',
  moderate_aggressive: 'Moderately aggressive',
  aggressive: 'Aggressive',
};

export const HORIZON_BAND_LABEL: Record<HorizonBand, string> = {
  short: 'Short (<3 yrs)',
  medium: 'Medium (3–10 yrs)',
  long: 'Long (10+ yrs)',
};

export const PATIENCE_BAND_LABEL: Record<PatienceBand, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
};
