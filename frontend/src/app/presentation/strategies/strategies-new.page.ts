import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';
import {
  DEFAULT_SCREENER_WEIGHTS, SCREENER_WEIGHT_LABELS,
  STRATEGY_KIND_DESCRIPTIONS, STRATEGY_KIND_OPTIONS, StrategyKind,
} from '../../core/models/strategy.model';

interface Kind { value: StrategyKind; label: string; subtitle: string; sketch: 'lo' | 'so' | 'ls' | 'mn' }
interface Preset { id: string; name: string; desc: string; cost: number; runtime: string }

@Component({
  selector: 'hf-strategies-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Strategy setup</div>
          <h1 style="margin-top:6px">Create strategy</h1>
          <p style="color:var(--text-2);font-size:13px;margin-top:6px;max-width:560px">
            Pick the kind, the universe, the construction rules, the council, the screener, and the model. Preview updates live.
          </p>
        </div>
        <div class="head-actions">
          <a class="btn ghost" routerLink="/strategies">Cancel</a>
          <button class="btn">Save as draft</button>
          <button class="btn primary" (click)="submit()" [disabled]="submitting() || !canSubmit()">
            Activate strategy <span class="kbd">⌘↵</span>
          </button>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:28px">
        <div style="display:flex;flex-direction:column;gap:18px">

          <section class="card">
            <div class="card-hd"><span class="title mono">01 · Identity &amp; kind</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:14px">
              <div class="field"><label class="lbl">Name</label>
                <input class="input sans" name="name" [(ngModel)]="name" /></div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px">
                @for(k of kindCards; track k.value){
                  <button (click)="selectKind(k.value)"
                    style="text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:12px;display:flex;flex-direction:column;gap:8px;cursor:pointer;position:relative"
                    [style.borderColor]="kind===k.value ? 'var(--acc-info)' : 'var(--border)'"
                    [style.background]="kind===k.value ? 'var(--surface-2)' : 'var(--surface)'">
                    @if(kind===k.value){<span style="position:absolute;top:8px;right:8px;color:var(--acc-info)"><svg width="12" height="12"><use href="/icons.svg#i-check"/></svg></span>}
                    <span class="kind" [class.lo]="k.sketch==='lo'" [class.so]="k.sketch==='so'" [class.ls]="k.sketch==='ls'" [class.mn]="k.sketch==='mn'">{{ k.sketch.toUpperCase() }}</span>
                    <span style="font-size:13px;color:var(--text)">{{ k.label }}</span>
                    <span style="font-size:11px;color:var(--text-3)">{{ k.subtitle }}</span>
                    <div style="display:flex;height:6px;background:var(--surface-2);border-radius:2px;overflow:hidden;margin-top:4px">
                      @if(k.sketch==='lo'){<div style="width:100%;background:var(--acc-long)"></div>}
                      @if(k.sketch==='so'){<div style="width:100%;background:var(--acc-short)"></div>}
                      @if(k.sketch==='ls'){<div style="width:35%;background:var(--acc-short)"></div><div style="width:65%;background:var(--acc-long)"></div>}
                      @if(k.sketch==='mn'){<div style="width:50%;background:var(--acc-short)"></div><div style="width:50%;background:var(--acc-long)"></div>}
                    </div>
                  </button>
                }
              </div>
              <p style="font-size:12px;color:var(--text-3);margin:0">{{ kindDesc() }}</p>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">02 · Universe</span></div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">
              @for(u of store.universes(); track u.id){
                <button (click)="universe = u.id"
                  style="text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:12px;cursor:pointer;display:flex;flex-direction:column;gap:6px;position:relative"
                  [style.borderColor]="universe===u.id ? 'var(--acc-info)' : 'var(--border)'"
                  [style.background]="universe===u.id ? 'var(--surface-2)' : 'var(--surface)'">
                  @if(universe===u.id){<span style="position:absolute;top:8px;right:8px;color:var(--acc-info)"><svg width="12" height="12"><use href="/icons.svg#i-check"/></svg></span>}
                  <span style="font-size:13px;color:var(--text)">{{ u.name }}</span>
                  <span class="mono" style="font-size:11px;color:var(--text-3)">{{ u.member_count }} names</span>
                </button>
              }
              <button style="text-align:left;background:transparent;border:1px dashed var(--border-2);border-radius:6px;padding:12px;color:var(--text-3);font-size:12.5px;cursor:pointer">+ Create universe</button>
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <span class="title mono">03 · Construction</span>
              <span class="pill"><span class="dot"></span>portfolio
                <select [(ngModel)]="portfolio" style="background:transparent;border:0;color:var(--text);font-size:12px;outline:0">
                  @for(p of store.portfolios(); track p.id){<option [value]="p.id">{{ p.name }}</option>}
                </select>
              </span>
            </div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:14px">
              <div class="field"><label class="lbl">Target gross</label><input class="input" type="number" step="0.05" [(ngModel)]="targetGross" /><span class="suffix">×</span></div>
              <div class="field"><label class="lbl">Target net</label><input class="input" type="number" step="0.05" [(ngModel)]="targetNet" [disabled]="kind==='market_neutral'" /><span class="suffix">×</span></div>
              <div class="field"><label class="lbl">Top-k longs</label><input class="input" type="number" [(ngModel)]="topLongs" /></div>
              <div class="field"><label class="lbl">Top-k shorts</label><input class="input" type="number" [(ngModel)]="topShorts" /></div>
              <div class="field"><label class="lbl">Max position</label><input class="input" type="number" step="0.005" [(ngModel)]="maxPosition" /><span class="suffix">×</span></div>
              <div class="field"><label class="lbl">Max sector</label><input class="input" type="number" step="0.01" [(ngModel)]="maxSector" /><span class="suffix">×</span></div>
              <div class="field"><label class="lbl">Cost ceiling</label><input class="input" type="number" step="0.5" [(ngModel)]="costCeiling" /><span class="suffix">USD</span></div>
              <div class="field"><label class="lbl">Rebalance</label>
                <div class="seg"><button class="opt" type="button" [class.on]="rebal()==='daily'" (click)="rebal.set('daily')">Daily</button>
                  <button class="opt" type="button" [class.on]="rebal()==='weekly'" (click)="rebal.set('weekly')">Weekly</button>
                  <button class="opt" type="button" [class.on]="rebal()==='monthly'" (click)="rebal.set('monthly')">Monthly</button></div>
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <span class="title mono">04 · Screener weights</span>
              <div class="actions"><button class="btn ghost sm">Load preset…</button></div>
            </div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:14px 28px">
              @for(k of weightKeys; track k){
                <div>
                  <div style="display:flex;justify-content:space-between;font-size:12px;align-items:center">
                    <span style="color:var(--text-2)">{{ label(k) }}</span>
                    <input type="number" step="0.05" [(ngModel)]="weights[k]" min="-1" max="1"
                      class="input mono" style="width:64px;text-align:right;height:24px;padding:0 6px;font-size:11.5px" />
                  </div>
                  <div style="position:relative;height:8px;background:var(--surface-2);border-radius:2px;margin-top:6px">
                    <div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--text-3);opacity:.4"></div>
                    <div [style.left.%]="50 + (weights[k] * 40)" style="position:absolute;top:-2px;width:8px;height:12px;border-radius:2px"
                      [style.background]="weights[k]>0 ? 'var(--acc-long)' : weights[k]<0 ? 'var(--acc-short)' : 'var(--text-3)'"></div>
                  </div>
                </div>
              }
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">05 · Model &amp; cost ceiling</span></div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
              @for(p of presets; track p.id){
                <button (click)="modelPreset = p.id"
                  style="text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;flex-direction:column;gap:6px;cursor:pointer;position:relative"
                  [style.borderColor]="modelPreset===p.id ? 'var(--acc-info)' : 'var(--border)'"
                  [style.background]="modelPreset===p.id ? 'var(--surface-2)' : 'var(--surface)'">
                  @if(modelPreset===p.id){<span style="position:absolute;top:10px;right:10px;color:var(--acc-info)"><svg width="14" height="14"><use href="/icons.svg#i-check"/></svg></span>}
                  <span class="eyebrow">{{ p.name }}</span>
                  <span style="font-size:12.5px;color:var(--text-2)">{{ p.desc }}</span>
                  <span class="mono" style="font-size:12px;color:var(--text)">$ {{ p.cost.toFixed(2) }} / cycle · {{ p.runtime }}</span>
                </button>
              }
            </div>
          </section>
        </div>

        <!-- Sticky live preview -->
        <aside style="position:sticky;top:64px;align-self:flex-start">
          <section class="card">
            <div class="card-hd">
              <span class="title">Preview</span>
              <span class="pill info"><span class="dot"></span>what next cycle would look like</span>
            </div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:10px">
              <div style="display:flex;align-items:center;gap:8px">
                <span class="kind" [class.lo]="kind==='long_only'" [class.so]="kind==='short_only'" [class.ls]="kind==='long_short'" [class.mn]="kind==='market_neutral'">{{ kindLabel() }}</span>
                <span style="font-size:13px;color:var(--text)">{{ name || 'Untitled' }}</span>
              </div>
              <div style="display:flex;flex-direction:column;gap:4px;font-size:11.5px;border-top:1px solid var(--border);padding-top:8px">
                <div style="display:flex;justify-content:space-between"><span style="color:var(--text-3)">universe</span><span class="mono">{{ universeName() }}</span></div>
                <div style="display:flex;justify-content:space-between"><span style="color:var(--text-3)">top-k</span><span class="mono">{{ topLongs }}L · {{ topShorts }}S</span></div>
                <div style="display:flex;justify-content:space-between"><span style="color:var(--text-3)">gross</span><span class="mono">{{ (targetGross * 100).toFixed(1) }}%</span></div>
                <div style="display:flex;justify-content:space-between"><span style="color:var(--text-3)">net</span><span class="mono">{{ (targetNet * 100).toFixed(1) }}%</span></div>
                <div style="display:flex;justify-content:space-between"><span style="color:var(--text-3)">rebalance</span><span class="mono">{{ rebal() }}</span></div>
              </div>

              <div style="border-top:1px solid var(--border);padding-top:8px">
                <div class="eyebrow" style="margin-bottom:6px">Mock orders</div>
                <table class="tbl" style="font-size:11.5px">
                  <tbody>
                    <tr><td><span class="stance bull">BUY</span></td><td class="mono">NVDA</td><td class="num mono">+4,200</td></tr>
                    <tr><td><span class="stance bear">SHORT</span></td><td class="mono">TSLA</td><td class="num mono">−1,800</td></tr>
                    <tr><td><span class="stance bull">COVER</span></td><td class="mono">UNH</td><td class="num mono">+800</td></tr>
                  </tbody>
                </table>
              </div>

              <div style="border-top:1px solid var(--border);padding-top:8px">
                <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--text-3)">
                  <span class="mono">$ 412.10 / $ 800.00</span><span class="mono">52%</span>
                </div>
                <div class="cost-gauge" style="margin-top:4px"><div class="fill" style="width:52%"></div></div>
              </div>

              @if(error()){<p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>}
              <button class="btn primary" style="height:36px;justify-content:center" (click)="submit()" [disabled]="submitting() || !canSubmit()">
                {{ submitting() ? 'Activating…' : 'Activate' }} <span class="kbd">⌘↵</span>
              </button>
            </div>
          </section>
        </aside>
      </div>
    </hf-app-shell>
  `,
})
export class StrategiesNewPage implements OnInit {
  readonly store = inject(StrategiesStore);
  private readonly router = inject(Router);

  name = 'Daily L/S 100/50';
  kind: StrategyKind = 'long_short';
  readonly kindOptions = STRATEGY_KIND_OPTIONS;
  readonly kindCards: Kind[] = [
    { value: 'long_only', label: 'Long-only', subtitle: 'Net = Gross', sketch: 'lo' },
    { value: 'long_short', label: 'Long/Short', subtitle: 'Directional', sketch: 'ls' },
    { value: 'market_neutral', label: 'Market-neutral', subtitle: 'Net ≈ 0', sketch: 'mn' },
    { value: 'short_only', label: 'Short-only', subtitle: 'Net = −Gross', sketch: 'so' },
  ];
  readonly presets: Preset[] = [
    { id: 'frugal', name: 'Frugal', desc: 'All agents fast-cheap tier.', cost: 0.32, runtime: '~12s' },
    { id: 'research', name: 'Research', desc: 'Council + CIO frontier.', cost: 4.21, runtime: '~38s' },
    { id: 'quality', name: 'Quality', desc: 'Every agent frontier.', cost: 12.40, runtime: '~84s' },
    { id: 'hybrid', name: 'Hybrid · BYO', desc: 'Local + frontier mix.', cost: 1.02, runtime: '~22s' },
  ];

  universe: number | null = null;
  portfolio: number | null = null;
  targetGross = 1.5;
  targetNet = 0.5;
  maxPosition = 0.03;
  maxSector = 0.25;
  topLongs = 10;
  topShorts = 5;
  costCeiling = 2.0;
  modelPreset = 'frugal';
  rebal = signal<'daily' | 'weekly' | 'monthly'>('weekly');
  weights: Record<string, number> = { ...DEFAULT_SCREENER_WEIGHTS };
  weightKeys = Object.keys(DEFAULT_SCREENER_WEIGHTS);
  submitting = signal(false);
  error = signal<string | null>(null);

  kindDesc = computed(() => STRATEGY_KIND_DESCRIPTIONS[this.kind]);
  kindLabel = () => this.kindCards.find((k) => k.value === this.kind)?.sketch.toUpperCase() ?? 'LS';
  universeName = () => this.store.universes().find((u) => u.id === this.universe)?.name ?? '—';
  canSubmit = () => this.universe !== null && this.portfolio !== null && this.name.length > 0;

  selectKind(k: StrategyKind) {
    this.kind = k;
    if (k === 'long_only') { this.topShorts = 0; if (this.targetNet < 0) this.targetNet = Math.abs(this.targetNet); }
    else if (k === 'short_only') { this.topLongs = 0; if (this.targetNet > 0) this.targetNet = -Math.abs(this.targetNet); }
    else if (k === 'market_neutral') { this.targetNet = 0; }
  }

  label(k: string): string { return SCREENER_WEIGHT_LABELS[k] ?? k; }

  ngOnInit(): void {
    this.store.loadUniverses().subscribe((us) => { if (us.length && this.universe === null) this.universe = us[0].id; });
    this.store.loadPortfolios().subscribe((ps) => { if (ps.length && this.portfolio === null) this.portfolio = ps[0].id; });
  }

  submit(): void {
    if (!this.canSubmit()) { this.error.set('Pick a universe and a portfolio first.'); return; }
    this.error.set(null); this.submitting.set(true);
    this.store.create({
      name: this.name, kind: this.kind, universe: this.universe!, portfolio: this.portfolio!,
      target_gross_pct: String(this.targetGross), target_net_pct: String(this.targetNet),
      max_position_pct: String(this.maxPosition), max_sector_pct: String(this.maxSector),
      top_k_longs: this.topLongs, top_k_shorts: this.topShorts,
      cost_ceiling_per_cycle_usd: String(this.costCeiling),
      model_preset: this.modelPreset, screener_weights: this.weights,
    }).subscribe({
      next: (s) => this.router.navigate(['/strategies', s.id]),
      error: (e) => { this.submitting.set(false); this.error.set(e?.error?.detail || 'Failed'); },
    });
  }
}
