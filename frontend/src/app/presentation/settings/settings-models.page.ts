import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { ModelsStore } from '../../abstraction/models.store';
import { AGENT_DISPLAY, GROUP_LABEL, PRESET_NAMES } from '../../core/models/model.types';

interface Preset { id: string; name: string; desc: string; cost: number; runtime: string }

const PERSONA_COL: Record<string, string> = {
  buffett: 'c1', munger: 'c2', graham: 'c3', wood: 'c4',
  druckenmiller: 'c5', burry: 'c6', damodaran: 'c7', lynch: 'c8',
};
const PERSONA_MONO: Record<string, string> = {
  buffett: 'WB', munger: 'CM', graham: 'BG', wood: 'CW',
  druckenmiller: 'SD', burry: 'MB', damodaran: 'AD', lynch: 'PL',
};

@Component({
  selector: 'hf-settings-models',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Settings'}, {label:'Models'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Configure which model runs which agent</div>
          <h1 style="margin-top:6px">Models</h1>
          <p style="color:var(--text-2);font-size:13px;margin-top:6px;max-width:560px">
            Pick a preset, then override per-agent if needed. Costs estimate live against the council you set in runs and strategies.
          </p>
        </div>
        <div class="head-actions">
          <button class="btn ghost">Discard</button>
          <button class="btn primary" (click)="savePrefs()" [disabled]="savingPrefs()">{{ savingPrefs() ? 'Saving…' : 'Save changes' }}</button>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:180px 1fr;gap:28px">
        <nav style="display:flex;flex-direction:column;gap:2px;position:sticky;top:64px;align-self:flex-start">
          <a class="row" style="padding:8px 10px;border-radius:6px;background:var(--surface-2);border-left:2px solid var(--acc-info);font-size:13px;color:var(--text)">
            <svg width="14" height="14"><use href="/icons.svg#i-cpu"/></svg> Models
          </a>
          <a class="row" style="padding:8px 10px;color:var(--text-3);font-size:13px"><svg width="14" height="14"><use href="/icons.svg#i-key"/></svg> API keys</a>
          <a class="row" style="padding:8px 10px;color:var(--text-3);font-size:13px"><svg width="14" height="14"><use href="/icons.svg#i-shield"/></svg> Risk policies</a>
          <a class="row" style="padding:8px 10px;color:var(--text-3);font-size:13px"><svg width="14" height="14"><use href="/icons.svg#i-layers"/></svg> Universes</a>
          <a class="row" style="padding:8px 10px;color:var(--text-3);font-size:13px"><svg width="14" height="14"><use href="/icons.svg#i-bell"/></svg> Notifications</a>
          <a class="row" style="padding:8px 10px;color:var(--text-3);font-size:13px"><svg width="14" height="14"><use href="/icons.svg#i-settings"/></svg> Workspace</a>
        </nav>

        <div style="display:flex;flex-direction:column;gap:18px;min-width:0">
          <!-- 1 · Preset -->
          <section class="card">
            <div class="card-hd">
              <span class="title">Preset</span>
              <span class="pill"><span class="dot"></span>{{ preset }} · per-agent override on {{ overrideCount() }} agents</span>
            </div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px">
              @for(p of presetCards; track p.id){
                <button (click)="selectPreset(p.id)"
                  style="text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;flex-direction:column;gap:6px;cursor:pointer;position:relative"
                  [style.borderColor]="preset===p.id ? 'var(--acc-info)' : 'var(--border)'"
                  [style.background]="preset===p.id ? 'var(--surface-2)' : 'var(--surface)'">
                  @if(preset===p.id){<span style="position:absolute;top:10px;right:10px;color:var(--acc-info)"><svg width="14" height="14"><use href="/icons.svg#i-check"/></svg></span>}
                  <span class="eyebrow">{{ p.name }}</span>
                  <span style="font-size:12.5px;color:var(--text-2)">{{ p.desc }}</span>
                  <span class="mono" style="font-size:12px;color:var(--text)">$ {{ p.cost.toFixed(2) }} / run · {{ p.runtime }}</span>
                </button>
              }
            </div>
            <div style="border-top:1px solid var(--border);padding:14px 16px;display:flex;flex-direction:column;gap:10px">
              <div class="eyebrow">Monthly cost ceiling</div>
              <div style="display:flex;align-items:center;justify-content:space-between">
                <div class="mono" style="font-size:22px;color:var(--text)">$ 412.10 <span style="color:var(--text-3)">/ $ {{ (ceiling || 0) * 160 }}.00</span> · 51.5%</div>
                <div style="display:flex;gap:6px">
                  <input type="number" step="0.5" min="0" class="input" style="width:120px" [(ngModel)]="ceiling" />
                  <button class="btn ghost sm">View history</button>
                </div>
              </div>
              <div class="cost-gauge"><div class="fill" style="width:51.5%"></div><div class="mark" style="left:51.5%"></div></div>
              <div class="mono" style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-3)">
                <span>$ 0</span><span>warn $ 600</span><span>cap $ 760</span><span>$ 800</span>
              </div>
              <p style="font-size:11.5px;color:var(--text-3)">
                Soft warn at 75%; new runs auto-queue once 95% is hit. Backtests over the ceiling are rejected.
              </p>
            </div>
          </section>

          <!-- 2 · Providers -->
          <section class="card">
            <div class="card-hd">
              <span class="title">Providers &amp; keys</span>
              <span class="pill"><span class="dot"></span>stored encrypted · per workspace</span>
              <div class="actions"><button class="btn ghost sm">+ Add provider</button></div>
            </div>
            <div>
              @for(p of providerRows(); track p.field){
                <div style="display:grid;grid-template-columns:auto 1fr auto auto;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border)">
                  <span style="width:28px;height:28px;border-radius:6px;display:grid;place-items:center;font-family:var(--font-mono);font-size:13px;font-weight:600"
                    [style.background]="p.color + '22'" [style.color]="p.color" [style.border]="'1px solid ' + p.color + '55'">{{ p.letter }}</span>
                  <div>
                    <div style="font-size:13px;color:var(--text)">{{ p.label }}</div>
                    <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:2px">{{ p.models }}</div>
                  </div>
                  <span class="pill mono" [class.ok]="statusOf(p.field)==='set'">
                    <svg width="11" height="11"><use href="/icons.svg#i-key"/></svg>
                    {{ statusOf(p.field)==='set' ? '••• stored' : 'not configured' }}
                  </span>
                  <div style="display:flex;gap:6px">
                    <input type="password" [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                      placeholder="paste to replace" class="input"
                      style="width:180px;font-size:11.5px" />
                    <button class="btn sm" (click)="saveKeys()">{{ savingKeys() ? '…' : 'Save' }}</button>
                  </div>
                </div>
              }
              <div style="display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;padding:12px 16px">
                <span style="width:28px;height:28px;border-radius:6px;display:grid;place-items:center;font-family:var(--font-mono);font-size:13px;font-weight:600;background:var(--acc-hold-soft);color:var(--acc-hold-fg);border:1px solid var(--acc-hold-soft)">L</span>
                <div>
                  <div style="font-size:13px;color:var(--text)">Ollama · local host</div>
                  <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:2px">llama-3.3-70b · qwen-2.5-32b · phi-4</div>
                </div>
                <div style="display:flex;gap:6px">
                  <input class="input mono sans" [(ngModel)]="ollamaHost" placeholder="http://localhost:11434" data-test="ollama-host" style="width:240px" />
                  <button class="btn sm">Test conn</button>
                </div>
              </div>
            </div>
          </section>

          <!-- 3 · Per-agent matrix -->
          <section class="card">
            <div class="card-hd">
              <span class="title">Per-agent override matrix</span>
              <span class="pill"><span class="dot"></span>{{ store.agents().length }} agents · {{ overrideCount() }} overrides</span>
              <div class="actions">
                <button class="btn ghost sm">Reset overrides</button>
                <span class="mono" style="font-size:11.5px;color:var(--text-3)">est. per run · $ {{ estCost() }}</span>
              </div>
            </div>
            <div>
              @for(group of groupedAgents(); track group.group){
                <div class="eyebrow" style="padding:10px 16px;background:var(--surface-2);border-bottom:1px solid var(--border)">{{ groupLabel(group.group) }} · {{ group.agents.length }} agents</div>
                @for(a of group.agents; track a){
                  <div style="display:grid;grid-template-columns:28px 1fr auto 220px auto auto;gap:12px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--border)"
                    [style.borderLeft]="agentDefault(a) ? '2px solid var(--acc-info)' : '2px solid transparent'">
                    <span class="mono-tile" [class]="'mt-' + (PERSONA_COL[a] || '')">{{ PERSONA_MONO[a] || a.slice(0,2).toUpperCase() }}</span>
                    <div>
                      <div style="font-size:13px;color:var(--text)">{{ display(a) }}</div>
                      <div class="mono" style="font-size:10.5px;color:var(--text-3);margin-top:2px">{{ a }}</div>
                    </div>
                    <span class="pill mono"
                      [class.info]="tierOf(a)==='frontier'" [class.ok]="tierOf(a)==='fast_cheap'" [class.warn]="tierOf(a)==='local'">
                      <span class="dot"></span>{{ tierOf(a) }}
                    </span>
                    <select [(ngModel)]="overrides[a]" (ngModelChange)="setAgentDefault(a, $event)" [name]="'agent-' + a" class="input sans" style="height:28px">
                      <option value="">— use preset —</option>
                      @for(m of store.models(); track m.id){
                        <option [value]="m.id" [disabled]="!m.available">{{ m.display_name }}{{ m.available ? '' : ' (no key)' }}</option>
                      }
                    </select>
                    <span class="mono" style="font-size:11.5px;color:var(--text-3)">$ 0.46 · 200k</span>
                    <button class="icon-btn"><svg width="12" height="12"><use href="/icons.svg#i-rerun"/></svg></button>
                  </div>
                }
              }
            </div>
          </section>

          <!-- 4 · Model catalog -->
          <section class="card">
            <div class="card-hd">
              <span class="title">Available models</span>
              <span class="pill"><span class="dot"></span>{{ store.models().length }} models · 4 providers</span>
            </div>
            <table class="tbl">
              <thead><tr>
                <th>Provider</th><th>Model</th><th>Tier</th>
                <th class="right">Ctx</th><th class="right">$/M in</th><th class="right">$/M out</th><th>Notes</th>
              </tr></thead>
              <tbody>
                @for(m of store.models(); track m.id){
                  <tr>
                    <td style="color:var(--text-2)">{{ m.provider }}</td>
                    <td class="mono" style="color:var(--text)">{{ m.display_name }}</td>
                    <td><span class="pill mono"
                      [class.info]="m.tier==='frontier'" [class.ok]="m.tier==='fast_cheap'" [class.warn]="m.tier==='local'">
                      <span class="dot"></span>{{ m.tier }}</span></td>
                    <td class="num">{{ m.context_window || '—' }}</td>
                    <td class="num">{{ m.price_in_per_mtok ?? '—' }}</td>
                    <td class="num">{{ m.price_out_per_mtok ?? '—' }}</td>
                    <td style="color:var(--text-3);font-size:11.5px">{{ m.available ? '' : 'no key' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </hf-app-shell>
  `,
})
export class SettingsModelsPage implements OnInit {
  readonly store = inject(ModelsStore);
  readonly PERSONA_COL = PERSONA_COL;
  readonly PERSONA_MONO = PERSONA_MONO;
  readonly providers = [
    { field: 'anthropic', label: 'Anthropic', letter: 'A', color: '#FF6B35', models: 'claude-haiku-4.5 · sonnet-4.5 · opus-4.1' },
    { field: 'openai', label: 'OpenAI', letter: 'O', color: '#10A37F', models: 'gpt-5 · gpt-4o · gpt-4o-mini' },
    { field: 'openrouter', label: 'OpenRouter', letter: 'R', color: '#5B8DEF', models: 'hosted llama-3.1 · qwen-2.5 · mixtral' },
  ] as const;
  readonly presets = PRESET_NAMES;
  readonly presetCards: Preset[] = [
    { id: 'frugal', name: 'Frugal', desc: 'All agents on fast-cheap tier.', cost: 0.32, runtime: '~12s' },
    { id: 'research', name: 'Research', desc: 'Council + CIO on frontier.', cost: 4.21, runtime: '~38s' },
    { id: 'quality', name: 'Quality', desc: 'Every agent on frontier.', cost: 12.40, runtime: '~84s' },
    { id: 'hybrid', name: 'Hybrid · BYO', desc: 'Local Ollama + frontier council.', cost: 1.02, runtime: '~22s' },
  ];

  keyEdits: Record<string, string> = { anthropic: '', openrouter: '', openai: '' };
  ollamaHost = '';
  preset = 'research';
  ceiling: number | null = 5;
  overrides: Record<string, string> = {};

  savingKeys = signal(false);
  savingPrefs = signal(false);
  keysMsg = signal<string | null>(null);
  prefsMsg = signal<string | null>(null);
  presetOverrides = signal<Record<string, string>>({});

  providerRows = () => this.providers;
  overrideCount = computed(() => Object.values(this.overrides).filter(Boolean).length);
  estCost = computed(() => this.presetCards.find((p) => p.id === this.preset)?.cost.toFixed(2) ?? '4.21');

  ngOnInit(): void {
    this.store.loadAll().subscribe(() => {
      const keys = this.store.keys();
      if (keys) this.ollamaHost = keys.ollama_host ?? '';
      const prefs = this.store.prefs();
      if (prefs) {
        this.preset = prefs.preset ?? 'research';
        this.ceiling = prefs.cost_ceiling_per_run_usd ? Number(prefs.cost_ceiling_per_run_usd) : null;
        this.overrides = { ...(prefs.per_agent_defaults || {}) };
      }
      this.loadPresetOverrides();
    });
  }

  selectPreset(id: string) {
    this.preset = id;
    this.loadPresetOverrides();
  }

  loadPresetOverrides(): void {
    this.store.fetchPreset(this.preset).subscribe((r) => this.presetOverrides.set(r.overrides));
  }

  tierOf(a: string): string {
    const id = this.overrides[a] || this.presetOverrides()[a] || this.store.agents().find((x) => x.id === a)?.default_model;
    if (!id) return 'fast_cheap';
    if (typeof id === 'string' && id.includes('ollama')) return 'local';
    const m = this.store.models().find((x) => x.id === id);
    return m?.tier ?? 'fast_cheap';
  }

  display(a: string): string { return AGENT_DISPLAY[a] ?? a; }
  groupLabel(g: string): string { return GROUP_LABEL[g] ?? g; }
  groupedAgents(): { group: string; agents: string[] }[] {
    const order = ['persona', 'analyst', 'context', 'orchestration', 'other'];
    const buckets: Record<string, string[]> = {};
    for (const a of this.store.agents()) (buckets[a.group ?? 'other'] ??= []).push(a.id);
    return order.filter((g) => buckets[g]?.length).map((g) => ({ group: g, agents: buckets[g] }));
  }
  agentDefault(a: string): string { return this.overrides[a] || ''; }
  setAgentDefault(a: string, v: string): void {
    if (v) this.overrides[a] = v; else delete this.overrides[a];
    this.store.savePrefs({ per_agent_defaults: this.overrides }).subscribe();
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
        this.keysMsg.set('Saved.');
        this.keyEdits = { anthropic: '', openrouter: '', openai: '' };
        this.store.loadModels().subscribe();
      },
      error: () => { this.savingKeys.set(false); this.keysMsg.set('Failed to save'); },
    });
  }
  savePrefs(): void {
    this.savingPrefs.set(true);
    this.store.savePrefs({ preset: this.preset, cost_ceiling_per_run_usd: this.ceiling }).subscribe({
      next: () => { this.savingPrefs.set(false); this.prefsMsg.set('Saved.'); },
      error: () => { this.savingPrefs.set(false); this.prefsMsg.set('Failed to save'); },
    });
  }
}
