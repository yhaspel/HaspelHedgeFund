import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { GraphRegistry, NodeSpec } from '../../core/models/graph.model';

/** Left-rail palette: draggable / clickable analytical + persona node types.
 *  Risk/PM/CIO are NOT palette items (implicit tail). */
@Component({
  selector: 'hf-graph-palette',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="palette">
      <h3>Analytical</h3>
      <div class="group">
        @for (s of registry()?.analytical ?? []; track s.agent_name) {
          <button
            type="button"
            class="chip analytical"
            [class.used]="present().has(s.agent_name)"
            [disabled]="present().has(s.agent_name)"
            draggable="true"
            (dragstart)="onDragStart($event, s.agent_name)"
            (click)="add.emit(s.agent_name)"
            [attr.data-testid]="'palette-' + s.agent_name"
            [attr.aria-label]="'Add ' + s.label"
          >
            <span class="dot"></span>{{ s.label }}
          </button>
        }
      </div>
      <h3>Personas</h3>
      <div class="group">
        @for (s of registry()?.personas ?? []; track s.agent_name) {
          <button
            type="button"
            class="chip persona"
            [class.used]="present().has(s.agent_name)"
            [disabled]="present().has(s.agent_name)"
            draggable="true"
            (dragstart)="onDragStart($event, s.agent_name)"
            (click)="add.emit(s.agent_name)"
            [attr.data-testid]="'palette-' + s.agent_name"
            [attr.aria-label]="'Add ' + s.label"
          >
            <span class="dot"></span>{{ s.label }}
          </button>
        }
      </div>
      <p class="hint">
        Risk Manager, Portfolio Manager and CIO are a fixed, non-editable tail.
      </p>
    </div>
  `,
  styles: [
    `
      .palette { padding: 12px; overflow-y: auto; height: 100%; }
      h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
           color: var(--text-dim, #93a0b5); margin: 12px 0 6px; }
      .group { display: flex; flex-direction: column; gap: 6px; }
      .chip { display: flex; align-items: center; gap: 8px; padding: 8px 10px;
              border-radius: 8px; border: 1px solid var(--border, #2a3142);
              background: var(--surface, #151b26); color: var(--text, #e6ebf5);
              font-size: 12.5px; cursor: grab; text-align: left; }
      .chip:hover:not(:disabled) { border-color: var(--acc-info); }
      .chip.used { opacity: .4; cursor: default; text-decoration: line-through; }
      .chip .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
      .chip.analytical .dot { background: #4ec3a5; }
      .chip.persona .dot { background: #b07cff; }
      .hint { font-size: 11px; color: var(--text-dim, #93a0b5); margin-top: 14px; line-height: 1.4; }
    `,
  ],
})
export class GraphPaletteComponent {
  readonly registry = input<GraphRegistry | null>(null);
  readonly present = input<Set<string>>(new Set());
  readonly add = output<string>();

  onDragStart(ev: DragEvent, type: string): void {
    ev.dataTransfer?.setData('text/hf-node-type', type);
    if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'copy';
  }
}
