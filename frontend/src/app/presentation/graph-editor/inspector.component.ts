import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ModelsStore } from '../../abstraction/models.store';
import { NodeKind } from '../../core/models/graph.model';

export interface InspectorSelection {
  id: string;
  label: string;
  kind: NodeKind;
  selectable: boolean;
  editable: boolean;
  modelId: string | null;
  defaultModelKey: string | null;
  isMacro: boolean;
  isNewsDigest: boolean;
  isTail: boolean;
}

/** Right-rail inspector: selected node config + single-node model dropdown
 *  (model_selectable nodes only). Reuses ModelsStore for the catalog. */
@Component({
  selector: 'hf-graph-inspector',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (selection(); as sel) {
      <div class="inspector">
        <div class="ins-hd">
          <span class="kind kind-{{ sel.kind }}">{{ sel.kind }}</span>
          <h3>{{ sel.label }}</h3>
        </div>

        @if (sel.kind === 'portfolio') {
          <p class="note">The Portfolio Manager is deterministic — it aggregates the
            council's signals in code and has no model to configure.</p>
        } @else if (sel.selectable) {
          <label class="fld">
            <span class="lbl">Model</span>
            <select
              [value]="sel.modelId || ''"
              (change)="onModel($event)"
              data-testid="inspector-model"
            >
              <option value="">Default ({{ shortName(sel.defaultModelKey) }})</option>
              @for (m of options(); track m.id) {
                <option [value]="m.id" [disabled]="!m.available">{{ m.display_name }}</option>
              }
            </select>
          </label>

          @if (sel.isMacro) {
            <p class="note macro">{{ macroNote() }}</p>
          }
          @if (sel.isNewsDigest) {
            <p class="note">news_digest packs ~40 news items + a 10-K excerpt — pick a
              model with ≥ {{ minContext().toLocaleString() }}-token context to avoid truncation.</p>
          }
        }

        @if (sel.isTail) {
          <p class="note locked">🔒 Risk Manager, Portfolio Manager and CIO are a fixed
            terminal tail — you can set models (for Risk &amp; CIO) but can't move, delete
            or rewire them.</p>
        }

        @if (sel.editable) {
          <button type="button" class="del" (click)="remove.emit(sel.id)" data-testid="inspector-delete">
            Remove node
          </button>
        }
      </div>
    } @else {
      <div class="inspector empty">
        <p>Select a node to configure its model.</p>
      </div>
    }
  `,
  styles: [
    `
      .inspector { padding: 14px; }
      .inspector.empty { color: var(--text-dim, #93a0b5); font-size: 12.5px; }
      .ins-hd { margin-bottom: 12px; }
      .kind { font-size: 10px; text-transform: uppercase; letter-spacing: .05em;
              padding: 2px 7px; border-radius: 999px; color: #fff; }
      .kind-persona { background: #7a52c4; } .kind-analytical { background: #2f8d76; }
      .kind-risk, .kind-cio, .kind-portfolio { background: #c47f2f; }
      h3 { margin: 8px 0 0; font-size: 15px; color: var(--text, #e6ebf5); }
      .fld { display: flex; flex-direction: column; gap: 5px; margin-top: 8px; }
      .lbl { font-size: 11px; color: var(--text-dim, #93a0b5); }
      select { width: 100%; padding: 7px 8px; border-radius: 7px;
               border: 1px solid var(--border, #2a3142); background: var(--surface-2, #0e1117);
               color: var(--text, #e6ebf5); font-size: 12.5px; }
      .note { font-size: 11.5px; color: var(--text-dim, #93a0b5); line-height: 1.45; margin: 10px 0 0; }
      .note.macro { border-left: 2px solid #f0a23b; padding-left: 8px; }
      .note.locked { border-left: 2px solid #c47f2f; padding-left: 8px; }
      .del { margin-top: 16px; width: 100%; padding: 8px; border-radius: 7px;
             border: 1px solid #6b2730; background: transparent; color: #f08a8a;
             cursor: pointer; font-size: 12.5px; }
      .del:hover { background: #2a1518; }
    `,
  ],
})
export class GraphInspectorComponent {
  private readonly modelsStore = inject(ModelsStore);

  readonly selection = input<InspectorSelection | null>(null);
  readonly macroNote = input<string>('');
  readonly minContext = input<number>(32000);

  readonly modelChange = output<{ id: string; modelId: string | null }>();
  readonly remove = output<string>();

  readonly options = computed(() => {
    const sel = this.selection();
    let models = this.modelsStore.models();
    if (sel?.isNewsDigest) {
      const min = this.minContext();
      models = models.filter((m) => !m.context_window || m.context_window >= min);
    }
    return models;
  });

  shortName(key: string | null): string {
    if (!key) return 'platform default';
    const m = this.modelsStore.models().find((x) => x.id === key);
    return m ? m.display_name : key.split(':').slice(1).join(':') || key;
  }

  onModel(ev: Event): void {
    const sel = this.selection();
    if (!sel) return;
    const value = (ev.target as HTMLSelectElement).value;
    this.modelChange.emit({ id: sel.id, modelId: value || null });
  }
}
