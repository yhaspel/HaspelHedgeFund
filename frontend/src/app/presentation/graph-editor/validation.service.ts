import { Injectable, inject } from '@angular/core';
import { ModelsStore } from '../../abstraction/models.store';
import {
  GraphNode,
  GraphRegistry,
  ValidationIssue,
  ValidationResult,
} from '../../core/models/graph.model';

/**
 * Client-side mirror of the authoritative backend rules (apps/graphs/
 * validators.py) for instant feedback while editing. The backend /validate/
 * endpoint remains the source of truth at save time; this only covers the
 * node-set + model rules that matter for live UX. Edge rules can't fail
 * because the canvas auto-maintains the canonical wiring.
 */
@Injectable({ providedIn: 'root' })
export class GraphValidationService {
  private readonly models = inject(ModelsStore);

  validate(nodes: GraphNode[], tailModels: Record<string, string>, reg: GraphRegistry | null): ValidationResult {
    const errors: ValidationIssue[] = [];
    const warnings: ValidationIssue[] = [];

    const personas = nodes.filter((n) => n.kind === 'persona');
    const analytical = nodes.filter((n) => n.kind === 'analytical');

    if (personas.length === 0) {
      errors.push(issue('no_persona', 'error', 'Add at least one persona.'));
    }
    if (analytical.length === 0) {
      errors.push(issue('no_analytical', 'error', 'Add at least one analytical agent.'));
    }

    const seen = new Set<string>();
    for (const n of nodes) {
      if (seen.has(n.type)) {
        errors.push(issue('duplicate_type', 'error', `${n.type} appears more than once.`, n.id));
      }
      seen.add(n.type);
    }

    const selectable = new Set(
      [...(reg?.analytical ?? []), ...(reg?.personas ?? [])]
        .filter((s) => s.model_selectable)
        .map((s) => s.agent_name),
    );
    for (const n of nodes) {
      if (selectable.has(n.type) && !n.model_id) {
        warnings.push(
          issue('missing_model_id', 'warning', `${n.type} has no model; the default will be used.`, n.id),
        );
      }
    }

    const minCtx = reg?.notes?.news_digest_min_context ?? 32000;
    const nd = nodes.find((n) => n.type === 'news_digest');
    if (nd?.model_id) {
      const entry = this.models.models().find((m) => m.id === nd.model_id);
      if (entry && entry.context_window && entry.context_window < minCtx) {
        warnings.push(
          issue(
            'news_digest_small_context',
            'warning',
            `news_digest model context (${entry.context_window.toLocaleString()}) is below ${minCtx.toLocaleString()} and may truncate.`,
            nd.id,
          ),
        );
      }
    }

    return {
      status: errors.length ? 'draft_invalid' : 'valid',
      is_valid: errors.length === 0,
      errors,
      warnings,
    };
  }
}

function issue(rule: string, severity: 'error' | 'warning', message: string, node_id: string | null = null): ValidationIssue {
  return { rule, severity, message, node_id };
}
