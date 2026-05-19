import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { ModelsStore } from '../../abstraction/models.store';
import { RunsStore } from '../../abstraction/runs.store';
import { ALL_PERSONAS, DEFAULT_PERSONA_IDS } from '../../core/models/run.model';

interface Preset { id: string; name: string; desc: string; cost: number; runtime: string }

const PERSONA_COLORS: Record<string, string> = {
  buffett: 'c1', munger: 'c2', graham: 'c3', wood: 'c4',
  druckenmiller: 'c5', burry: 'c6', damodaran: 'c7', lynch: 'c8',
};
const PERSONA_MONO: Record<string, string> = {
  buffett: 'WB', munger: 'CM', graham: 'BG', wood: 'CW',
  druckenmiller: 'SD', burry: 'MB', damodaran: 'AD', lynch: 'PL',
};

@Component({
  selector: 'hf-runs-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Runs', link:'/'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Run setup</div>
          <h1 style="margin-top:6px">Run the portfolio</h1>
          <p style="color:var(--text-2);font-size:13px;margin-top:6px;max-width:560px">
            Pick the tickers, the as-of date, the model preset, and the council. We'll quote live cost as you go.
          </p>
        </div>
        <div class="head-actions">
          <a class="btn ghost" routerLink="/">Cancel</a>
          <button class="btn">Save as preset</button>
          <button class="btn primary" (click)="submit()" [disabled]="!canRun() || submitting()">
            Run <span class="kbd">⌘↵</span>
          </button>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:28px">
        <div style="display:flex;flex-direction:column;gap:18px">

          <section class="card">
            <div class="card-hd">
              <span class="title mono">01 · Tickers</span>
            </div>
            <div class="card-bd">
              <div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px">
                @for(t of tickers(); track t){
                  <span class="pill" style="height:24px"><span class="mono" style="color:var(--text);font-weight:600">{{ t }}</span>
                    <button class="icon-btn" style="width:16px;height:16px" (click)="removeTicker(t)" aria-label="Remove">
                      <svg width="10" height="10"><use href="/icons.svg#i-x" /></svg>
                    </button>
                  </span>
                }
                <input class="input" style="flex:1;min-width:140px;border:0;background:transparent;height:24px;padding:0;text-transform:uppercase"
                  placeholder="Type a ticker, press ↵ or ," [(ngModel)]="tickerDraft"
                  (keydown.enter)="addTicker($event)" (keydown.comma)="addTicker($event)" />
              </div>
              <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:8px">Or paste a CSV list</div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">02 · As-of date</span></div>
            <div class="card-bd" style="display:flex;gap:18px;align-items:flex-end">
              <div class="field" style="flex:1;max-width:200px">
                <label class="lbl">Date</label>
                <input class="input" type="date" [(ngModel)]="asOfDate" />
              </div>
              <div class="field" style="flex:1;max-width:300px">
                <label class="lbl">Mode</label>
                <div class="seg">
                  @for(m of modes; track m){
                    <button class="opt" [class.on]="mode()===m" (click)="mode.set(m)">{{ m }}</button>
                  }
                </div>
              </div>
              <div style="flex:1;font-size:11.5px;color:var(--text-3);padding-bottom:8px">
                Data freshness: prices 12:48 ET · fundamentals as of Q1 2026
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">03 · Model preset</span></div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
              @for(p of presets; track p.id){
                <button (click)="preset.set(p.id)"
                  style="text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;flex-direction:column;gap:6px;position:relative;cursor:pointer"
                  [style.background]="preset()===p.id ? 'var(--surface-2)' : 'var(--surface)'"
                  [style.borderColor]="preset()===p.id ? 'var(--acc-info)' : 'var(--border)'">
                  @if(preset()===p.id){
                    <span style="position:absolute;top:10px;right:10px;color:var(--acc-info)"><svg width="14" height="14"><use href="/icons.svg#i-check"/></svg></span>
                  }
                  <span class="eyebrow">{{ p.name }}</span>
                  <span style="font-size:12.5px;color:var(--text-2);line-height:18px">{{ p.desc }}</span>
                  <span class="mono" style="font-size:12px;color:var(--text);margin-top:4px">$ {{ p.cost.toFixed(2) }} / run · {{ p.runtime }}</span>
                </button>
              }
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <span class="title mono">04 · Council</span>
              <span class="pill"><span class="dot"></span>{{ selected().size }} of {{ allPersonas.length }}</span>
            </div>
            <div class="card-bd" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px">
              @for(p of allPersonas; track p.id){
                <button (click)="toggle(p.id)"
                  style="text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px;display:flex;align-items:center;gap:8px;cursor:pointer"
                  [style.opacity]="selected().has(p.id) ? 1 : 0.45"
                  [style.borderColor]="selected().has(p.id) ? 'var(--border-2)' : 'var(--border)'">
                  <span class="mono-tile" [class]="'mt-' + colOf(p.id)">{{ monoOf(p.id) }}</span>
                  <span style="display:flex;flex-direction:column;line-height:1.3">
                    <span style="font-size:12.5px;color:var(--text)">{{ p.name }}</span>
                    <span style="font-size:11px;color:var(--text-3)">{{ p.optional ? 'optional' : 'core' }}</span>
                  </span>
                  <span style="margin-left:auto">
                    @if(selected().has(p.id)){<svg width="14" height="14" style="color:var(--acc-long)"><use href="/icons.svg#i-check"/></svg>}
                  </span>
                </button>
              }
            </div>
          </section>

          <section class="card">
            <button class="card-hd" style="width:100%;justify-content:flex-start;background:transparent;border:0;cursor:pointer;display:flex" (click)="adv.set(!adv())">
              <span class="title mono">05 · Advanced</span>
              <span class="pill" style="margin-left:auto">{{ adv() ? 'hide' : 'show' }}
                <svg width="10" height="10"><use href="/icons.svg#i-chevron-dn" /></svg>
              </span>
            </button>
            @if(adv()){
              <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
                <div class="field"><label class="lbl">Cost ceiling override</label>
                  <input class="input" placeholder="$ 8.00" /><span class="suffix">USD</span></div>
                <div class="field"><label class="lbl">Tool budget</label>
                  <div class="seg"><button class="opt on">Standard</button><button class="opt">Extended</button></div></div>
                <div class="field"><label class="lbl">Risk policy</label>
                  <select class="input sans"><option>Default · concentrated L/S</option><option>Conservative</option></select></div>
                <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-2);margin-top:24px">
                  <input type="checkbox" checked style="accent-color:var(--acc-info)" /> Save artifacts
                </label>
              </div>
            }
          </section>
        </div>

        <!-- Sticky cost estimate -->
        <aside style="position:sticky;top:64px;align-self:flex-start">
          <section class="card">
            <div class="card-hd">
              <span class="title">Live estimate</span>
              <span class="pill info live"><span class="dot"></span>updates as you edit</span>
            </div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:10px">
              <div>
                <div class="kpi" style="padding:0;border:0;gap:4px">
                  <div class="v" style="font-size:32px">$ {{ currentCost().toFixed(2) }}</div>
                  <div class="mono" style="font-size:12px;color:var(--text-3)">
                    ~{{ runtime() }}s · {{ agentCount() }} agents · 92k tok in / 12k out
                  </div>
                </div>
              </div>

              <div style="display:flex;flex-direction:column;gap:4px;max-height:240px;overflow:auto;border-top:1px solid var(--border);padding-top:8px">
                @for(p of allPersonas; track p.id){
                  @if(selected().has(p.id)){
                    <div style="display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;font-size:11.5px;padding:3px 0">
                      <span class="mono-tile sm" [class]="'mt-' + colOf(p.id)">{{ monoOf(p.id) }}</span>
                      <span style="color:var(--text-2)">{{ p.name }}</span>
                      <span class="mono" style="color:var(--text-3)">$ {{ perAgent().toFixed(3) }}</span>
                    </div>
                  }
                }
                @for(a of analystAgents; track a){
                  <div style="display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;font-size:11.5px;padding:3px 0">
                    <span class="mono-tile sm">{{ a.slice(0,2).toUpperCase() }}</span>
                    <span style="color:var(--text-2)">{{ a }}</span>
                    <span class="mono" style="color:var(--text-3)">$ 0.120</span>
                  </div>
                }
              </div>

              <div style="border-top:1px solid var(--border);padding-top:10px">
                <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--text-3)">
                  <span class="mono">Monthly $ 412.10 / $ 800.00</span>
                  <span class="mono">51.5% → 52.0%</span>
                </div>
                <div class="cost-gauge" style="margin-top:6px">
                  <div class="fill" style="width:52%"></div>
                  <div class="mark" style="left:51.5%"></div>
                </div>
              </div>

              @if(error()){<p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>}
              <button class="btn primary" style="width:100%;height:36px;justify-content:center" (click)="submit()" [disabled]="!canRun() || submitting()">
                {{ submitting() ? 'Submitting…' : 'Run' }} <span class="kbd">⌘↵</span>
              </button>
            </div>
          </section>
        </aside>
      </div>
    </hf-app-shell>
  `,
})
export class RunsNewPage implements OnInit {
  readonly runs = inject(RunsStore);
  readonly modelsStore = inject(ModelsStore);
  private readonly router = inject(Router);

  readonly allPersonas = ALL_PERSONAS;
  readonly analystAgents = ['fundamentals', 'technicals', 'valuation', 'sentiment', 'macro', 'news', 'risk', 'portfolio', 'cio'];
  readonly modes = ['Live', 'EOD', 'Custom'];
  readonly presets: Preset[] = [
    { id: 'frugal', name: 'Frugal', desc: 'All agents on fast-cheap tier.', cost: 0.32, runtime: '~12s' },
    { id: 'research', name: 'Research', desc: 'Council + CIO on frontier. Analysts fast-cheap.', cost: 4.21, runtime: '~38s' },
    { id: 'quality', name: 'Quality', desc: 'Every agent on frontier.', cost: 12.40, runtime: '~84s' },
    { id: 'hybrid', name: 'Hybrid · BYO', desc: 'Local Ollama + frontier council.', cost: 1.02, runtime: '~22s' },
  ];

  tickers = signal<string[]>(['AAPL']);
  tickerDraft = '';
  asOfDate = new Date().toISOString().slice(0, 10);
  mode = signal('Live');
  preset = signal('research');
  selected = signal<Set<string>>(new Set(DEFAULT_PERSONA_IDS));
  adv = signal(false);
  submitting = signal(false);
  error = signal<string | null>(null);

  currentCost = computed(() => {
    const p = this.presets.find((x) => x.id === this.preset())!;
    const ratio = this.selected().size / Math.max(1, DEFAULT_PERSONA_IDS.length);
    return p.cost * (0.5 + 0.5 * ratio);
  });
  runtime = computed(() => Math.round(this.currentCost() * 9));
  agentCount = computed(() => this.selected().size + this.analystAgents.length);
  perAgent = computed(() => this.currentCost() / Math.max(1, this.selected().size + 4));
  canRun = computed(() => this.tickers().length >= 1 && this.selected().size >= 3);

  colOf(id: string) { return PERSONA_COLORS[id] ?? 'c1'; }
  monoOf(id: string) { return PERSONA_MONO[id] ?? id.slice(0, 2).toUpperCase(); }

  toggle(id: string) {
    const s = new Set(this.selected());
    s.has(id) ? s.delete(id) : s.add(id);
    this.selected.set(s);
  }

  addTicker(e: Event) {
    e.preventDefault();
    const v = this.tickerDraft.trim().replace(/,/g, '').toUpperCase();
    if (!v) return;
    if (!this.tickers().includes(v)) this.tickers.set([...this.tickers(), v]);
    this.tickerDraft = '';
  }
  removeTicker(t: string) { this.tickers.set(this.tickers().filter((x) => x !== t)); }

  ngOnInit(): void { this.modelsStore.loadAll().subscribe({ error: () => {} }); }

  submit(): void {
    if (!this.canRun()) return;
    this.error.set(null);
    this.submitting.set(true);
    this.runs.submitRun({
      tickers: this.tickers(),
      as_of_date: this.asOfDate,
      model_overrides: {},
      personas: Array.from(this.selected()),
    }).subscribe({
      next: (run) => this.router.navigate(['/runs', run.id]),
      error: (e) => {
        this.submitting.set(false);
        this.error.set(e?.error?.detail ?? 'Failed to submit run');
      },
    });
  }
}
