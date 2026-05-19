import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ModelsStore } from '../../abstraction/models.store';
import { AGENT_DISPLAY, GROUP_LABEL, PRESET_NAMES } from '../../core/models/model.types';

@Component({
  selector: 'hf-settings-models',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">Settings → Models</h1>
        <a routerLink="/" class="text-blue-600 hover:underline">Dashboard</a>
      </header>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-5xl">
        <!-- A: BYO keys -->
        <section class="bg-white rounded shadow p-5 space-y-3">
          <h2 class="font-medium">Provider keys (BYO)</h2>
          <p class="text-xs text-gray-500">
            Keys are stored Fernet-encrypted on your user row. P4a will replace
            this with a multi-tenant vault.
          </p>
          @for (p of providers; track p.field) {
            <label class="block text-sm">
              <span class="flex justify-between">
                {{ p.label }}
                <span class="text-xs"
                  [class.text-green-600]="statusOf(p.field) === 'set'"
                  [class.text-gray-400]="statusOf(p.field) !== 'set'"
                >{{ statusOf(p.field) }}</span>
              </span>
              <input
                type="password"
                [(ngModel)]="keyEdits[p.field]"
                [name]="p.field"
                placeholder="•••• paste to replace, blank to keep"
                class="mt-1 w-full border rounded px-3 py-2 font-mono text-xs"
              />
            </label>
          }
          <label class="block text-sm">
            Ollama host (for local models)
            <input
              type="text"
              [(ngModel)]="ollamaHost"
              name="ollama"
              placeholder="http://localhost:11434"
              class="mt-1 w-full border rounded px-3 py-2 font-mono text-xs"
              data-test="ollama-host"
            />
          </label>
          <button (click)="saveKeys()" [disabled]="savingKeys()"
            class="bg-blue-600 text-white rounded px-4 py-2 text-sm disabled:opacity-50">
            {{ savingKeys() ? 'Saving…' : 'Save provider keys' }}
          </button>
          @if (keysMsg()) {
            <p class="text-xs text-green-700">{{ keysMsg() }}</p>
          }
        </section>

        <!-- B: Default model + preset + cost ceiling -->
        <section class="bg-white rounded shadow p-5 space-y-3">
          <label class="block text-sm font-medium">
            Default model (applies to every agent)
            <select [(ngModel)]="globalDefault" name="globalDefault"
              (ngModelChange)="applyGlobalDefault($event)"
              class="mt-1 w-full border rounded px-3 py-2 text-sm"
              data-test="global-default-select">
              <option value="">— use preset per-agent rules —</option>
              @for (m of store.models(); track m.id) {
                <option [value]="m.id" [disabled]="!m.available">
                  {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                </option>
              }
            </select>
            <p class="text-xs text-gray-500 mt-1">
              Sets the same model for every agent. Clear to fall back to the preset rules below.
            </p>
          </label>

          <label class="block text-sm font-medium mt-2">
            Preset (used when no global default is set)
            <select [(ngModel)]="preset" name="preset"
              (ngModelChange)="loadPresetOverrides()"
              class="mt-1 w-full border rounded px-3 py-2 text-sm" data-test="preset-select">
              @for (p of presets; track p) {
                <option [value]="p">{{ p }}</option>
              }
            </select>
          </label>

          <label class="block text-sm mt-3">
            Cost ceiling per scheduled run (USD)
            <input type="number" step="0.5" min="0" [(ngModel)]="ceiling" name="ceil"
              class="mt-1 w-full border rounded px-3 py-2 text-sm" />
          </label>

          <button (click)="savePrefs()" [disabled]="savingPrefs()"
            class="bg-blue-600 text-white rounded px-4 py-2 text-sm disabled:opacity-50"
            data-test="save-prefs">
            {{ savingPrefs() ? 'Saving…' : 'Save preferences' }}
          </button>
          @if (prefsMsg()) {
            <p class="text-xs text-green-700">{{ prefsMsg() }}</p>
          }
        </section>

        <!-- C: per-agent defaults -->
        <section class="bg-white rounded shadow p-5 space-y-2 lg:col-span-2">
          <h2 class="font-medium">Per-agent defaults</h2>
          <p class="text-xs text-gray-500">
            "Current default" = what the active preset (<b>{{ preset }}</b>) resolves to.
            Pick an explicit model to override it for this agent.
          </p>
          <div class="mt-2 space-y-4">
            @for (g of groupedAgents(); track g.group) {
              <div>
                <h3 class="text-xs font-semibold uppercase text-gray-500 border-b pb-1 mb-2">
                  {{ groupLabel(g.group) }} ({{ g.agents.length }})
                </h3>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                  @for (a of g.agents; track a) {
                    <div class="grid grid-cols-3 gap-2 items-center text-sm">
                      <div>{{ display(a) }}</div>
                      <div class="text-xs font-mono"
                           [class.text-gray-400]="!!agentDefault(a)"
                           [class.text-gray-700]="!agentDefault(a)">
                        {{ resolvedDefault(a) }}
                      </div>
                      <select
                        [ngModel]="agentDefault(a)"
                        (ngModelChange)="setAgentDefault(a, $event)"
                        class="border rounded px-2 py-1 text-xs"
                      >
                        <option value="">— use preset default —</option>
                        @for (m of store.models(); track m.id) {
                          <option [value]="m.id" [disabled]="!m.available">
                            {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                          </option>
                        }
                      </select>
                    </div>
                  }
                </div>
              </div>
            }
          </div>
        </section>

        <!-- D: Available models -->
        <section class="bg-white rounded shadow p-5 lg:col-span-2">
          <h2 class="font-medium mb-2">Available models ({{ store.models().length }})</h2>
          <table class="w-full text-sm">
            <thead class="text-left text-gray-500 text-xs">
              <tr><th>Model</th><th>Provider</th><th>Tier</th>
                <th class="text-right">$/Mtok in</th>
                <th class="text-right">$/Mtok out</th>
                <th>Status</th></tr>
            </thead>
            <tbody>
              @for (m of store.models(); track m.id) {
                <tr class="border-t">
                  <td class="font-mono">{{ m.display_name }}</td>
                  <td>{{ m.provider }}</td>
                  <td>{{ m.tier }}</td>
                  <td class="text-right font-mono">{{ m.price_in_per_mtok ?? '0' }}</td>
                  <td class="text-right font-mono">{{ m.price_out_per_mtok ?? '0' }}</td>
                  <td>
                    <span [class.text-green-600]="m.available"
                          [class.text-gray-400]="!m.available">
                      {{ m.available ? 'available' : 'no key' }}
                    </span>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      </div>
    </div>
  `,
})
export class SettingsModelsPage implements OnInit {
  readonly store = inject(ModelsStore);
  readonly providers = [
    { field: 'anthropic', label: 'Anthropic API key' },
    { field: 'openrouter', label: 'OpenRouter API key' },
    { field: 'openai', label: 'OpenAI API key' },
  ] as const;
  readonly presets = PRESET_NAMES;

  keyEdits: Record<string, string> = { anthropic: '', openrouter: '', openai: '' };
  ollamaHost = '';
  preset = 'research';
  ceiling: number | null = 5;
  globalDefault = 'openrouter:meta-llama/llama-3.3-70b-instruct';

  savingKeys = signal(false);
  savingPrefs = signal(false);
  keysMsg = signal<string | null>(null);
  prefsMsg = signal<string | null>(null);
  presetOverrides = signal<Record<string, string>>({});

  ngOnInit(): void {
    this.store.loadAll().subscribe(() => {
      const keys = this.store.keys();
      if (keys) this.ollamaHost = keys.ollama_host ?? '';
      const prefs = this.store.prefs();
      if (prefs) {
        this.preset = prefs.preset ?? 'research';
        this.ceiling = prefs.cost_ceiling_per_run_usd
          ? Number(prefs.cost_ceiling_per_run_usd) : null;
        // If every agent shares one model in per_agent_defaults, treat that as
        // the active "global default" — otherwise leave the selector blank so
        // the preset rules apply.
        const vals = Object.values(prefs.per_agent_defaults ?? {});
        const allSame = vals.length > 0 && vals.every((v) => v === vals[0]);
        this.globalDefault = allSame ? (vals[0] as string) : '';
      }
      this.loadPresetOverrides();
    });
  }

  applyGlobalDefault(modelId: string): void {
    const next: Record<string, string> = {};
    if (modelId) {
      for (const a of this.store.agents()) next[a.id] = modelId;
    }
    this.store.savePrefs({ per_agent_defaults: next }).subscribe(() => {
      this.prefsMsg.set(modelId ? 'Default model applied to all agents.' : 'Cleared.');
    });
  }

  loadPresetOverrides(): void {
    this.store.fetchPreset(this.preset).subscribe((r) => {
      this.presetOverrides.set(r.overrides);
    });
  }

  resolvedDefault(a: string): string {
    const override = this.agentDefault(a);
    if (override) {
      const m = this.store.models().find((x) => x.id === override);
      return (m?.display_name ?? override) + ' (override)';
    }
    const fromPreset = this.presetOverrides()[a];
    if (fromPreset) {
      const m = this.store.models().find((x) => x.id === fromPreset);
      return m?.display_name ?? fromPreset;
    }
    const sysDefault = this.store.agents().find((x) => x.id === a)?.default_model;
    if (sysDefault) {
      const m = this.store.models().find((x) => x.id === sysDefault);
      return (m?.display_name ?? sysDefault) + ' (system)';
    }
    return '—';
  }

  display(a: string): string { return AGENT_DISPLAY[a] ?? a; }
  agentIds(): string[] { return this.store.agents().map((a) => a.id); }
  groupLabel(g: string): string { return GROUP_LABEL[g] ?? g; }
  groupedAgents(): { group: string; agents: string[] }[] {
    const order = ['persona', 'analyst', 'context', 'orchestration', 'other'];
    const buckets: Record<string, string[]> = {};
    for (const a of this.store.agents()) {
      (buckets[a.group ?? 'other'] ??= []).push(a.id);
    }
    return order
      .filter((g) => buckets[g]?.length)
      .map((g) => ({ group: g, agents: buckets[g] }));
  }
  agentDefault(a: string): string {
    return this.store.prefs()?.per_agent_defaults?.[a] ?? '';
  }
  setAgentDefault(a: string, v: string): void {
    const prefs = this.store.prefs();
    const next = { ...(prefs?.per_agent_defaults ?? {}) };
    if (v) next[a] = v; else delete next[a];
    this.store.savePrefs({ per_agent_defaults: next }).subscribe();
  }
  statusOf(field: string): string {
    return (this.store.keys() as Record<string, string> | null)?.[field] ?? 'unset';
  }
  saveKeys(): void {
    this.savingKeys.set(true);
    const body: Record<string, string> = { ollama_host: this.ollamaHost };
    for (const f of ['anthropic', 'openrouter', 'openai']) {
      if (this.keyEdits[f]) body[`${f}_api_key`] = this.keyEdits[f];
    }
    this.store.saveKeys(body).subscribe({
      next: () => {
        this.savingKeys.set(false);
        this.keysMsg.set('Saved. Reloading models…');
        this.keyEdits = { anthropic: '', openrouter: '', openai: '' };
        this.store.loadModels().subscribe();
      },
      error: () => { this.savingKeys.set(false); this.keysMsg.set('Failed to save'); },
    });
  }
  savePrefs(): void {
    this.savingPrefs.set(true);
    this.store.savePrefs({
      preset: this.preset,
      cost_ceiling_per_run_usd: this.ceiling,
    }).subscribe({
      next: () => { this.savingPrefs.set(false); this.prefsMsg.set('Saved.'); },
      error: () => { this.savingPrefs.set(false); this.prefsMsg.set('Failed to save'); },
    });
  }
}
