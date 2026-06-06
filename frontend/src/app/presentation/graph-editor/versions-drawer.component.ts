import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { GraphsStore } from '../../abstraction/graphs.store';
import {
  AgentGraphVersion,
  AgentGraphVersionRow,
} from '../../core/models/graph.model';

interface DiffRow {
  kind: 'added' | 'removed' | 'model' | 'tail';
  label: string;
  detail: string;
}

/** Slide-over: version history + load + side-by-side diff of two versions. */
@Component({
  selector: 'hf-graph-versions-drawer',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scrim" (click)="close.emit()"></div>
    <aside class="drawer" role="dialog" aria-label="Version history">
      <div class="hd">
        <h3>Version history</h3>
        <button type="button" class="x" (click)="close.emit()" aria-label="Close">✕</button>
      </div>

      <div class="diff-ctl">
        <label>Compare
          <select [value]="diffA() ?? ''" (change)="setA($event)" data-testid="diff-a">
            <option value="">—</option>
            @for (r of rows(); track r.id) { <option [value]="r.version">v{{ r.version }}</option> }
          </select>
        </label>
        <label>vs
          <select [value]="diffB() ?? ''" (change)="setB($event)" data-testid="diff-b">
            <option value="">—</option>
            @for (r of rows(); track r.id) { <option [value]="r.version">v{{ r.version }}</option> }
          </select>
        </label>
      </div>

      @if (diffRows(); as d) {
        <div class="diff" data-testid="diff-result">
          @if (d.length === 0) {
            <p class="muted">No differences.</p>
          } @else {
            @for (row of d; track row.label + row.kind) {
              <div class="drow {{ row.kind }}">
                <span class="tag">{{ row.kind }}</span>
                <span class="dl"><strong>{{ row.label }}</strong> {{ row.detail }}</span>
              </div>
            }
          }
        </div>
      }

      <ul class="vlist">
        @for (r of rows(); track r.id) {
          <li [class.current]="r.version === currentVersion()">
            <div class="vmeta">
              <strong>v{{ r.version }}</strong>
              <span class="vstatus" [class.ok]="r.validation_status === 'valid'">{{ r.validation_status }}</span>
              <span class="vdate">{{ r.created_at | date: 'MMM d, HH:mm' }}</span>
            </div>
            @if (r.notes) { <p class="vnotes">{{ r.notes }}</p> }
            <button type="button" class="load" (click)="loadVersion(r.version)" [attr.data-testid]="'load-v' + r.version">
              Load v{{ r.version }}
            </button>
          </li>
        }
        @if (rows().length === 0) { <li class="muted">No saved versions yet.</li> }
      </ul>
    </aside>
  `,
  styles: [
    `
      .scrim { position: absolute; inset: 0; background: rgba(0,0,0,.35); z-index: 5; }
      .drawer { position: absolute; top: 0; right: 0; width: 340px; height: 100%; z-index: 6;
                background: var(--surface, #151b26); border-left: 1px solid var(--border, #2a3142);
                padding: 14px; overflow-y: auto; box-shadow: -4px 0 16px rgba(0,0,0,.4); }
      .hd { display: flex; justify-content: space-between; align-items: center; }
      h3 { margin: 0; font-size: 14px; color: var(--text, #e6ebf5); }
      .x { background: none; border: none; color: var(--text-3); cursor: pointer; font-size: 14px; }
      .diff-ctl { display: flex; gap: 10px; margin: 12px 0 8px; }
      .diff-ctl label { font-size: 11px; color: var(--text-3); display: flex; flex-direction: column; gap: 3px; }
      .diff-ctl select { padding: 4px 6px; border-radius: 6px; border: 1px solid var(--border, #2a3142);
                         background: var(--surface-2, #0e1117); color: var(--text, #e6ebf5); }
      .diff { background: var(--surface-2, #0e1117); border-radius: 8px; padding: 8px; margin-bottom: 12px; }
      .drow { display: flex; gap: 8px; font-size: 11.5px; padding: 3px 0; align-items: baseline; }
      .tag { font-size: 9.5px; text-transform: uppercase; padding: 1px 6px; border-radius: 999px; flex: none; }
      .drow.added .tag { background: #163a2c; color: #5fd6a6; }
      .drow.removed .tag { background: #3a1b1b; color: #f08a8a; }
      .drow.model .tag, .drow.tail .tag { background: #2e2410; color: #e8c06a; }
      .dl { color: var(--text, #e6ebf5); }
      .muted { color: var(--text-3); font-size: 12px; }
      .vlist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
      .vlist li { border: 1px solid var(--border, #2a3142); border-radius: 8px; padding: 8px 10px; }
      .vlist li.current { border-color: var(--acc-info); }
      .vmeta { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--text, #e6ebf5); }
      .vstatus { font-size: 10px; color: #f08a8a; } .vstatus.ok { color: #5fd6a6; }
      .vdate { margin-left: auto; font-size: 10.5px; color: var(--text-3); }
      .vnotes { font-size: 11px; color: var(--text-3); margin: 5px 0; }
      .load { margin-top: 4px; background: var(--surface-2, #0e1117); border: 1px solid var(--border, #2a3142);
              color: var(--text, #e6ebf5); border-radius: 6px; padding: 5px 9px; font-size: 11.5px; cursor: pointer; }
    `,
  ],
})
export class GraphVersionsDrawerComponent implements OnInit {
  private readonly store = inject(GraphsStore);

  readonly graphId = input.required<number>();
  readonly currentVersion = input<number | null>(null);
  readonly close = output<void>();
  readonly load = output<AgentGraphVersion>();

  readonly rows = signal<AgentGraphVersionRow[]>([]);
  readonly diffA = signal<number | null>(null);
  readonly diffB = signal<number | null>(null);
  private readonly verCache = signal<Record<number, AgentGraphVersion>>({});

  readonly diffRows = computed<DiffRow[] | null>(() => {
    const a = this.diffA();
    const b = this.diffB();
    if (a == null || b == null) return null;
    const cache = this.verCache();
    const va = cache[a];
    const vb = cache[b];
    if (!va || !vb) return null;
    return computeDiff(va, vb);
  });

  ngOnInit(): void {
    this.store.listVersions(this.graphId()).subscribe((rows) => this.rows.set(rows));
  }

  loadVersion(version: number): void {
    this.store.getVersion(this.graphId(), version).subscribe((v) => this.load.emit(v));
  }

  setA(ev: Event): void {
    const v = numOrNull((ev.target as HTMLSelectElement).value);
    this.diffA.set(v);
    if (v != null) this.ensure(v);
  }

  setB(ev: Event): void {
    const v = numOrNull((ev.target as HTMLSelectElement).value);
    this.diffB.set(v);
    if (v != null) this.ensure(v);
  }

  private ensure(version: number): void {
    if (this.verCache()[version]) return;
    this.store.getVersion(this.graphId(), version).subscribe((v) =>
      this.verCache.update((c) => ({ ...c, [version]: v })),
    );
  }
}

function numOrNull(s: string): number | null {
  return s === '' ? null : Number(s);
}

function computeDiff(a: AgentGraphVersion, b: AgentGraphVersion): DiffRow[] {
  const out: DiffRow[] = [];
  const aTypes = new Map(a.nodes.map((n) => [n.type, n]));
  const bTypes = new Map(b.nodes.map((n) => [n.type, n]));
  for (const [type, n] of bTypes) {
    if (!aTypes.has(type)) out.push({ kind: 'added', label: type, detail: `added (${n.kind})` });
  }
  for (const [type, n] of aTypes) {
    if (!bTypes.has(type)) out.push({ kind: 'removed', label: type, detail: `removed (${n.kind})` });
  }
  for (const [type, an] of aTypes) {
    const bn = bTypes.get(type);
    if (bn && (an.model_id || null) !== (bn.model_id || null)) {
      out.push({ kind: 'model', label: type, detail: `${an.model_id || 'default'} → ${bn.model_id || 'default'}` });
    }
  }
  const tailKeys = new Set([...Object.keys(a.tail_models), ...Object.keys(b.tail_models)]);
  for (const k of tailKeys) {
    const av = a.tail_models[k];
    const bv = b.tail_models[k];
    if (av !== bv) out.push({ kind: 'tail', label: k, detail: `${av || 'default'} → ${bv || 'default'}` });
  }
  return out;
}
