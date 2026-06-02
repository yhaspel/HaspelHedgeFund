// P4c agent-graph editor types. Mirrors the backend apps/graphs serializers.

export type NodeKind =
  | 'analytical'
  | 'persona'
  | 'risk'
  | 'portfolio'
  | 'cio'
  | 'structural';

/** Palette / locked-tail metadata from GET /graphs/registry/. */
export interface NodeSpec {
  agent_name: string;
  kind: NodeKind;
  state_key: string;
  model_selectable: boolean;
  editable: boolean;
  default_model_key: string | null;
  label: string;
}

export interface GraphRegistry {
  schema_version: number;
  analytical: NodeSpec[];
  personas: NodeSpec[];
  tail: NodeSpec[];
  structural: string[];
  notes: { macro: string; news_digest_min_context: number };
}

export interface GraphNode {
  id: string; // == type
  type: string;
  kind: NodeKind;
  position: { x: number; y: number };
  model_id?: string | null;
  config?: Record<string, unknown>;
}

export interface GraphEdge {
  from: string;
  to: string;
}

export interface AgentGraphVersion {
  id: number;
  graph: number;
  version: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  tail_models: Record<string, string>;
  validation_status: 'valid' | 'draft_invalid';
  created_at: string;
  created_by: number | null;
  notes: string;
  display_label: string;
}

export interface AgentGraphVersionRow {
  id: number;
  version: number;
  validation_status: 'valid' | 'draft_invalid';
  created_at: string;
  notes: string;
  display_label: string;
}

export interface AgentGraphSummary {
  id: number;
  name: string;
  description: string;
  is_template: boolean;
  owned: boolean;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  latest_version: AgentGraphVersionRow | null;
  version_count: number;
}

export interface ValidationIssue {
  rule: string;
  severity: 'error' | 'warning';
  message: string;
  node_id: string | null;
}

export interface ValidationResult {
  status: 'valid' | 'draft_invalid';
  is_valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

/** Draft payload sent to /validate/ and /versions/. */
export interface GraphDraft {
  nodes: GraphNode[];
  edges: GraphEdge[];
  tail_models: Record<string, string>;
  notes?: string;
}
