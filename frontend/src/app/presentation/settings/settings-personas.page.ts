import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';
import { ModelsStore } from '../../abstraction/models.store';
import { PersonaEvolutionStore } from '../../abstraction/persona-evolution.store';
import { PersonaEvolutionSettings as EvolutionSettings } from '../../core/models/persona-evolution.model';

/**
 * Settings › Personas — persona evolution settings + the persona roster.
 *
 * Split out of the former monolithic settings-models page (section "D4").
 * Mirrors the mockup's two-card layout (settings card + roster table) while
 * preserving every binding, the autosave/dirty/run-now behavior, the
 * cost-cap-reached badge, frozen-persona rows and all evo-* data-test hooks.
 */
@Component({
  selector: 'hf-settings-personas',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent, SettingsTabsComponent],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Settings', link: '/settings/models' }, { label: 'Personas' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">Personas</h1>
        </div>
      </div>

      <hf-settings-tabs />

      <div role="tabpanel" aria-label="Personas settings" class="flex flex-col gap-[18px] max-w-[1100px]">
        <!-- Persona evolution settings -->
        <section class="card" data-test="persona-evolution-card">
          <div class="card-hd flex items-center justify-between">
            <h2 class="title">Persona evolution</h2>
            @if (evoSettings()?.cost_cap_reached_at) {
              <span class="pill" style="background: var(--acc-short-soft); color: var(--acc-short-fg);" data-test="evo-cap-reached">
                <span class="dot"></span>monthly cost cap reached
              </span>
            }
          </div>
          <div class="card-bd flex flex-col gap-3.5">
            <p class="text-[11.5px] text-text-3 m-0 max-w-[640px]">
              Periodically refresh a short, dated note on what each living-investor
              persona has done and said in the real world, and inject it into the
              persona's LLM call at run time. Backtests are never touched, and the
              core persona prompt is never modified.
              <a routerLink="/runs" class="text-[var(--acc-info-fg)] underline">Run detail</a>
              shows an "Evolved" badge when a note was applied.
            </p>

            <div class="evo-grid">
              <div class="field">
                <label class="lbl flex items-center justify-between" for="evo-enabled">
                  <span>Enable persona evolution</span>
                  <input id="evo-enabled" type="checkbox"
                         [(ngModel)]="evoForm.enabled" name="evo_enabled"
                         data-test="evo-enabled" />
                </label>
                <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                  Default off. Six personas evolve (Buffett, Wood, Druckenmiller,
                  Burry, Damodaran, Lynch); Graham and Munger stay canon.
                </p>
              </div>

              <div class="field">
                <label class="lbl flex items-center justify-between" for="evo-web">
                  <span>Web search (OpenRouter :online)</span>
                  <input id="evo-web" type="checkbox"
                         [(ngModel)]="evoForm.web_search_enabled" name="evo_web"
                         [disabled]="!evoForm.enabled"
                         data-test="evo-web" />
                </label>
                <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                  When off, only the FMP/Tiingo news filter feeds the merge call.
                </p>
              </div>

              <div class="field">
                <label class="lbl" for="evo-cadence">Cadence</label>
                <select id="evo-cadence" class="input sans"
                        [(ngModel)]="evoForm.cadence" name="evo_cadence"
                        [disabled]="!evoForm.enabled"
                        data-test="evo-cadence">
                  <option value="off">Off</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>

              <div class="field">
                <label class="lbl" for="evo-model">Evolution model</label>
                <select id="evo-model" class="input sans"
                        [(ngModel)]="evoForm.model_id" name="evo_model"
                        [disabled]="!evoForm.enabled"
                        data-test="evo-model">
                  <option value="">— Llama 3.3 70B (recommended) —</option>
                  @for (m of evoModelChoices(); track m.id) {
                    <option [value]="m.id" [disabled]="!m.available">
                      {{ m.display_name }} · {{ m.tier }}{{ m.available ? '' : ' (no key)' }}
                    </option>
                  }
                </select>
                <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                  Anthropic models are excluded in dev. Frugal OpenRouter models only.
                </p>
              </div>

              <div class="field">
                <label class="lbl" for="evo-cap">Monthly cost cap (USD)</label>
                <input id="evo-cap" class="input mono" type="number" step="0.5" min="0"
                       [(ngModel)]="evoForm.monthly_cost_cap_usd" name="evo_cap"
                       [disabled]="!evoForm.enabled"
                       data-test="evo-cap" />
                <p class="text-[11.5px] text-text-3 m-0 mt-0.5">
                  Hard kill-switch. When reached, the task skips all cycles until next month.
                  Month-to-date: {{ evoMtdCostLabel() }}.
                </p>
              </div>
            </div>

            <div class="flex gap-2">
              <button type="button" class="btn primary save-btn save-btn--portfolio"
                      (click)="saveEvoSettings()"
                      [disabled]="savingEvo() || !isEvoDirty()"
                      data-test="save-evo">
                {{ savingEvo() ? 'Saving…' : (isEvoDirty() ? 'Save persona evolution' : 'No changes') }}
              </button>
              <button type="button" class="btn"
                      (click)="runEvoNow()"
                      [disabled]="evoStore.running() || !evoSettings()?.enabled"
                      data-test="evo-run-now">
                {{ evoStore.running() ? 'Running…' : 'Run evolution now' }}
              </button>
            </div>
            @if (evoStore.running() && evoStore.currentPersonaLabel(); as label) {
              <p role="status" aria-live="polite" class="text-[11.5px] text-text-2 m-0" data-test="evo-current-persona">
                Analysing: <b>{{ label }}</b>
              </p>
            }
            @if (evoMsg()) {
              <p role="status" aria-live="polite" class="text-[11.5px] text-[var(--acc-long-fg)] m-0" data-test="evo-msg">{{ evoMsg() }}</p>
            }
            @if (evoStore.runMsg()) {
              <p role="status" aria-live="polite" class="text-[11.5px] text-text-2 m-0" data-test="evo-run-msg">{{ evoStore.runMsg() }}</p>
            }
          </div>
        </section>

        <!-- Persona roster -->
        <section class="card">
          <div class="card-hd"><h2 class="title">Personas</h2></div>
          <div class="card-bd">
            <table class="tbl" data-test="evo-persona-table">
              <thead><tr>
                <th>Persona</th><th>Status</th><th>Last cycle</th>
                <th>Latest revision</th><th></th>
              </tr></thead>
              <tbody>
                @for (p of evoStore.profiles(); track p.persona_name) {
                  <tr [attr.data-test-evo-row]="p.persona_name"
                      [class.evo-frozen]="!p.is_evolvable">
                    <td>
                      <div>{{ p.display_name }}</div>
                      @if (p.firm_name) {
                        <div class="text-[11px] text-text-3">{{ p.firm_name }}</div>
                      }
                      @if (!p.is_evolvable) {
                        <div class="text-[11px] text-text-3 italic">
                          Frozen — philosophy is canon{{ p.lifecycle_note ? ' (' + p.lifecycle_note + ')' : '' }}
                        </div>
                      }
                    </td>
                    <td>
                      @if (p.current_cycle_started_at) {
                        <span class="pill"
                              style="background: var(--acc-info-soft); color: var(--acc-info-fg);"
                              [attr.data-test-status]="p.persona_name">
                          <span class="dot"></span>running…
                        </span>
                      } @else {
                        <span class="pill"
                              [class.ok]="p.last_cycle_status === 'ok'"
                              [attr.data-test-status]="p.persona_name">
                          <span class="dot"></span>{{ p.last_cycle_status }}
                        </span>
                      }
                    </td>
                    <td class="text-[11.5px] text-text-3">
                      {{ p.last_cycle_at ? relTime(p.last_cycle_at) : '—' }}
                    </td>
                    <td class="text-[11.5px]">
                      @if (p.current_revision; as rev) {
                        seq {{ rev.seq }} · {{ rev.as_of_date }} ·
                        {{ rev.char_count }} chars{{ rev.over_budget ? ' (over budget)' : '' }}
                      } @else {
                        <span class="text-text-3">no revisions</span>
                      }
                    </td>
                    <td>
                      @if (p.is_evolvable) {
                        <button type="button" class="btn btn-xs"
                                (click)="toggleViewRevisions(p.persona_name)"
                                [attr.data-test]="'evo-view-' + p.persona_name">
                          {{ revisionsOpenFor() === p.persona_name ? 'Hide' : 'View' }}
                        </button>
                      }
                    </td>
                  </tr>
                  @if (revisionsOpenFor() === p.persona_name && evoStore.revisions(); as rv) {
                    <tr>
                      <td colspan="5">
                        <div class="rev-block" [attr.data-test]="'evo-revisions-' + p.persona_name">
                          @if (rv.items.length === 0) {
                            <p class="text-[11.5px] text-text-3 m-0">No revisions yet.</p>
                          }
                          @for (r of rv.items; track r.id) {
                            <details class="rev-item">
                              <summary>
                                <b>seq {{ r.seq }}</b> · {{ r.as_of_date }} ·
                                {{ r.char_count }} chars
                                @if (r.over_budget) {
                                  <span class="badge-free" style="background: var(--acc-short-soft); color: var(--acc-short-fg);">truncated</span>
                                }
                                @if (!r.material_change) {
                                  <span class="badge-free">no-change</span>
                                }
                              </summary>
                              <div class="rev-body">
                                <pre class="rev-md">{{ r.composite_markdown }}</pre>
                                @if (r.source_urls?.length) {
                                  <div class="text-[11px] text-text-3 mt-1">
                                    Sources:
                                    @for (u of r.source_urls; track u) {
                                      <a [href]="u" target="_blank" rel="noopener noreferrer"
                                         class="text-[var(--acc-info-fg)] underline mr-2">{{ u }}</a>
                                    }
                                  </div>
                                }
                                @if (r.dropped_facts?.length) {
                                  <div class="text-[11px] text-text-3 mt-1">
                                    Dropped: {{ r.dropped_facts.join('; ') }}
                                  </div>
                                }
                              </div>
                            </details>
                          }
                        </div>
                      </td>
                    </tr>
                  }
                }
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      /* P4 WS-DA-2: de-emphasize frozen persona rows via a subtle background
         tint instead of reduced opacity — so text never drops below 4.5:1.
         The "Frozen — philosophy is canon" label carries the state. */
      tr.evo-frozen { background: var(--surface-2); }
      .evo-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px 24px;
      }
      .save-btn { height: 32px; justify-content: center; }
      .save-btn--portfolio { align-self: flex-start; min-width: 200px; }
      .badge-free {
        display: inline-block;
        margin-left: 6px;
        padding: 1px 6px;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.04em;
        border-radius: 4px;
        background: var(--acc-long-soft, #e6f4ea);
        color: var(--acc-long-fg, #1b5e20);
        vertical-align: middle;
      }
      .btn-xs { height: 22px; padding: 0 8px; font-size: 11px; }
      .rev-block {
        padding: 8px 4px 8px 4px;
        background: var(--surface);
        border-top: 1px solid var(--border);
      }
      .rev-item { padding: 4px 0; border-bottom: 1px dashed var(--border); }
      .rev-item summary { cursor: pointer; font-size: 12px; }
      .rev-body { padding: 6px 0 6px 12px; }
      .rev-md {
        white-space: pre-wrap;
        font-family: var(--font-mono, monospace);
        font-size: 11.5px;
        background: var(--bg);
        border: 1px solid var(--border);
        border-radius: var(--r-6, 6px);
        padding: 8px;
        margin: 0;
      }
      @media (max-width: 900px) {
        .evo-grid { grid-template-columns: 1fr; }
      }
    `,
  ],
})
export class SettingsPersonasPage implements OnInit {
  readonly store = inject(ModelsStore);
  readonly evoStore = inject(PersonaEvolutionStore);

  evoForm: EvolutionSettings = {
    enabled: false,
    cadence: 'off',
    model_id: '',
    web_search_enabled: true,
    monthly_cost_cap_usd: '2.00',
    cost_cap_reached_at: null,
    updated_at: '',
  };
  private savedEvo: EvolutionSettings = { ...this.evoForm };
  savingEvo = signal(false);
  evoMsg = signal<string | null>(null);
  revisionsOpenFor = signal<string | null>(null);

  evoSettings = computed(() => this.evoStore.settings());
  evoMtdCostLabel = computed(
    () => `$${this.evoSettings()?.month_to_date_cost_usd ?? '0.00'}`,
  );

  evoModelChoices = computed(() =>
    this.store
      .models()
      .filter((m) => m.provider !== 'anthropic')
      .filter((m) => m.tier === 'fast_cheap' || m.tier === 'hosted_open'),
  );

  ngOnInit(): void {
    // Evolution model dropdown filters the catalog, so load it.
    this.store.loadModels().subscribe();
    this.evoStore.loadSettings().subscribe({
      next: (s) => {
        this.evoForm = { ...s };
        this.savedEvo = { ...s };
      },
      error: () => { /* ignore — defaults are fine */ },
    });
    this.evoStore.loadProfiles().subscribe({
      next: () => {
        if (this.evoStore.runningPersonas().length > 0) {
          this.evoStore.startPolling();
        }
      },
    });
  }

  isEvoDirty(): boolean {
    return JSON.stringify(this.evoForm) !== JSON.stringify(this.savedEvo);
  }

  saveEvoSettings(): void {
    this.savingEvo.set(true);
    this.evoMsg.set(null);
    const body = { ...this.evoForm };
    this.evoStore.patchSettings(body).subscribe({
      next: (s) => {
        this.evoForm = { ...s };
        this.savedEvo = { ...s };
        this.savingEvo.set(false);
        this.evoMsg.set('Saved.');
        setTimeout(() => this.evoMsg.set(null), 2500);
      },
      error: (err) => {
        this.savingEvo.set(false);
        this.evoMsg.set(err?.error?.detail || 'Failed to save.');
      },
    });
  }

  runEvoNow(): void {
    this.evoStore.runNow().subscribe();
  }

  toggleViewRevisions(persona: string): void {
    if (this.revisionsOpenFor() === persona) {
      this.revisionsOpenFor.set(null);
      this.evoStore.clearRevisions();
      return;
    }
    this.revisionsOpenFor.set(persona);
    this.evoStore.loadRevisions(persona).subscribe();
  }

  relTime(iso: string | null | undefined): string {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '';
    const diff = Date.now() - t;
    const min = Math.round(diff / 60_000);
    if (min < 1) return 'just now';
    if (min < 60) return `${min}m ago`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.round(hr / 24);
    return `${day}d ago`;
  }
}
