import { Component, computed, effect, inject, input, model, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ModelsStore } from '../../abstraction/models.store';
import {
  AGENT_DISPLAY,
  ModelEntry,
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
 * - Per-agent dropdowns are scoped to the active tier's curated menu
 *   (P3-C §7.1). Discovered local models (provider==="ollama") stay
 *   first-class and visible regardless of the tier. "Show all models"
 *   restores the full catalog as an escape hatch.
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
          <label class="show-all-row">
            <input
              type="checkbox"
              [checked]="showAll()"
              (change)="showAll.set($any($event.target).checked)"
              data-test="model-panel-show-all"
            />
            <span class="text-[11.5px] text-text-3">
              Show all models (default: scope to the active tier's menu).
            </span>
          </label>
          @for (a of agents(); track a) {
            <div class="model-panel-row">
              <div class="text-text-2">{{ display(a) }}</div>
              <select
                class="input sans model-panel-select"
                [ngModel]="currentFor(a)"
                (ngModelChange)="setOverride(a, $event)"
                [attr.data-test]="'select-' + a"
              >
                @for (m of visibleModels(a); track m.id) {
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
      .show-all-row {
        display: flex;
        align-items: center;
        gap: 6px;
        padding-bottom: 4px;
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
  showAll = signal(false);
  presets = PRESET_NAMES;

  activePreset = signal<string>('research');
  tierMenu = signal<string[]>([]);

  totalCost = computed(() =>
    estimateRunCost(
      this.agents(),
      this.overrides(),
      this.store.defaultsMap(),
      this.store.models(),
    ),
  );

  // React to async preference loads — the prefs() signal may still be null
  // when the panel first renders. Adopt the saved preset by *applying* it
  // (populating `overrides`), not just by updating the display label.
  // Previously the effect only flipped `activePreset`, so a user who landed
  // on the form, saw "Preset: frugal", and submitted without re-clicking the
  // button would send `overrides: {}` → backend fell through to the
  // BLOCK_ANTHROPIC fallback (gpt-oss-120b:free), not to frugal. Skip if the
  // user has already touched the dropdown or clicked a different preset.
  private prefsApplied = false;
  private readonly _syncPreset = effect(() => {
    const prefs = this.store.prefs();
    if (!prefs?.preset || this.prefsApplied) return;
    if (Object.keys(this.overrides()).length > 0) {
      this.prefsApplied = true;
      return;
    }
    this.prefsApplied = true;
    this.applyPreset(prefs.preset);
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

  /**
   * The list of <option> models for a given agent's select.
   *
   * - default: tier menu ∪ all discovered Ollama models (always first-class)
   * - "Show all" on: full catalog
   * - empty menu: fall back to full catalog so the dropdown never renders
   *   empty (e.g. before applyPreset()'s fetch resolves, or unknown preset)
   * - always append `currentFor(a)` if it would otherwise be missing — a
   *   <select> with a value not among its options renders blank and silently
   *   loses the user's selection.
   */
  visibleModels(a: string): ModelEntry[] {
    const all = this.store.models();
    if (this.showAll()) return all;
    const menu = this.tierMenu();
    if (!menu.length) return all;
    const menuSet = new Set(menu);
    const out = all.filter(
      (m) => menuSet.has(m.id) || m.provider === 'ollama',
    );
    const current = this.currentFor(a);
    if (current && !out.some((m) => m.id === current)) {
      const stale = all.find((m) => m.id === current);
      if (stale) out.push(stale);
    }
    return out;
  }

  setOverride(a: string, modelId: string): void {
    this.overrides.set({ ...this.overrides(), [a]: modelId });
  }

  applyPreset(name: string): void {
    this.activePreset.set(name);
    this.store.fetchPreset(name).subscribe((r) => {
      this.tierMenu.set(r.menu ?? []);
      const next: Record<string, string> = {};
      for (const a of this.agents()) {
        if (r.overrides[a]) next[a] = r.overrides[a];
      }
      this.overrides.set(next);
    });
  }
}
