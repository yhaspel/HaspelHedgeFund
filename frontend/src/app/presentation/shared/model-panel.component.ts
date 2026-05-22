import { Component, computed, effect, inject, input, model, signal } from '@angular/core';
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
    <section class="flex flex-col gap-3">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="eyebrow">Models</div>
          <div class="text-[11.5px] text-text-3 mt-1">
            Preset: <span class="mono text-text-2">{{ activePreset() }}</span>
            · Est. per-ticker cost:
            <span class="mono text-text-2">$ {{ totalCost().toFixed(4) }}</span>
            @if (multiplier() > 1) {
              <span> · ×{{ multiplier() }} ≈ <span class="mono text-text-2">$ {{ (totalCost() * multiplier()).toFixed(2) }}</span></span>
            }
          </div>
        </div>
        <button
          type="button"
          (click)="expanded.set(!expanded())"
          class="btn ghost sm text-[var(--acc-info-fg)]"
          data-test="model-panel-expand"
        >
          {{ expanded() ? 'Collapse' : 'Expand' }}
        </button>
      </div>

      <div class="flex flex-wrap gap-1.5">
        @for (p of presets; track p) {
          <button
            type="button"
            (click)="applyPreset(p)"
            class="btn sm"
            [class.primary]="activePreset() === p"
            [attr.data-test]="'preset-' + p"
          >
            {{ p }}
          </button>
        }
      </div>

      @if (expanded()) {
        <div class="model-panel-rows">
          @for (a of agents(); track a) {
            <div class="model-panel-row">
              <div class="text-text-2">{{ display(a) }}</div>
              <select
                class="input sans model-panel-select"
                [ngModel]="currentFor(a)"
                (ngModelChange)="setOverride(a, $event)"
                [attr.data-test]="'select-' + a"
              >
                @for (m of store.models(); track m.id) {
                  <option [value]="m.id" [disabled]="!m.available">
                    {{ m.display_name }} · {{ m.tier }} ·
                    $ {{ estimate(a, m.id).toFixed(4) }}
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
  styles: [
    `
      .model-panel-rows {
        border-top: 1px solid var(--border);
        padding-top: 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        max-height: 384px;
        overflow-y: auto;
      }
      .model-panel-row {
        display: grid;
        grid-template-columns: 1fr 2fr;
        gap: 8px;
        align-items: center;
        font-size: 13px;
      }
      .model-panel-select {
        height: 28px;
        font-size: 11.5px;
        padding: 0 8px;
      }
    `,
  ],
})
export class ModelPanelComponent {
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

  // React to async preference loads — the prefs() signal may still be null
  // when the panel first renders; flipping it must update the active preset.
  // Only adopt the server preset if the user hasn't already touched the
  // dropdown locally (preserve user intent during the GET round-trip).
  private prefsApplied = false;
  private readonly _syncPreset = effect(() => {
    const prefs = this.store.prefs();
    if (!prefs?.preset || this.prefsApplied) return;
    this.activePreset.set(prefs.preset);
    this.prefsApplied = true;
  });

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
