import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { GraphRegistry, GraphNode } from '../../core/models/graph.model';
import { GraphValidationService } from './validation.service';

const REGISTRY: GraphRegistry = {
  schema_version: 1,
  analytical: [
    { agent_name: 'fundamentals', kind: 'analytical', state_key: 'fundamentals', model_selectable: true, editable: true, default_model_key: 'p:m', label: 'Fundamentals' },
    { agent_name: 'news_digest', kind: 'analytical', state_key: 'news_digest', model_selectable: true, editable: true, default_model_key: 'p:m', label: 'News Digest' },
  ],
  personas: [
    { agent_name: 'buffett', kind: 'persona', state_key: 'buffett', model_selectable: true, editable: true, default_model_key: 'p:m', label: 'Warren Buffett' },
  ],
  tail: [],
  structural: ['entry', 'analytical_join', 'persona_join'],
  notes: { macro: 'x', news_digest_min_context: 32000 },
};

function node(type: string, kind: 'analytical' | 'persona', model_id?: string): GraphNode {
  return { id: type, type, kind, position: { x: 0, y: 0 }, model_id, config: {} };
}

describe('GraphValidationService (client mirror)', () => {
  let svc: GraphValidationService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [GraphValidationService, provideHttpClient(), provideHttpClientTesting()],
    });
    svc = TestBed.inject(GraphValidationService);
  });

  it('is valid with at least one analytical + one persona', () => {
    const res = svc.validate(
      [node('fundamentals', 'analytical', 'p:m'), node('buffett', 'persona', 'p:m')],
      {},
      REGISTRY,
    );
    expect(res.is_valid).toBe(true);
    expect(res.errors).toHaveLength(0);
  });

  it('errors when no persona', () => {
    const res = svc.validate([node('fundamentals', 'analytical', 'p:m')], {}, REGISTRY);
    expect(res.is_valid).toBe(false);
    expect(res.errors.map((e) => e.rule)).toContain('no_persona');
  });

  it('errors when no analytical', () => {
    const res = svc.validate([node('buffett', 'persona', 'p:m')], {}, REGISTRY);
    expect(res.errors.map((e) => e.rule)).toContain('no_analytical');
  });

  it('errors on duplicate type', () => {
    const res = svc.validate(
      [node('fundamentals', 'analytical', 'p:m'), node('fundamentals', 'analytical', 'p:m'), node('buffett', 'persona', 'p:m')],
      {},
      REGISTRY,
    );
    expect(res.errors.map((e) => e.rule)).toContain('duplicate_type');
  });

  it('warns (not errors) when a selectable node has no model', () => {
    const res = svc.validate(
      [node('fundamentals', 'analytical'), node('buffett', 'persona', 'p:m')],
      {},
      REGISTRY,
    );
    expect(res.is_valid).toBe(true);
    expect(res.warnings.map((w) => w.rule)).toContain('missing_model_id');
  });
});
