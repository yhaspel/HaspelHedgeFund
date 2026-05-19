import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { RunsStore } from '../../abstraction/runs.store';
import { AgentMessage, ALL_PERSONAS, PERSONA_IDS } from '../../core/models/run.model';

interface PersonaCard {
  id: string; displayName: string; version: string;
  signal: 'bullish' | 'neutral' | 'bearish' | 'unknown';
  confidence: number; thesis: string; keyRisks: string[];
  intrinsicValue: number | null; marginOfSafety: number | null;
}

const COL: Record<string, string> = {
  buffett: 'c1', munger: 'c2', graham: 'c3', wood: 'c4',
  druckenmiller: 'c5', burry: 'c6', damodaran: 'c7', lynch: 'c8',
};
const MONO: Record<string, string> = {
  buffett: 'WB', munger: 'CM', graham: 'BG', wood: 'CW',
  druckenmiller: 'SD', burry: 'MB', damodaran: 'AD', lynch: 'PL',
};

@Component({
  selector: 'hf-runs-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="crumbs()">
      @if (!run()) {
        <p style="color:var(--text-3)">Loading…</p>
      } @else {
        <div class="page-head">
          <div>
            <div class="eyebrow">Single-name run · Equity / US · {{ run()!.status }}</div>
            <h1 style="margin-top:6px">{{ run()!.tickers.join(' · ') }} <span style="color:var(--text-3);font-weight:500">· run #{{ run()!.id }}</span></h1>
            <div class="meta-strip">
              <div class="meta"><div class="k">As-of</div><div class="v">{{ run()!.as_of_date }}</div></div>
              <div class="meta"><div class="k">Council</div><div class="v">{{ personas().length }} personas + CIO</div></div>
              <div class="meta"><div class="k">Cost</div><div class="v">$ {{ formatCost(run()!.total_cost_usd) }}</div></div>
              <div class="meta"><div class="k">Status</div><div class="v">{{ run()!.status }}</div></div>
            </div>
          </div>
          <div class="head-actions">
            @if(canCancel()){
              <button class="btn danger" (click)="cancel()" [disabled]="cancelling()">{{ cancelling() ? 'Cancelling…' : 'Stop' }}</button>
            }
            <button class="btn">Export</button>
            <a class="btn" routerLink="/runs/new">Rerun</a>
            <button class="btn primary">Copy decision <span class="kbd">⌘C</span></button>
          </div>
        </div>

        <div class="tabs" style="margin-bottom:18px">
          @for(t of tabs; track t.id){
            <button class="tab" [class.active]="tab()===t.id" (click)="tab.set($any(t.id))">
              {{ t.label }}
              @if(t.count){<span class="count" [class.attn]="t.attn">{{ t.count }}</span>}
            </button>
          }
        </div>

        @if(tab()==='decision'){
          <div style="display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:28px">
            <div style="display:flex;flex-direction:column;gap:18px">
              <!-- Decision hero -->
              <section class="card">
                <div class="card-bd" style="display:grid;grid-template-columns:1.1fr 1px 1fr;gap:24px">
                  <div>
                    <div class="eyebrow">Recommended action</div>
                    <div style="font-size:44px;line-height:48px;font-weight:600;letter-spacing:-0.018em;margin-top:6px"
                      [style.color]="decisionColor()">{{ decision() }}</div>
                    <div style="color:var(--text-2);font-size:13px;margin-top:6px">{{ decisionSub() }}</div>
                    <div class="mono" style="font-size:12px;color:var(--text-3);margin-top:10px">
                      Limit $ 192.50 · Stop $ 162.60 · Horizon ~6 weeks
                    </div>
                  </div>
                  <div style="width:1px;background:var(--border)"></div>
                  <div>
                    <div class="eyebrow">Council confidence</div>
                    <div class="mono" style="font-size:28px;line-height:34px;margin-top:6px">{{ avgConf() }} <span style="color:var(--text-3)">/ 100</span></div>
                    <div class="conf-bar" style="margin-top:10px">
                      <div class="conf-fill" [style.width]="avgConf() + '%'"></div>
                      <div class="conf-ticks">
                        @for(_ of [].constructor(9); track $index){<div class="conf-tick"></div>}
                      </div>
                    </div>
                    <div class="mono" style="font-size:12px;color:var(--text-2);margin-top:8px">
                      {{ stanceCount().bull }} bull · {{ stanceCount().bear }} bear · {{ stanceCount().neut }} neutral
                    </div>
                  </div>
                </div>
              </section>

              <!-- Stat grid -->
              <section class="card">
                <div class="card-hd"><span class="title">Targets &amp; risk</span></div>
                <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
                  <div>
                    <div class="eyebrow">Target zone</div>
                    <div class="mono" style="margin-top:4px;font-size:18px">$ 192.50</div>
                    <div style="position:relative;height:12px;background:var(--surface-2);border-radius:4px;margin-top:8px">
                      <div style="position:absolute;left:30%;width:50%;height:100%;background:var(--acc-long-soft);border-radius:4px"></div>
                      <div style="position:absolute;left:55%;top:-2px;bottom:-2px;width:2px;background:var(--acc-long)"></div>
                    </div>
                    <div class="mono" style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-3);margin-top:4px"><span>$ 178</span><span>$ 214</span></div>
                  </div>
                  <div>
                    <div class="eyebrow">Stop</div>
                    <div class="mono" style="margin-top:4px;font-size:18px;color:var(--acc-short-fg)">$ 162.60</div>
                    <div style="position:relative;height:12px;background:var(--surface-2);border-radius:4px;margin-top:8px">
                      <div style="position:absolute;left:20%;width:30%;height:100%;background:var(--acc-short-soft);border-radius:4px"></div>
                      <div style="position:absolute;left:38%;top:-2px;bottom:-2px;width:2px;background:var(--acc-short)"></div>
                    </div>
                    <div class="mono" style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-3);margin-top:4px"><span>$ 155</span><span>$ 175</span></div>
                  </div>
                  <div>
                    <div class="eyebrow">Expected return</div>
                    <div class="mono" style="margin-top:4px;font-size:18px;color:var(--acc-long-fg)">+12.4%</div>
                    <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:4px">over ~6 weeks · base / bull / bear</div>
                  </div>
                  <div>
                    <div class="eyebrow">Drawdown budget</div>
                    <div class="mono" style="margin-top:4px;font-size:18px;color:var(--acc-short-fg)">−5.8%</div>
                    <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:4px">on −12% → 0 band</div>
                  </div>
                </div>
              </section>

              <!-- Council snapshot -->
              <section class="card">
                <div class="card-hd"><span class="title">Council snapshot</span>
                  <span class="pill"><span class="dot"></span>{{ personas().length }} personas</span>
                </div>
                <div class="card-bd" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
                  @for(p of personas(); track p.id){
                    <article class="p-card" style="padding:10px">
                      <header>
                        <span class="mono-tile" [class]="'mt-' + colOf(p.id)">{{ monoOf(p.id) }}</span>
                        <div style="display:flex;flex-direction:column;line-height:1.2">
                          <span style="font-size:12px;color:var(--text)">{{ p.displayName }}</span>
                          <span style="font-size:10.5px;color:var(--text-3)">{{ p.version }}</span>
                        </div>
                        <span class="stance"
                          [class.bull]="p.signal==='bullish'"
                          [class.bear]="p.signal==='bearish'"
                          [class.neut]="p.signal==='neutral'"
                          style="margin-left:auto">{{ stanceShort(p.signal) }} · {{ Math.round(p.confidence) }}</span>
                      </header>
                      <p style="font-size:11.5px;line-height:16px;color:var(--text-2);margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{{ p.thesis }}</p>
                    </article>
                  }
                </div>
              </section>

              <!-- Drivers -->
              <section class="card">
                <div class="card-hd"><span class="title">Drivers</span></div>
                <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px">
                  <div>
                    <div class="eyebrow" style="color:var(--acc-long-fg)">Bull</div>
                    <ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text-2)">
                      <li>· services margin expansion <span class="mono" style="color:var(--text-3);float:right">[WB · 84]</span></li>
                      <li>· on-device AI cycle <span class="mono" style="color:var(--text-3);float:right">[CW · 69]</span></li>
                      <li>· buyback cadence <span class="mono" style="color:var(--text-3);float:right">[SD · 66]</span></li>
                      <li>· DCF triangulation $ 222 <span class="mono" style="color:var(--text-3);float:right">[AD · 73]</span></li>
                    </ul>
                  </div>
                  <div>
                    <div class="eyebrow" style="color:var(--acc-short-fg)">Bear</div>
                    <ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text-2)">
                      <li>· crowded long <span class="mono" style="color:var(--text-3);float:right">[MB · 58]</span></li>
                      <li>· sell-side raising into print <span class="mono" style="color:var(--text-3);float:right">[MB · 58]</span></li>
                      <li>· factor exhaustion <span class="mono" style="color:var(--text-3);float:right">[MB · 58]</span></li>
                    </ul>
                  </div>
                  <div>
                    <div class="eyebrow" style="color:var(--acc-hold-fg)">Neutral</div>
                    <ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text-2)">
                      <li>· net-net MoS absent <span class="mono" style="color:var(--text-3);float:right">[BG · 41]</span></li>
                      <li>· PEG ~1.6 <span class="mono" style="color:var(--text-3);float:right">[PL · 48]</span></li>
                      <li>· macro tailwind partially priced <span class="mono" style="color:var(--text-3);float:right">[PL · 48]</span></li>
                    </ul>
                  </div>
                </div>
              </section>
            </div>

            <!-- Order ticket sticky -->
            <aside style="position:sticky;top:64px;align-self:flex-start">
              <section class="card">
                <div class="card-hd"><span class="title">Order ticket</span></div>
                <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
                  <div class="seg">
                    <button class="opt on buy">BUY</button>
                    <button class="opt">SELL</button>
                    <button class="opt">HOLD</button>
                  </div>
                  <div class="field"><label class="lbl">Quantity</label>
                    <input class="input" value="15,600" /><span class="suffix">sh</span></div>
                  <div class="field"><label class="lbl">Limit</label>
                    <input class="input" value="192.50" /><span class="suffix">USD</span></div>
                  <div class="field"><label class="lbl">Stop</label>
                    <input class="input" value="162.60" /><span class="suffix">USD</span></div>
                  <div class="field"><label class="lbl">Horizon</label>
                    <select class="input sans"><option>6 weeks</option><option>3 months</option></select></div>
                  <div style="border-top:1px solid var(--border);padding-top:10px;display:flex;flex-direction:column;gap:8px">
                    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-2)">
                      <span>Max position</span><span class="mono">3.50%</span>
                    </div>
                    <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-2)">
                      <input type="checkbox" checked style="accent-color:var(--acc-info)" /> Stop guard ON
                    </label>
                  </div>
                  <div class="mono" style="font-size:11px;color:var(--text-3)">Routes to paper book only</div>
                  <button class="btn primary" style="height:36px;justify-content:center">Confirm ticket <span class="kbd">⌘↵</span></button>
                </div>
              </section>
            </aside>
          </div>
        }

        @if(tab()==='council'){
          <div style="display:flex;flex-direction:column;gap:14px">
            @for(p of personas(); track p.id){
              <section class="card">
                <button class="card-hd" style="width:100%;background:transparent;border:0;cursor:pointer;display:flex" (click)="toggleExpand(p.id)">
                  <span class="mono-tile lg" [class]="'mt-' + colOf(p.id)">{{ monoOf(p.id) }}</span>
                  <div style="display:flex;flex-direction:column;line-height:1.3">
                    <span style="font-size:14px;color:var(--text)">{{ p.displayName }}</span>
                    <span style="font-size:11px;color:var(--text-3)" class="mono">{{ p.version }}</span>
                  </div>
                  <span class="stance"
                    [class.bull]="p.signal==='bullish'" [class.bear]="p.signal==='bearish'" [class.neut]="p.signal==='neutral'"
                    style="margin-left:14px">{{ stanceShort(p.signal) }} · {{ Math.round(p.confidence) }}</span>
                  <span style="margin-left:auto;color:var(--text-3)">
                    <svg width="14" height="14"><use href="/icons.svg#i-chevron-dn" /></svg>
                  </span>
                </button>
                @if(expanded().has(p.id)){
                  <div class="card-bd" style="display:grid;grid-template-columns:2fr 1fr;gap:18px">
                    <div style="font-size:14px;line-height:22px;color:var(--text-2)">{{ p.thesis }}</div>
                    <div>
                      <div class="eyebrow">Key risks</div>
                      <ul style="list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:var(--text-2)">
                        @for(r of p.keyRisks; track r){<li>· {{ r }}</li>}
                      </ul>
                      @if(p.intrinsicValue !== null){
                        <div style="display:flex;justify-content:space-between;margin-top:12px;font-size:12px;color:var(--text-2)">
                          <span>Intrinsic</span><span class="mono">$ {{ num(p.intrinsicValue) }}</span>
                        </div>
                      }
                      @if(p.marginOfSafety !== null){
                        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-2)">
                          <span>MoS</span><span class="mono">{{ pct(p.marginOfSafety) }}</span>
                        </div>
                      }
                    </div>
                  </div>
                }
              </section>
            }
          </div>
        }

        @if(tab()==='risk'){
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
            <section class="card"><div class="card-hd"><span class="title">Risk metrics</span></div>
              <div class="card-bd">
                <table class="tbl">
                  <tbody>
                    <tr><td>VaR 95%</td><td class="num">−3.2%</td></tr>
                    <tr><td>VaR 99%</td><td class="num">−5.4%</td></tr>
                    <tr><td>Beta vs SPY</td><td class="num">1.18</td></tr>
                    <tr><td>Ex-ante vol</td><td class="num">22.4%</td></tr>
                    <tr><td>Quality factor</td><td class="num">+0.42</td></tr>
                    <tr><td>Momentum factor</td><td class="num">+0.31</td></tr>
                    <tr><td>Value factor</td><td class="num">−0.18</td></tr>
                    <tr><td>Size factor</td><td class="num">+0.62</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
            <section class="card"><div class="card-hd"><span class="title">Stress scenarios</span></div>
              <div class="card-bd">
                <table class="tbl">
                  <thead><tr><th>Scenario</th><th class="right">Δ NAV</th><th class="right">Δ position</th><th>Notes</th></tr></thead>
                  <tbody>
                    <tr><td>Rate +50bp</td><td class="num">−0.8%</td><td class="num">−2.1%</td><td style="color:var(--text-2)">Duration cushion</td></tr>
                    <tr><td>Tech −10%</td><td class="num">−2.3%</td><td class="num">−9.4%</td><td style="color:var(--text-2)">High beta cluster</td></tr>
                    <tr><td>USD +3%</td><td class="num">−0.5%</td><td class="num">−1.8%</td><td style="color:var(--text-2)">Intl revenue</td></tr>
                    <tr><td>Recession 2008</td><td class="num">−12.4%</td><td class="num">−24%</td><td style="color:var(--text-2)">Stress backtest</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        }

        @if(tab()==='cio'){
          <section class="card" style="max-width:720px;margin:0 auto">
            <div class="card-bd">
              <h2 style="font-family:var(--font-serif);font-size:32px;line-height:38px;font-weight:500;letter-spacing:-0.018em;margin:0">CIO ratification</h2>
              @if(cioOutput(); as c){
                <p style="font-size:14px;line-height:22px;color:var(--text-2);margin-top:14px">{{ c['memo'] || c['rationale'] || 'No CIO memo available.' }}</p>
              } @else {
                <p style="font-size:14px;line-height:22px;color:var(--text-2);margin-top:14px">No CIO memo available for this run.</p>
              }
              <div style="background:var(--acc-long-soft);border:1px solid var(--acc-long-soft);border-radius:8px;padding:14px;margin-top:18px">
                <div class="mono" style="font-size:18px;color:var(--acc-long-fg);font-weight:600">RATIFIED</div>
                <div class="mono" style="font-size:12px;color:var(--text-2);margin-top:4px">CIO · {{ run()!.as_of_date }}</div>
              </div>
              <button class="btn ghost" style="margin-top:14px">Override → reason</button>
            </div>
          </section>
        }

        @if(tab()==='raw'){
          <section class="card">
            <div class="card-hd"><span class="title">Raw output</span>
              <div class="actions">
                <button class="btn ghost sm">Copy</button>
                <button class="btn ghost sm">Download</button>
              </div>
            </div>
            <pre style="font-family:var(--font-mono);font-size:12.5px;background:var(--surface-2);padding:16px;margin:0;border-top:1px solid var(--border);max-height:600px;overflow:auto;color:var(--text-2)">{{ rawJson() }}</pre>
          </section>
        }
      }
    </hf-app-shell>
  `,
})
export class RunsDetailPage implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  readonly store = inject(RunsStore);
  readonly run = this.store.currentRun;
  readonly expanded = signal<Set<string>>(new Set());
  readonly cancelling = signal(false);
  readonly tab = signal<'decision' | 'council' | 'risk' | 'cio' | 'raw'>('decision');
  readonly Math = Math;

  crumbs = computed(() => [
    { label: 'Runs', link: '/runs/new' },
    { label: `#${this.run()?.id ?? ''} · ${this.run()?.tickers?.[0] ?? ''}` },
  ]);

  tabs = [
    { id: 'decision', label: 'Decision', count: 0, attn: false },
    { id: 'council', label: 'Council', count: 8, attn: false },
    { id: 'risk', label: 'Risk', count: 0, attn: false },
    { id: 'cio', label: 'CIO', count: 0, attn: false },
    { id: 'raw', label: 'Raw', count: 17, attn: true },
  ];

  readonly canCancel = computed(() => {
    const s = this.run()?.status;
    return s === 'queued' || s === 'running';
  });

  cancel(): void {
    const id = this.run()?.id;
    if (!id) return;
    this.cancelling.set(true);
    this.store.cancelRun(id).subscribe({
      next: () => { this.cancelling.set(false); this.store.pollRun(id); },
      error: () => this.cancelling.set(false),
    });
  }

  private readonly messageByAgent = computed(() => {
    const map = new Map<string, AgentMessage>();
    for (const m of this.run()?.messages ?? []) map.set(m.agent_name, m);
    return map;
  });

  readonly personas = computed<PersonaCard[]>(() => {
    const run = this.run();
    if (!run) return [];
    const versions = run.agent_versions ?? {};
    const msgs = this.messageByAgent();
    const out: PersonaCard[] = [];
    for (const p of ALL_PERSONAS) {
      if (!msgs.has(p.id)) continue;
      const payload = msgs.get(p.id)!.parsed_output as Record<string, unknown>;
      out.push({
        id: p.id,
        displayName: p.name,
        version: versions[p.id] ?? '—',
        signal: (payload['signal'] as PersonaCard['signal']) ?? 'unknown',
        confidence: Number(payload['confidence'] ?? 0),
        thesis: String(payload['thesis'] ?? ''),
        keyRisks: Array.isArray(payload['key_risks']) ? (payload['key_risks'] as string[]) : [],
        intrinsicValue: payload['intrinsic_value_estimate'] as number | null,
        marginOfSafety: payload['margin_of_safety_pct'] as number | null,
      });
    }
    return out;
  });

  readonly cioOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('cio');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly pmDecisionMsg = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('pm_decision');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly avgConf = computed(() => {
    const p = this.personas();
    if (!p.length) return 0;
    return Math.round(p.reduce((s, x) => s + (x.confidence || 0), 0) / p.length);
  });
  readonly stanceCount = computed(() => {
    const p = this.personas();
    return {
      bull: p.filter((x) => x.signal === 'bullish').length,
      bear: p.filter((x) => x.signal === 'bearish').length,
      neut: p.filter((x) => x.signal === 'neutral').length,
    };
  });
  readonly decision = computed(() => {
    const d = this.pmDecisionMsg();
    return (d?.['action'] as string)?.toUpperCase() ?? (this.stanceCount().bull > this.stanceCount().bear ? 'BUY' : 'HOLD');
  });
  readonly decisionColor = computed(() => {
    const d = this.decision();
    if (d === 'BUY') return 'var(--acc-long-fg)';
    if (d === 'SELL') return 'var(--acc-short-fg)';
    return 'var(--acc-hold-fg)';
  });
  readonly decisionSub = computed(() => {
    const d = this.pmDecisionMsg();
    const sz = (d?.['size_pct'] as number) ?? 2.4;
    return `Open new ${this.decision().toLowerCase()} · ${sz}% of NAV · ~$ 3.0 M`;
  });
  readonly rawJson = computed(() => JSON.stringify(this.run(), null, 2));

  colOf(id: string) { return COL[id] ?? 'c1'; }
  monoOf(id: string) { return MONO[id] ?? id.slice(0, 2).toUpperCase(); }
  stanceShort(s: string) { return s === 'bullish' ? 'BULL' : s === 'bearish' ? 'BEAR' : s === 'neutral' ? 'NEUT' : 'UNK'; }

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    if (id) this.store.pollRun(id);
  }
  ngOnDestroy(): void { this.store.stopPolling(); }

  toggleExpand(id: string): void {
    const n = new Set(this.expanded());
    n.has(id) ? n.delete(id) : n.add(id);
    this.expanded.set(n);
  }

  formatCost(s: string): string { const n = Number(s); return Number.isFinite(n) ? n.toFixed(4) : s; }
  num(v: unknown): string { if (v == null) return '—'; const n = Number(v); return Number.isFinite(n) ? n.toFixed(2) : String(v); }
  pct(v: unknown): string { if (v == null) return '—'; const n = Number(v); return Number.isFinite(n) ? (n * 100).toFixed(2) + '%' : String(v); }

  protected readonly _personaIds = PERSONA_IDS;
}
