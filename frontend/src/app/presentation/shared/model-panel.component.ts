import { Component, computed, inject, input, model, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ModelsStore } from '../../abstraction/models.store';
import {
  AGENT_DISPLAY,
  PRESET_NAMES,
  estimateAgentCost,
  estimateRunCost,
} from '../../core/models/model.types';

/**
 * Reusable model-selection panel.
 *
 * - Input `agents`: list of agent ids to show rows for (e.g. selected personas).
 * - Two-way `overrides`: {agent_id: model_id}.
 * - Shows current preset, total cost estimate, and "Expand" affordance.
 * - Preset buttons populate `overrides` in one click.
 */
@Component({
  selector: 'hf-model-panel',
  standalone: true,
  imports: [FormsModule],
  template: `
    <section class="border rounded bg-white p-4 space-y-3">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm font-medium">Models</div>
          <div class="text-xs text-gray-500">
            Preset: <span class="font-mono">{{ activePreset() }}</span>
            · Est. per-ticker cost:
            <span class="font-mono">\${{ totalCost().toFixed(4) }}</span>
            @if (multiplier() > 1) {
              <span> · ×{{ multiplier() }} ≈ \${{ (totalCost() * multiplier()).toFixed(2) }}</span>
            }
          </div>
        </div>
        <button
          type="button"
          (click)="expanded.set(!expanded())"
          class="text-blue-600 text-sm hover:underline"
          data-test="model-panel-expand"
        >
          {{ expanded() ? 'Collapse' : 'Expand' }}
        </button>
      </div>

      <div class="flex flex-wrap gap-2">
        @for (p of presets; track p) {
          <button
            type="button"
            (click)="applyPreset(p)"
            [class.bg-blue-600]="activePreset() === p"
            [class.text-white]="activePreset() === p"
            [class.bg-gray-100]="activePreset() !== p"
            class="px-3 py-1 rounded text-xs"
            [attr.data-test]="'preset-' + p"
          >
            {{ p }}
          </button>
        }
      </div>

      @if (expanded()) {
        <div class="border-t pt-3 space-y-2 max-h-96 overflow-y-auto">
          @for (a of agents(); track a) {
            <div class="grid grid-cols-3 gap-2 items-center text-sm">
              <div>{{ display(a) }}</div>
              <select
                class="col-span-2 border rounded px-2 py-1 text-xs"
                [ngModel]="currentFor(a)"
                (ngModelChange)="setOverride(a, $event)"
                [attr.data-test]="'select-' + a"
              >
                @for (m of store.models(); track m.id) {
                  <option [value]="m.id" [disabled]="!m.available">
                    {{ m.display_name }} · {{ m.tier }} ·
                    \${{ estimate(a, m.id).toFixed(4) }}
                    {{ m.available ? '' : ' (no key)' }}
                  </option>
                }
              </select>
            </div>
          }
        </div>
      }
    </section>
  `,
})
export class ModelPanelComponent implements OnInit {
  readonly store = inject(ModelsStore);

  agents = input<string[]>([]);
  multiplier = input<number>(1);
  overrides = model<Record<string, string>>({});

  expanded = signal(false);
  presets = PRESET_NAMES;

  activePreset = signal<string>('research');

  totalCost = computed(() =>
    estimateRunCost(
      this.agents(),
      this.overrides(),
      this.store.defaultsMap(),
      this.store.models(),
    ),
  );

  ngOnInit(): void {
    const prefs = this.store.prefs();
    if (prefs?.preset) this.activePreset.set(prefs.preset);
  }

  display(a: string): string {
    return AGENT_DISPLAY[a] ?? a;
  }

  currentFor(a: string): string {
    return this.overrides()[a] ?? this.store.defaultsMap()[a] ?? '';
  }

  estimate(a: string, modelId: string): number {
    return estimateAgentCost(a, modelId, this.store.models());
  }

  setOverride(a: string, modelId: string): void {
    this.overrides.set({ ...this.overrides(), [a]: modelId });
  }

  applyPreset(name: string): void {
    this.activePreset.set(name);
    this.store.fetchPreset(name).subscribe((r) => {
      const next: Record<string, string> = {};
      for (const a of this.agents()) {
        if (r.overrides[a]) next[a] = r.overrides[a];
      }
      this.overrides.set(next);
    });
  }
}
