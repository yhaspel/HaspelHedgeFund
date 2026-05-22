import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AppShellComponent } from '../shared/app-shell.component';
import { ModelsStore } from '../../abstraction/models.store';
import { AGENT_DISPLAY, GROUP_LABEL, PRESET_NAMES } from '../../core/models/model.types';

@Component({
  selector: 'hf-settings-models',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Settings'}, {label:'Models'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 style="margin-top:6px">Models</h1>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;max-width:1100px">
        <!-- A: Provider keys (BYO) -->
        <section class="card">
          <div class="card-hd"><span class="title">Provider keys (BYO)</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
            <p style="font-size:11.5px;color:var(--text-3);margin:0">
              Keys are stored Fernet-encrypted on your user row. P4a will replace this with a multi-tenant vault.
            </p>

            <div class="eyebrow" style="border-bottom:1px solid var(--border);padding-bottom:6px">
              LLM providers
            </div>
            @for (p of providers; track p.field) {
              <div class="field">
                <label class="lbl" style="display:flex;justify-content:space-between">
                  <span>{{ p.label }}</span>
                  <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                    <span class="dot"></span>{{ statusOf(p.field) }}
                  </span>
                </label>
                <input class="input mono" type="password" [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                  [attr.data-test]="'llm-key-' + p.field"
                  placeholder="•••• paste to replace, blank to keep" />
              </div>
            }
            <div class="field">
              <label class="lbl">Ollama host (for local models)</label>
              <input class="input mono" type="text" [(ngModel)]="ollamaHost" name="ollama"
                placeholder="http://localhost:11434" data-test="ollama-host" />
            </div>

            <div class="eyebrow" style="border-bottom:1px solid var(--border);padding-bottom:6px;margin-top:6px">
              Data providers
            </div>
            <p style="font-size:11.5px;color:var(--text-3);margin:0">
              P2n BYOK: FMP and Tiingo require user-supplied keys (no platform fallback in prod). FRED is optional — free public data falls back to a shared platform key.
            </p>
            @for (p of dataProviders; track p.field) {
              <div class="field">
                <label class="lbl" style="display:flex;justify-content:space-between">
                  <span>{{ p.label }}</span>
                  <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                    <span class="dot"></span>{{ statusOf(p.field) }}
                  </span>
                </label>
                <input class="input mono" type="password" [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                  [attr.data-test]="'data-key-' + p.field"
                  placeholder="•••• paste to replace, blank to keep" />
                <p style="font-size:11px;color:var(--text-3);margin:2px 0 0">{{ p.note }}</p>
              </div>
            }

            <button type="button" class="btn primary" (click)="saveKeys()" [disabled]="savingKeys()"
              data-test="save-keys"
              style="height:32px;justify-content:center">
              {{ savingKeys() ? 'Saving…' : 'Save provider keys' }}
            </button>
            @if (keysMsg()) {
              <p style="font-size:11.5px;color:var(--acc-long-fg);margin:0" data-test="keys-msg">{{ keysMsg() }}</p>
            }
          </div>
        </section>

        <!-- B: Default model + preset + ceiling -->
        <section class="card">
          <div class="card-hd"><span class="title">Defaults &amp; cost ceiling</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
            <div class="field">
              <label class="lbl">Default model (applies to every agent)</label>
              <select class="input sans" [(ngModel)]="globalDefault" name="globalDefault"
                (ngModelChange)="applyGlobalDefault($event)" data-test="global-default-select">
                <option value="">— use preset per-agent rules —</option>
                @for (m of store.models(); track m.id) {
                  <option [value]="m.id" [disabled]="!m.available">
                    {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                  </option>
                }
              </select>
              <p style="font-size:11.5px;color:var(--text-3);margin:4px 0 0">
                Sets the same model for every agent. Clear to fall back to the preset rules below.
              </p>
            </div>

            <div class="field">
              <label class="lbl">Preset (used when no global default is set)</label>
              <select class="input sans" [(ngModel)]="preset" name="preset"
                (ngModelChange)="loadPresetOverrides()" data-test="preset-select">
                @for (p of presets; track p) {
                  <option [value]="p">{{ p }}</option>
                }
              </select>
            </div>

            <div class="field">
              <label class="lbl">Cost ceiling per scheduled run (USD)</label>
              <input class="input" type="number" step="0.5" min="0" [(ngModel)]="ceiling" name="ceil" />
            </div>

            <!-- P02d review: disable Save when no dirty change so the
                 button reflects whether there is anything to commit. -->
            <button type="button" class="btn primary"
              (click)="savePrefs()"
              [disabled]="savingPrefs() || !isPrefsDirty()"
              data-test="save-prefs" style="height:32px;justify-content:center">
              {{ savingPrefs() ? 'Saving…' : (isPrefsDirty() ? 'Save preferences' : 'No changes') }}
            </button>
            @if (prefsMsg()) {
              <p style="font-size:11.5px;color:var(--acc-long-fg);margin:0">{{ prefsMsg() }}</p>
            }
          </div>
        </section>

        <!-- C: per-agent defaults -->
        <section class="card" style="grid-column:span 2">
          <div class="card-hd"><span class="title">Per-agent defaults</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:14px">
            <p style="font-size:11.5px;color:var(--text-3);margin:0">
              "Current default" = what the active preset (<b>{{ preset }}</b>) resolves to. Pick an explicit model to override it for this agent.
            </p>
            <p style="font-size:11.5px;color:var(--text-3);margin:0;font-style:italic" data-test="per-agent-autosave-note">
              Per-agent selections save automatically. Preset and cost ceiling save via the button above.
            </p>
            @for (g of groupedAgents(); track g.group) {
              <div>
                <div class="eyebrow" style="border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:8px">
                  {{ groupLabel(g.group) }} ({{ g.agents.length }})
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 24px">
                  @for (a of g.agents; track a) {
                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;align-items:center;font-size:13px">
                      <div>{{ display(a) }}</div>
                      <div class="mono" style="font-size:11.5px"
                        [style.color]="agentDefault(a) ? 'var(--text-3)' : 'var(--text-2)'">
                        {{ resolvedDefault(a) }}
                      </div>
                      <select class="input sans"
                        [ngModel]="agentDefault(a)"
                        (ngModelChange)="setAgentDefault(a, $event)"
                        style="height:26px;font-size:11.5px;padding:0 6px">
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
        <section class="card" style="grid-column:span 2">
          <div class="card-hd"><span class="title">Available models ({{ store.models().length }})</span></div>
          <table class="tbl">
            <thead><tr>
              <th>Model</th><th>Provider</th><th>Tier</th>
              <th class="right">$/Mtok in</th><th class="right">$/Mtok out</th><th>Status</th>
            </tr></thead>
            <tbody>
              @for (m of store.models(); track m.id) {
                <tr>
                  <td class="mono" style="color:var(--text)">{{ m.display_name }}</td>
                  <td>{{ m.provider }}</td>
                  <td>{{ m.tier }}</td>
                  <td class="num">{{ m.price_in_per_mtok ?? '0' }}</td>
                  <td class="num">{{ m.price_out_per_mtok ?? '0' }}</td>
                  <td>
                    <span class="pill" [class.ok]="m.available">
                      <span class="dot"></span>{{ m.available ? 'available' : 'no key' }}
                    </span>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      </div>
    </hf-app-shell>
  `,
})
export class SettingsModelsPage implements OnInit {
  readonly store = inject(ModelsStore);
  readonly providers = [
    { field: 'anthropic', label: 'Anthropic API key' },
    { field: 'openrouter', label: 'OpenRouter API key' },
    { field: 'openai', label: 'OpenAI API key' },
  ] as const;
  readonly dataProviders = [
    {
      field: 'fmp',
      label: 'FMP API key',
      note: 'Required for backtests and live agent runs. Sign up at financialmodelingprep.com.',
    },
    {
      field: 'tiingo',
      label: 'Tiingo API key',
      note: 'Required for news features. Free tier available at tiingo.com.',
    },
    {
      field: 'fred',
      label: 'FRED API key',
      note: 'Optional — defaults to a shared platform key. Set your own for isolation or higher rate limits.',
    },
  ] as const;
  readonly presets = PRESET_NAMES;

  keyEdits: Record<string, string> = {
    anthropic: '', openrouter: '', openai: '',
    fmp: '', tiingo: '', fred: '',
  };
  ollamaHost = '';
  preset = 'research';
  ceiling: number | null = 5;
  globalDefault = 'openrouter:meta-llama/llama-3.3-70b-instruct';

  savingKeys = signal(false);
  savingPrefs = signal(false);
  keysMsg = signal<string | null>(null);
  prefsMsg = signal<string | null>(null);
  presetOverrides = signal<Record<string, string>>({});
  // P02d review: track which preset/ceiling values are persisted so the
  // Save button can be disabled when nothing is dirty (consistent save UX).
  private savedPreset = 'research';
  private savedCeiling: number | null = 5;

  isPrefsDirty(): boolean {
    return this.preset !== this.savedPreset || this.ceiling !== this.savedCeiling;
  }

  ngOnInit(): void {
    this.store.loadAll().subscribe(() => {
      const keys = this.store.keys();
      if (keys) this.ollamaHost = keys.ollama_host ?? '';
      const prefs = this.store.prefs();
      if (prefs) {
        this.preset = prefs.preset ?? 'research';
        this.ceiling = prefs.cost_ceiling_per_run_usd
          ? Number(prefs.cost_ceiling_per_run_usd) : null;
        // Snapshot persisted values so isPrefsDirty() works.
        this.savedPreset = this.preset;
        this.savedCeiling = this.ceiling;
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
    return order.filter((g) => buckets[g]?.length).map((g) => ({ group: g, agents: buckets[g] }));
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
    const allFields = [
      'anthropic', 'openrouter', 'openai',
      'fmp', 'tiingo', 'fred',
    ];
    for (const f of allFields) {
      if (this.keyEdits[f]) body[`${f}_api_key`] = this.keyEdits[f];
    }
    this.store.saveKeys(body).subscribe({
      next: () => {
        this.savingKeys.set(false);
        this.keysMsg.set('Saved. Reloading models…');
        this.keyEdits = {
          anthropic: '', openrouter: '', openai: '',
          fmp: '', tiingo: '', fred: '',
        };
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
      next: () => {
        this.savingPrefs.set(false);
        this.prefsMsg.set('Saved.');
        this.savedPreset = this.preset;
        this.savedCeiling = this.ceiling;
      },
      error: () => { this.savingPrefs.set(false); this.prefsMsg.set('Failed to save'); },
    });
  }
}
