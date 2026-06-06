import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ModelsStore } from '../../abstraction/models.store';
import { GraphNode, GraphRegistry, NodeSpec } from '../../core/models/graph.model';

interface LaidNode {
  id: string;
  label: string;
  kind: string;
  x: number;
  y: number;
  w: number;
  h: number;
  editable: boolean;
  selectable: boolean;
  modelLabel: string | null;
  locked: boolean;
}

interface LaidEdge {
  id: string;
  path: string;
}

const NODE_W = 158;
const NODE_H = 60;
const STRUCT_W = 84;
const STRUCT_H = 44;
const LANE = {
  entry: 24,
  analytical: 210,
  analytical_join: 430,
  persona: 620,
  persona_join: 840,
  risk_manager: 1030,
  portfolio_manager: 1220,
  cio: 1410,
};
const ROW_DY = 84;
const CENTER_Y = 300;

/**
 * Hand-built SVG/HTML canvas for the P4c agent-graph editor (D3 SVG fallback —
 * rete-angular is incompatible with this app's zoneless Angular 21 runtime).
 * The topology is fixed lanes (entry → analytical → analytical_join → persona →
 * persona_join → risk → PM → cio), so wiring is auto-canonical and the tail is
 * locked. Pan = drag background; zoom = wheel; nodes draggable; click selects.
 */
@Component({
  selector: 'hf-graph-canvas',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      #viewport
      class="vp"
      tabindex="0"
      role="application"
      aria-label="Agent graph canvas"
      (pointerdown)="onBgPointerDown($event)"
      (wheel)="onWheel($event)"
      (keydown)="onKeydown($event)"
    >
      <div class="world" [style.transform]="worldTransform()">
        <svg class="edges" [attr.width]="4000" [attr.height]="1200">
          @for (e of edges(); track e.id) {
            <path [attr.d]="e.path" class="edge" />
          }
        </svg>
        @for (n of laid(); track n.id) {
          <div
            class="node"
            [class.persona]="n.kind === 'persona'"
            [class.analytical]="n.kind === 'analytical'"
            [class.structural]="n.kind === 'structural'"
            [class.tail]="n.kind === 'risk' || n.kind === 'portfolio' || n.kind === 'cio'"
            [class.selected]="n.id === selectedId()"
            [class.locked]="n.locked"
            [style.left.px]="n.x"
            [style.top.px]="n.y"
            [style.width.px]="n.w"
            [style.height.px]="n.h"
            [attr.data-testid]="'node-' + n.id"
            [attr.aria-label]="n.label + (n.locked ? ' (locked)' : '')"
            (pointerdown)="onNodePointerDown($event, n)"
            (click)="onNodeClick($event, n)"
          >
            <span class="port in" aria-hidden="true"></span>
            <span class="port out" aria-hidden="true"></span>
            <div class="node-hd">
              <span class="dot"></span>{{ n.label }}
              @if (n.locked) { <span class="lock" title="Locked tail node">🔒</span> }
            </div>
            @if (n.kind !== 'structural') {
              <div class="node-bd">
                @if (n.selectable) {
                  <span class="model">{{ n.modelLabel || 'default model' }}</span>
                } @else if (n.kind === 'portfolio') {
                  <span class="model det">deterministic — no model</span>
                }
              </div>
            }
          </div>
        }
      </div>

      <div class="zoom-ctl">
        <button type="button" (click)="zoomBy(0.1)" aria-label="Zoom in">+</button>
        <button type="button" (click)="zoomBy(-0.1)" aria-label="Zoom out">−</button>
        <button type="button" (click)="resetView()" aria-label="Reset view">⟳</button>
        <span class="zlbl">{{ (view().scale * 100) | number: '1.0-0' }}%</span>
      </div>
    </div>
  `,
  styles: [
    `
      .vp { position: relative; width: 100%; height: 100%; overflow: hidden;
            background: var(--surface-2, #0e1117); cursor: grab; outline: none;
            border-radius: 10px; }
      .vp:active { cursor: grabbing; }
      .world { position: absolute; top: 0; left: 0; transform-origin: 0 0; }
      svg.edges { position: absolute; top: 0; left: 0; overflow: visible; pointer-events: none; }
      .edge { fill: none; stroke: var(--border-strong, #3a4254); stroke-width: 2; }
      .node { position: absolute; border-radius: 9px; border: 1px solid var(--border, #2a3142);
              background: var(--surface, #151b26); box-shadow: 0 1px 3px rgba(0,0,0,.4);
              user-select: none; cursor: grab; overflow: visible; }
      .node.selected { border-color: var(--acc-info); box-shadow: 0 0 0 2px var(--acc-info); }
      .node.locked { cursor: pointer; opacity: .96; }
      .node.structural { background: transparent; border-style: dashed; display: flex;
                         align-items: center; justify-content: center; font-size: 11px;
                         color: var(--text-3); cursor: default; }
      .node.persona .dot { background: #b07cff; }
      .node.analytical .dot { background: #4ec3a5; }
      .node.tail .dot { background: #f0a23b; }
      .node-hd { display: flex; align-items: center; gap: 6px; padding: 8px 10px 2px;
                 font-size: 12.5px; font-weight: 600; color: var(--text, #e6ebf5); }
      .node.structural .node-hd { padding: 0; font-weight: 500; }
      .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
      .lock { margin-left: auto; font-size: 11px; }
      .node-bd { padding: 0 10px 8px; }
      .model { font-size: 10.5px; color: var(--text-3); white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; display: block; }
      .model.det { font-style: italic; }
      .port { position: absolute; width: 9px; height: 9px; border-radius: 50%;
              background: var(--border-strong, #3a4254); top: 50%; transform: translateY(-50%); }
      .port.in { left: -5px; } .port.out { right: -5px; }
      .node.structural .port { display: none; }
      .zoom-ctl { position: absolute; right: 10px; bottom: 10px; display: flex; gap: 4px;
                  align-items: center; background: var(--surface, #151b26);
                  border: 1px solid var(--border, #2a3142); border-radius: 8px; padding: 3px 6px; }
      .zoom-ctl button { width: 24px; height: 24px; border-radius: 5px; border: none;
                         background: var(--surface-2, #0e1117); color: var(--text, #e6ebf5);
                         cursor: pointer; font-size: 14px; }
      .zlbl { font-size: 11px; color: var(--text-3); min-width: 34px; text-align: center; }
    `,
  ],
})
export class GraphCanvasComponent {
  private readonly modelsStore = inject(ModelsStore);

  readonly userNodes = input<GraphNode[]>([]);
  readonly tail = input<NodeSpec[]>([]);
  readonly tailModels = input<Record<string, string>>({});
  readonly registry = input<GraphRegistry | null>(null);
  readonly selectedId = input<string | null>(null);

  readonly selectNode = output<string | null>();
  readonly moveNode = output<{ id: string; position: { x: number; y: number } }>();
  readonly addNodeAt = output<{ type: string; position: { x: number; y: number } }>();

  private readonly viewportRef = viewChild.required<ElementRef<HTMLElement>>('viewport');
  readonly view = signal({ x: 40, y: 0, scale: 1 });

  readonly worldTransform = computed(() => {
    const v = this.view();
    return `translate(${v.x}px, ${v.y}px) scale(${v.scale})`;
  });

  private modelLabel(id?: string | null): string | null {
    if (!id) return null;
    const m = this.modelsStore.models().find((x) => x.id === id);
    return m ? m.display_name : id;
  }

  readonly laid = computed<LaidNode[]>(() => {
    const reg = this.registry();
    const nodes: LaidNode[] = [];

    // structural
    nodes.push(struct('entry', 'Entry', LANE.entry, CENTER_Y));
    nodes.push(struct('analytical_join', 'Join', LANE.analytical_join, CENTER_Y));
    nodes.push(struct('persona_join', 'Join', LANE.persona_join, CENTER_Y));

    const analytical = this.userNodes().filter((n) => n.kind === 'analytical');
    const personas = this.userNodes().filter((n) => n.kind === 'persona');
    const specByName = new Map<string, NodeSpec>(
      [...(reg?.analytical ?? []), ...(reg?.personas ?? [])].map((s) => [s.agent_name, s]),
    );

    analytical.forEach((n, i) => {
      const spec = specByName.get(n.type);
      nodes.push({
        id: n.id, label: spec?.label ?? n.type, kind: 'analytical',
        x: n.position?.x ?? LANE.analytical, y: n.position?.y ?? laneY(i, analytical.length),
        w: NODE_W, h: NODE_H, editable: true, selectable: spec?.model_selectable ?? true,
        modelLabel: this.modelLabel(n.model_id), locked: false,
      });
    });
    personas.forEach((n, i) => {
      const spec = specByName.get(n.type);
      nodes.push({
        id: n.id, label: spec?.label ?? n.type, kind: 'persona',
        x: n.position?.x ?? LANE.persona, y: n.position?.y ?? laneY(i, personas.length),
        w: NODE_W, h: NODE_H, editable: true, selectable: spec?.model_selectable ?? true,
        modelLabel: this.modelLabel(n.model_id), locked: false,
      });
    });

    // locked tail
    const tailModels = this.tailModels();
    (this.tail() ?? []).forEach((s) => {
      const lane = (LANE as Record<string, number>)[s.agent_name] ?? LANE.cio;
      nodes.push({
        id: s.agent_name, label: s.label, kind: s.kind,
        x: lane, y: CENTER_Y, w: NODE_W, h: NODE_H, editable: false,
        selectable: s.model_selectable,
        modelLabel: s.model_selectable ? this.modelLabel(tailModels[s.agent_name] || s.default_model_key) : null,
        locked: true,
      });
    });

    return nodes;
  });

  readonly edges = computed<LaidEdge[]>(() => {
    const byId = new Map(this.laid().map((n) => [n.id, n]));
    const pairs: [string, string][] = [];
    const analytical = this.userNodes().filter((n) => n.kind === 'analytical');
    const personas = this.userNodes().filter((n) => n.kind === 'persona');
    for (const a of analytical) {
      pairs.push(['entry', a.id]);
      pairs.push([a.id, 'analytical_join']);
    }
    for (const p of personas) {
      pairs.push(['analytical_join', p.id]);
      pairs.push([p.id, 'persona_join']);
    }
    pairs.push(['persona_join', 'risk_manager']);
    pairs.push(['risk_manager', 'portfolio_manager']);
    pairs.push(['portfolio_manager', 'cio']);

    const out: LaidEdge[] = [];
    for (const [from, to] of pairs) {
      const s = byId.get(from);
      const t = byId.get(to);
      if (!s || !t) continue;
      const x1 = s.x + s.w;
      const y1 = s.y + s.h / 2;
      const x2 = t.x;
      const y2 = t.y + t.h / 2;
      const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
      out.push({ id: `${from}->${to}`, path: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}` });
    }
    return out;
  });

  // ---- pan / zoom -------------------------------------------------------
  private panning = false;
  private panStart = { x: 0, y: 0, vx: 0, vy: 0 };

  onBgPointerDown(ev: PointerEvent): void {
    if (ev.button !== 0) return;
    this.selectNode.emit(null);
    this.panning = true;
    const v = this.view();
    this.panStart = { x: ev.clientX, y: ev.clientY, vx: v.x, vy: v.y };
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    const move = (e: PointerEvent) => {
      if (!this.panning) return;
      this.view.update((s) => ({ ...s, x: this.panStart.vx + (e.clientX - this.panStart.x), y: this.panStart.vy + (e.clientY - this.panStart.y) }));
    };
    const up = () => {
      this.panning = false;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  onWheel(ev: WheelEvent): void {
    ev.preventDefault();
    const delta = ev.deltaY < 0 ? 0.1 : -0.1;
    this.zoomAt(delta, ev.offsetX, ev.offsetY);
  }

  zoomBy(delta: number): void {
    const el = this.viewportRef().nativeElement;
    this.zoomAt(delta, el.clientWidth / 2, el.clientHeight / 2);
  }

  private zoomAt(delta: number, px: number, py: number): void {
    this.view.update((s) => {
      const scale = Math.min(1.8, Math.max(0.35, s.scale + delta));
      const k = scale / s.scale;
      // keep the point under the cursor fixed
      return { scale, x: px - (px - s.x) * k, y: py - (py - s.y) * k };
    });
  }

  resetView(): void {
    this.view.set({ x: 40, y: 0, scale: 1 });
  }

  // ---- node interactions -----------------------------------------------
  private dragging: { id: string; startX: number; startY: number; nodeX: number; nodeY: number } | null = null;

  onNodePointerDown(ev: PointerEvent, n: LaidNode): void {
    ev.stopPropagation();
    this.selectNode.emit(n.id);
    if (!n.editable) return;
    this.dragging = { id: n.id, startX: ev.clientX, startY: ev.clientY, nodeX: n.x, nodeY: n.y };
    const scale = this.view().scale;
    const move = (e: PointerEvent) => {
      if (!this.dragging) return;
      const nx = this.dragging.nodeX + (e.clientX - this.dragging.startX) / scale;
      const ny = this.dragging.nodeY + (e.clientY - this.dragging.startY) / scale;
      this.moveNode.emit({ id: this.dragging.id, position: { x: Math.round(nx), y: Math.round(ny) } });
    };
    const up = () => {
      this.dragging = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  onNodeClick(ev: MouseEvent, n: LaidNode): void {
    ev.stopPropagation();
    this.selectNode.emit(n.id);
  }

  onKeydown(ev: KeyboardEvent): void {
    // Delete handled by the page (needs selection + node list); forward focus only.
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && this.selectedId()) {
      // bubbles to page via a custom approach? Page listens on window; no-op here.
    }
  }
}

function struct(id: string, label: string, x: number, y: number): LaidNode {
  return {
    id, label, kind: 'structural', x, y: y + (NODE_H - STRUCT_H) / 2,
    w: STRUCT_W, h: STRUCT_H, editable: false, selectable: false, modelLabel: null, locked: false,
  };
}

function laneY(index: number, count: number): number {
  const totalH = count * NODE_H + (count - 1) * (ROW_DY - NODE_H);
  const top = CENTER_Y + NODE_H / 2 - totalH / 2;
  return Math.round(top + index * ROW_DY);
}
