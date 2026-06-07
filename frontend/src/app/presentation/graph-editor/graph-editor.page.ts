import {
  ChangeDetectionStrategy,
  Component,
  HostListener,
  OnInit,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import {
  AgentGraphVersion,
  GraphEdge,
  GraphNode,
  ValidationResult,
} from '../../core/models/graph.model';
import { GraphCanvasComponent } from './graph-canvas.component';
import { GraphPaletteComponent } from './palette.component';
import { GraphInspectorComponent, InspectorSelection } from './inspector.component';
import { GraphValidationService } from './validation.service';
import { GraphVersionsDrawerComponent } from './versions-drawer.component';

@Component({
  selector: 'hf-graph-editor',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    AppShellComponent,
    GraphCanvasComponent,
    GraphPaletteComponent,
    GraphInspectorComponent,
    GraphVersionsDrawerComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Graphs', link: '/graphs' }, { label: graphName() || 'Editor' }]">
      <div class="editor">
        <div class="toolbar">
          <div class="tb-left">
            <strong class="gname">{{ graphName() }}</strong>
            @if (validation(); as v) {
              <span class="pill" [class.ok]="v.is_valid" [class.bad]="!v.is_valid" data-testid="validation-pill">
                {{ v.is_valid ? 'Valid' : v.errors.length + ' error' + (v.errors.length === 1 ? '' : 's') }}
              </span>
            }
            @if (savedLabel()) { <span class="saved">saved {{ savedLabel() }}</span> }
            @if (draftRestored()) {
              <span class="draft">restored unsaved draft
                <button type="button" (click)="discardDraft()">discard</button>
              </span>
            }
          </div>
          <div class="tb-right">
            <button type="button" class="btn ghost" (click)="exportJson()" data-testid="export-btn">Export</button>
            <label class="btn ghost" data-testid="import-btn">
              Import
              <input type="file" accept="application/json" hidden (change)="importJson($event)" />
            </label>
            <button type="button" class="btn ghost" (click)="versionsOpen.set(!versionsOpen())" data-testid="versions-btn">
              Versions
            </button>
            <button
              type="button"
              class="btn primary"
              [disabled]="!validation()?.is_valid || saving()"
              (click)="save()"
              data-testid="save-btn"
            >
              {{ saving() ? 'Saving…' : 'Save version' }}
            </button>
          </div>
        </div>

        <div class="tier-bar">
          <span class="tier-lbl">Set all models</span>
          <label class="tier-field">
            <span>Tier</span>
            <select [ngModel]="selectedTier()" (ngModelChange)="onTierChange($event)"
                    name="tier" data-testid="tier-select">
              <option [ngValue]="null">— pick a price tier —</option>
              @for (t of store.registry()?.tiers ?? []; track t.name) {
                <option [ngValue]="t.name">{{ t.label }}</option>
              }
            </select>
          </label>
          <label class="tier-field">
            <span>Model</span>
            <select [ngModel]="selectedTierModel()" (ngModelChange)="onTierModelChange($event)"
                    name="tierModel" [disabled]="!selectedTier()" data-testid="tier-model-select">
              @if (!selectedTier()) {
                <option [ngValue]="null">— select a tier first —</option>
              }
              @for (m of tierModelOptions(); track m.id) {
                <option [ngValue]="m.id">{{ m.label }}</option>
              }
            </select>
          </label>
          <span class="tier-hint">Applies one model to every persona + agent (overwrites per-node picks).</span>
        </div>

        @if (saveError()) {
          <div class="save-error" role="alert">{{ saveError() }}</div>
        }

        <div class="panes">
          <aside class="rail left">
            <hf-graph-palette [registry]="store.registry()" [present]="presentTypes()" (add)="addNode($event)" />
          </aside>

          <section class="canvas-wrap"
                   (dragover)="onDragOver($event)"
                   (drop)="onDrop($event)">
            <hf-graph-canvas
              [userNodes]="workingNodes()"
              [tail]="store.registry()?.tail ?? []"
              [tailModels]="tailModels()"
              [registry]="store.registry()"
              [selectedId]="selectedId()"
              (selectNode)="selectedId.set($event)"
              (moveNode)="onMove($event)"
            />
            @if (validation()?.warnings?.length) {
              <div class="warnings" data-testid="warnings">
                @for (w of validation()!.warnings; track w.rule + w.node_id) {
                  <div class="warn">⚠ {{ w.message }}</div>
                }
              </div>
            }
          </section>

          <aside class="rail right">
            <hf-graph-inspector
              [selection]="selection()"
              [macroNote]="store.registry()?.notes?.macro || ''"
              [minContext]="store.registry()?.notes?.news_digest_min_context || 32000"
              (modelChange)="onModelChange($event)"
              (remove)="onDelete($event)"
            />
          </aside>

          @if (versionsOpen()) {
            <hf-graph-versions-drawer
              [graphId]="graphId()"
              [currentVersion]="loadedVersion()?.version ?? null"
              (close)="versionsOpen.set(false)"
              (load)="onLoadVersion($event)"
            />
          }
        </div>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .editor { display: flex; flex-direction: column; height: calc(100vh - 124px); min-height: 540px; }
      .toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px;
                 padding: 6px 2px 10px; flex-wrap: wrap; }
      .tb-left { display: flex; align-items: center; gap: 10px; }
      .gname { font-size: 15px; color: var(--text, #e6ebf5); }
      .pill { font-size: 11px; padding: 2px 9px; border-radius: 999px; font-weight: 600; }
      .pill.ok { background: #163a2c; color: #5fd6a6; } .pill.bad { background: #3a1b1b; color: #f08a8a; }
      .saved, .draft { font-size: 11px; color: var(--text-3); }
      .draft button { margin-left: 4px; background: none; border: none; color: #5b8cff; cursor: pointer; font-size: 11px; }
      .tb-right { display: flex; gap: 8px; align-items: center; }
      .btn { padding: 7px 13px; border-radius: 8px; font-size: 12.5px; cursor: pointer; border: 1px solid var(--border, #2a3142); }
      .btn.ghost { background: var(--surface, #151b26); color: var(--text, #e6ebf5); }
      .btn.primary { background: var(--acc-long); color: #06231a; border-color: var(--acc-long); font-weight: 600; }
      .btn.primary:disabled { opacity: .45; cursor: not-allowed; }
      .save-error { background: #3a1b1b; color: #f6b3b3; padding: 8px 12px; border-radius: 8px;
                    font-size: 12.5px; margin-bottom: 8px; }
      .tier-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 8px 10px;
                  margin-bottom: 8px; border: 1px solid var(--border, #2a3142); border-radius: 9px;
                  background: var(--surface, #151b26); }
      .tier-lbl { font-size: 12px; font-weight: 600; color: var(--text, #e6ebf5); }
      .tier-field { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-3); }
      .tier-field select { padding: 6px 8px; border-radius: 7px; border: 1px solid var(--border, #2a3142);
                           background: var(--surface-2, #0e1117); color: var(--text, #e6ebf5); font-size: 12px; }
      .tier-field select:disabled { opacity: .5; }
      .tier-hint { font-size: 10.5px; color: var(--text-3); margin-left: auto; }
      .panes { flex: 1; display: grid; grid-template-columns: 210px 1fr 270px; gap: 10px; min-height: 0; position: relative; }
      .rail { border: 1px solid var(--border, #2a3142); border-radius: 10px; background: var(--surface, #151b26); overflow: hidden; }
      .canvas-wrap { position: relative; min-width: 0; }
      .warnings { position: absolute; left: 10px; bottom: 10px; max-width: 60%; display: flex; flex-direction: column; gap: 4px; }
      .warn { background: #2e2410; color: #e8c06a; font-size: 11px; padding: 5px 9px; border-radius: 7px; }
    `,
  ],
})
export class GraphEditorPage implements OnInit {
  readonly store = inject(GraphsStore);
  private readonly modelsStore = inject(ModelsStore);
  private readonly validator = inject(GraphValidationService);
  private readonly route = inject(ActivatedRoute);

  readonly graphId = signal<number>(0);
  readonly graphName = signal<string>('');
  readonly workingNodes = signal<GraphNode[]>([]);
  readonly tailModels = signal<Record<string, string>>({});
  readonly selectedId = signal<string | null>(null);
  readonly loadedVersion = signal<AgentGraphVersion | null>(null);
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);
  readonly savedLabel = signal<string | null>(null);
  readonly versionsOpen = signal(false);
  readonly draftRestored = signal(false);
  // Gates autosave until the initial version load completes (see constructor).
  private readonly ready = signal(false);

  // Bulk "set all models to a tier" switcher.
  readonly selectedTier = signal<string | null>(null);
  readonly selectedTierModel = signal<string | null>(null);
  readonly tierModelOptions = computed<{ id: string; label: string }[]>(() => {
    const tier = (this.store.registry()?.tiers ?? []).find((t) => t.name === this.selectedTier());
    if (!tier) return [];
    return tier.models.map((id) => {
      const m = this.modelsStore.models().find((x) => x.id === id);
      return { id, label: m ? `${m.supports_reasoning ? '🧠 ' : ''}${m.display_name}` : id };
    });
  });

  readonly presentTypes = computed(() => new Set(this.workingNodes().map((n) => n.type)));

  readonly validation = computed<ValidationResult>(() =>
    this.validator.validate(this.workingNodes(), this.tailModels(), this.store.registry()),
  );

  readonly selection = computed<InspectorSelection | null>(() => {
    const id = this.selectedId();
    const reg = this.store.registry();
    if (!id || !reg) return null;
    const tailSpec = reg.tail.find((t) => t.agent_name === id);
    if (tailSpec) {
      return {
        id, label: tailSpec.label, kind: tailSpec.kind,
        selectable: tailSpec.model_selectable, editable: false,
        modelId: this.tailModels()[id] ?? null, defaultModelKey: tailSpec.default_model_key,
        isMacro: false, isNewsDigest: false, isTail: true,
      };
    }
    const node = this.workingNodes().find((n) => n.id === id);
    if (!node) return null;
    const spec = [...reg.analytical, ...reg.personas].find((s) => s.agent_name === node.type);
    return {
      id, label: spec?.label ?? node.type, kind: node.kind,
      selectable: spec?.model_selectable ?? true, editable: true,
      modelId: node.model_id ?? null, defaultModelKey: spec?.default_model_key ?? null,
      isMacro: node.type === 'macro', isNewsDigest: node.type === 'news_digest', isTail: false,
    };
  });

  constructor() {
    // Autosave the working draft to localStorage on every change — but only
    // AFTER the saved version has loaded (`ready`), so the empty initial state
    // never overwrites the draft and then gets "restored" over the real graph.
    effect(() => {
      if (!this.ready()) return;
      const id = this.graphId();
      if (!id) return;
      const payload = JSON.stringify({
        nodes: this.workingNodes(),
        tail_models: this.tailModels(),
        ts: new Date().toISOString(),
      });
      try {
        localStorage.setItem(this.draftKey(id), payload);
      } catch {
        /* storage may be unavailable */
      }
    });
  }

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    this.graphId.set(id);
    if (this.modelsStore.models().length === 0) {
      this.modelsStore.loadModels().subscribe();
    }
    this.store.loadRegistry().subscribe();
    this.store.getGraph(id).subscribe((g) => this.graphName.set(g.name));
    this.store.listVersions(id).subscribe((rows) => {
      const latest = rows[0];
      if (latest) {
        this.store.getVersion(id, latest.version).subscribe((v) => this.applyVersion(v, true));
      } else {
        this.applyVersion(null, true);
      }
    });
  }

  private draftKey(id: number): string {
    return `hf.graph-draft.${id}`;
  }

  private applyVersion(v: AgentGraphVersion | null, allowDraftRestore: boolean): void {
    this.loadedVersion.set(v);
    const baseNodes = v ? v.nodes.map((n) => ({ ...n })) : [];
    const baseTail = v ? { ...v.tail_models } : {};
    this.workingNodes.set(baseNodes);
    this.tailModels.set(baseTail);
    if (v) this.savedLabel.set('v' + v.version);
    // Restore an unsaved draft only if it genuinely differs from the saved
    // version and is newer (survives a forced refresh without clobbering load).
    if (allowDraftRestore) {
      try {
        const raw = localStorage.getItem(this.draftKey(this.graphId()));
        if (raw) {
          const d = JSON.parse(raw);
          const differs =
            JSON.stringify({ n: d.nodes ?? [], t: d.tail_models ?? {} }) !==
            JSON.stringify({ n: baseNodes, t: baseTail });
          const newer = !v?.created_at || (d.ts && d.ts > v.created_at);
          if (Array.isArray(d.nodes) && d.nodes.length && differs && newer) {
            this.workingNodes.set(d.nodes);
            this.tailModels.set(d.tail_models ?? {});
            this.draftRestored.set(true);
          }
        }
      } catch {
        /* ignore */
      }
    }
    this.ready.set(true);
  }

  discardDraft(): void {
    this.draftRestored.set(false);
    try {
      localStorage.removeItem(this.draftKey(this.graphId()));
    } catch {
      /* ignore */
    }
    const v = this.loadedVersion();
    this.workingNodes.set(v ? v.nodes.map((n) => ({ ...n })) : []);
    this.tailModels.set(v ? { ...v.tail_models } : {});
  }

  // ---- node ops ---------------------------------------------------------
  addNode(type: string, position?: { x: number; y: number }): void {
    if (this.presentTypes().has(type)) return;
    const reg = this.store.registry();
    const spec = [...(reg?.analytical ?? []), ...(reg?.personas ?? [])].find((s) => s.agent_name === type);
    if (!spec) return;
    const node: GraphNode = {
      id: type, type, kind: spec.kind,
      position: position ?? { x: 0, y: 0 },
      model_id: spec.default_model_key,
      config: {},
    };
    this.workingNodes.update((ns) => [...ns, node]);
    this.selectedId.set(type);
  }

  onMove(e: { id: string; position: { x: number; y: number } }): void {
    this.workingNodes.update((ns) =>
      ns.map((n) => (n.id === e.id ? { ...n, position: e.position } : n)),
    );
  }

  onDelete(id: string): void {
    this.workingNodes.update((ns) => ns.filter((n) => n.id !== id));
    if (this.selectedId() === id) this.selectedId.set(null);
  }

  onModelChange(e: { id: string; modelId: string | null }): void {
    const reg = this.store.registry();
    const isTail = reg?.tail.some((t) => t.agent_name === e.id);
    if (isTail) {
      this.tailModels.update((tm) => {
        const next = { ...tm };
        if (e.modelId) next[e.id] = e.modelId;
        else delete next[e.id];
        return next;
      });
    } else {
      this.workingNodes.update((ns) =>
        ns.map((n) => (n.id === e.id ? { ...n, model_id: e.modelId } : n)),
      );
    }
  }

  // ---- bulk tier switcher ----------------------------------------------
  onTierChange(name: string | null): void {
    this.selectedTier.set(name ?? null);
    if (!name) {
      this.selectedTierModel.set(null);
      return;
    }
    const tier = (this.store.registry()?.tiers ?? []).find((t) => t.name === name);
    const model = tier?.default_model ?? null;
    this.selectedTierModel.set(model);
    if (model) this.applyModelToAll(model);
  }

  onTierModelChange(modelId: string | null): void {
    this.selectedTierModel.set(modelId ?? null);
    if (modelId) this.applyModelToAll(modelId);
  }

  /** Set one model on every persona/analytical node + the selectable tail
   *  (risk, cio). PM is deterministic and untouched. */
  private applyModelToAll(modelId: string): void {
    this.workingNodes.update((ns) => ns.map((n) => ({ ...n, model_id: modelId })));
    const selectableTail = (this.store.registry()?.tail ?? []).filter((t) => t.model_selectable);
    this.tailModels.update((tm) => {
      const next = { ...tm };
      for (const t of selectableTail) next[t.agent_name] = modelId;
      return next;
    });
  }

  // ---- drag-drop from palette ------------------------------------------
  onDragOver(ev: DragEvent): void {
    if (ev.dataTransfer?.types.includes('text/hf-node-type')) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'copy';
    }
  }

  onDrop(ev: DragEvent): void {
    const type = ev.dataTransfer?.getData('text/hf-node-type');
    if (!type) return;
    ev.preventDefault();
    this.addNode(type);
  }

  // ---- save / export / import ------------------------------------------
  private canonicalEdges(): GraphEdge[] {
    const edges: GraphEdge[] = [];
    for (const n of this.workingNodes()) {
      if (n.kind === 'analytical') {
        edges.push({ from: 'entry', to: n.id });
        edges.push({ from: n.id, to: 'analytical_join' });
      } else if (n.kind === 'persona') {
        edges.push({ from: 'analytical_join', to: n.id });
        edges.push({ from: n.id, to: 'persona_join' });
      }
    }
    return edges;
  }

  save(): void {
    if (!this.validation().is_valid || this.saving()) return;
    this.saving.set(true);
    this.saveError.set(null);
    this.store
      .saveVersion(this.graphId(), {
        nodes: this.workingNodes(),
        edges: this.canonicalEdges(),
        tail_models: this.tailModels(),
        notes: '',
      })
      .subscribe({
        next: (v) => {
          this.saving.set(false);
          this.draftRestored.set(false);
          try {
            localStorage.removeItem(this.draftKey(this.graphId()));
          } catch {
            /* ignore */
          }
          this.applyVersion(v, false);
          this.savedLabel.set('v' + v.version);
        },
        error: (err) => {
          this.saving.set(false);
          const body = err?.error;
          if (body?.errors?.length) {
            this.saveError.set('Cannot save: ' + body.errors.map((e: any) => e.message).join('; '));
          } else {
            this.saveError.set(body?.detail || 'Save failed.');
          }
        },
      });
  }

  onLoadVersion(v: AgentGraphVersion): void {
    this.applyVersion(v, false);
    this.versionsOpen.set(false);
    this.draftRestored.set(false);
  }

  exportJson(): void {
    const data = {
      schema_version: this.store.registry()?.schema_version ?? 1,
      name: this.graphName(),
      nodes: this.workingNodes(),
      edges: this.canonicalEdges(),
      tail_models: this.tailModels(),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${this.graphName().replace(/\s+/g, '-').toLowerCase() || 'graph'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  importJson(ev: Event): void {
    const file = (ev.target as HTMLInputElement).files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const d = JSON.parse(String(reader.result));
        if (Array.isArray(d.nodes)) {
          this.workingNodes.set(d.nodes);
          this.tailModels.set(d.tail_models ?? {});
          this.selectedId.set(null);
          this.saveError.set(null);
        }
      } catch {
        this.saveError.set('Import failed: invalid JSON.');
      }
    };
    reader.readAsText(file);
    (ev.target as HTMLInputElement).value = '';
  }

  // ---- keyboard ---------------------------------------------------------
  @HostListener('window:keydown', ['$event'])
  onKey(ev: KeyboardEvent): void {
    const target = ev.target as HTMLElement;
    const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(target?.tagName);
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 's') {
      ev.preventDefault();
      this.save();
      return;
    }
    if (!typing && (ev.key === 'Delete' || ev.key === 'Backspace')) {
      const id = this.selectedId();
      if (id && this.workingNodes().some((n) => n.id === id)) {
        ev.preventDefault();
        this.onDelete(id);
      }
    }
  }
}
