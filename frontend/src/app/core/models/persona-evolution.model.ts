/** P3-D — frontend types for the persona-evolution API. */

export type EvolutionCadence = 'off' | 'daily' | 'weekly' | 'monthly';

export interface PersonaEvolutionSettings {
  enabled: boolean;
  cadence: EvolutionCadence;
  model_id: string;
  web_search_enabled: boolean;
  monthly_cost_cap_usd: string;
  cost_cap_reached_at: string | null;
  updated_at: string;
  month_to_date_cost_usd?: string;
}

export interface PersonaEvolutionRevision {
  id: number;
  seq: number;
  as_of_date: string;
  market_stance_md: string;
  general_notes_md: string;
  composite_markdown: string;
  char_count: number;
  over_budget: boolean;
  material_change: boolean;
  dropped_facts: string[];
  source_urls: string[];
  model_id: string;
  created_at: string;
}

export interface PersonaEvolutionProfile {
  persona_name: string;
  display_name: string;
  firm_name: string;
  is_evolvable: boolean;
  lifecycle_note: string;
  search_aliases: string[];
  last_cycle_at: string | null;
  last_cycle_status: 'never' | 'ok' | 'skipped' | 'failed';
  last_cycle_note: string;
  current_revision: PersonaEvolutionRevision | null;
  updated_at: string;
}

export interface PersonaEvolutionRunResult {
  ran: string[];
  skipped: string[];
  failed: string[];
  reason?: string;
}
