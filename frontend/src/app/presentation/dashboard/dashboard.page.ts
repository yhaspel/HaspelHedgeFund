import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { AuthStore } from '../../abstraction/auth.store';
import { MacroStore } from '../../abstraction/macro.store';
import { RunsStore } from '../../abstraction/runs.store';
import { StrategiesStore } from '../../abstraction/strategies.store';

type PillKind = 'ok' | 'warn' | 'err' | 'info' | '';

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Dashboard'}]">
      <div class="page-head">
        <div>
          <h1>Dashboard</h1>
        </div>
        <div class="head-actions">
          <a class="btn" routerLink="/backtests/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New backtest
          </a>
          <a class="btn primary" routerLink="/runs/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New analysis
          </a>
        </div>
      </div>

      <!-- Macro regime -->
      <section class="card" style="margin-bottom:18px">
        <div class="card-hd">
          <span class="title">Macro regime</span>
          @if (macro.snapshot(); as s) {
            <span class="pill"><span class="dot"></span>as of {{ s.as_of_date }}</span>
          }
        </div>
        <div class="card-bd">
          @if (macro.snapshot(); as s) {
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px">
              <span class="pill" [class.ok]="chipKind('growth', s.growth_quadrant)==='ok'"
                [class.warn]="chipKind('growth', s.growth_quadrant)==='warn'"
                [class.err]="chipKind('growth', s.growth_quadrant)==='err'">
                <span class="dot"></span>growth · {{ s.growth_quadrant }}
              </span>
              <span class="pill" [class.ok]="chipKind('inflation', s.inflation_regime)==='ok'"
                [class.warn]="chipKind('inflation', s.inflation_regime)==='warn'"
                [class.err]="chipKind('inflation', s.inflation_regime)==='err'">
                <span class="dot"></span>inflation · {{ s.inflation_regime }}
              </span>
              <span class="pill" [class.ok]="chipKind('curve', s.yield_curve_state)==='ok'"
                [class.warn]="chipKind('curve', s.yield_curve_state)==='warn'"
                [class.err]="chipKind('curve', s.yield_curve_state)==='err'">
                <span class="dot"></span>yield curve · {{ s.yield_curve_state }}
              </span>
              <span class="pill" [class.ok]="chipKind('policy', s.policy_stance)==='ok'"
                [class.warn]="chipKind('policy', s.policy_stance)==='warn'"
                [class.info]="chipKind('policy', s.policy_stance)==='info'">
                <span class="dot"></span>policy · {{ s.policy_stance }}
              </span>
            </div>
            <p style="font-size:13px;color:var(--text-2);line-height:20px;margin:0">{{ s.narrative }}</p>
          } @else {
            <p style="font-size:13px;color:var(--text-3);margin:0">Loading macro snapshot…</p>
          }
        </div>
      </section>

      <!-- Current book -->
      <section class="card" style="margin-bottom:18px">
        <div class="card-hd">
          <span class="title">Current book</span>
          @if (book(); as b) {
            <span class="pill"><span class="dot"></span>
              {{ b.strategy_name }} · {{ b.as_of_date }} · gross {{ b.gross_pct }} · net {{ b.net_pct }}
            </span>
          }
        </div>
        <div class="card-bd">
          @if (book(); as b) {
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
              <div>
                <div class="eyebrow" style="color:var(--acc-long-fg);margin-bottom:6px">Top longs</div>
                <table class="tbl">
                  <tbody>
                    @for (row of b.longs; track row.ticker) {
                      <tr>
                        <td class="mono" style="color:var(--text)">{{ row.ticker }}</td>
                        <td class="num">{{ row.weight.toFixed(2) }}%</td>
                      </tr>
                    }
                    @if (b.longs.length === 0) {
                      <tr><td colspan="2" style="color:var(--text-3)">— none —</td></tr>
                    }
                  </tbody>
                </table>
              </div>
              <div>
                <div class="eyebrow" style="color:var(--acc-short-fg);margin-bottom:6px">Top shorts</div>
                <table class="tbl">
                  <tbody>
                    @for (row of b.shorts; track row.ticker) {
                      <tr>
                        <td class="mono" style="color:var(--text)">{{ row.ticker }}</td>
                        <td class="num">{{ row.weight.toFixed(2) }}%</td>
                      </tr>
                    }
                    @if (b.shorts.length === 0) {
                      <tr><td colspan="2" style="color:var(--text-3)">— none —</td></tr>
                    }
                  </tbody>
                </table>
              </div>
            </div>
          } @else {
            <p style="font-size:13px;color:var(--text-3);margin:0">
              No autonomous cycles yet.
              <a routerLink="/strategies/new" style="color:var(--acc-info-fg)">Create a strategy</a>.
            </p>
          }
        </div>
      </section>

      <!-- Quick links -->
      <section class="card" style="margin-bottom:18px">
        <div class="card-bd" style="display:flex;flex-wrap:wrap;gap:8px">
          <a class="btn" routerLink="/strategies" data-test="strategies-link">Strategies</a>
          <a class="btn" routerLink="/settings/models" data-test="settings-link">Settings</a>
        </div>
      </section>

      <!-- Recent runs -->
      <section class="card">
        <div class="card-hd"><span class="title">Recent runs</span></div>
        @if (runs.runs().length === 0) {
          <div class="card-bd"><p style="font-size:13px;color:var(--text-3);margin:0">
            No runs yet. Start your first analysis above.
          </p></div>
        } @else {
          <table class="tbl">
            <thead><tr>
              <th>#</th><th>Tickers</th><th>As-of</th><th>Status</th>
              <th class="right">Cost</th><th></th>
            </tr></thead>
            <tbody>
              @for (r of runs.runs(); track r.id) {
                <tr>
                  <td class="mono">{{ r.id }}</td>
                  <td class="mono" style="color:var(--text)">{{ r.tickers.join(', ') }}</td>
                  <td class="mono" style="color:var(--text-2)">{{ r.as_of_date }}</td>
                  <td>
                    <span class="pill"
                      [class.ok]="r.status==='done'"
                      [class.warn]="r.status==='running' || r.status==='queued'"
                      [class.err]="r.status==='failed'">
                      <span class="dot"></span>{{ r.status }}
                    </span>
                  </td>
                  <td class="num">$ {{ (+r.total_cost_usd).toFixed(4) }}</td>
                  <td><a [routerLink]="['/runs', r.id]" style="color:var(--acc-info-fg)">view</a></td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </hf-app-shell>
  `,
})
export class DashboardPage implements OnInit {
  readonly auth = inject(AuthStore);
  readonly runs = inject(RunsStore);
  readonly macro = inject(MacroStore);
  readonly strategies = inject(StrategiesStore);

  book = signal<{
    strategy_name: string;
    as_of_date: string;
    gross_pct: string;
    net_pct: string;
    longs: { ticker: string; weight: number }[];
    shorts: { ticker: string; weight: number }[];
  } | null>(null);

  ngOnInit(): void {
    this.runs.listRuns().subscribe();
    this.macro.loadSnapshot().subscribe({ error: () => {} });
    this.strategies.list().subscribe((ss) => {
      const recent = ss.find((s) => !!s.last_run_at) ?? ss[0];
      if (!recent) return;
      this.strategies.listCycles(recent.id).subscribe((cs) => {
        const done = cs.find((c) => c.status === 'done') ?? cs[0];
        if (!done) return;
        this.strategies.cycleDetail(recent.id, done.id).subscribe((d) => {
          const longs = Object.entries(d.target_weights)
            .filter(([, w]) => Number(w) > 0)
            .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
            .sort((a, b) => b.weight - a.weight).slice(0, 5);
          const shorts = Object.entries(d.target_weights)
            .filter(([, w]) => Number(w) < 0)
            .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
            .sort((a, b) => a.weight - b.weight).slice(0, 5);
          this.book.set({
            strategy_name: recent.name,
            as_of_date: d.as_of_date,
            gross_pct: d.gross_pct,
            net_pct: d.net_pct,
            longs, shorts,
          });
        });
      });
    });
  }

  chipKind(kind: string, value: string): PillKind {
    const map: Record<string, Record<string, PillKind>> = {
      growth: { expansion: 'ok', recovery: 'ok', slowdown: 'warn', recession: 'err' },
      inflation: { low: 'ok', moderate: '', high: 'warn', accelerating: 'err' },
      curve: { normal: 'ok', flat: 'warn', inverted: 'err' },
      policy: { easing: 'info', neutral: '', tightening: 'warn' },
    };
    return map[kind]?.[value] ?? '';
  }
}
