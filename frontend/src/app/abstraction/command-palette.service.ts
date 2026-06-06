import { Injectable, computed, inject, signal } from '@angular/core';
import { forkJoin } from 'rxjs';
import { RunsStore } from './runs.store';
import { StrategiesStore } from './strategies.store';
import { BacktestsStore } from './backtests.store';

/** ADR 0004: a single result row rendered in the ⌘K palette. */
export interface PaletteResult {
  source: 'run' | 'strategy' | 'backtest' | 'action';
  label: string;
  sublabel: string;
  route: (string | number)[];
}

const MAX_RESULTS = 20;

/** Static navigation actions surfaced in the palette. "New run" was demoted
 *  from the sidebar rail (HHF-03 / §E) — ⌘K is now a primary way to reach it. */
const ACTIONS: { label: string; sublabel: string; route: (string | number)[]; keywords: string }[] = [
  { label: 'New run', sublabel: 'Start a new analysis run', route: ['/runs/new'], keywords: 'new run create start analysis' },
];

@Injectable({ providedIn: 'root' })
export class CommandPaletteService {
  private readonly runs = inject(RunsStore);
  private readonly strategies = inject(StrategiesStore);
  private readonly backtests = inject(BacktestsStore);

  /** True while the first cold-load of the three sources is in flight. */
  readonly loading = signal(false);
  private hydrated = false;

  readonly query = signal('');

  readonly results = computed<PaletteResult[]>(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return [];
    const out: PaletteResult[] = [];

    // Static actions first, so e.g. typing "new run" surfaces the action on top.
    for (const a of ACTIONS) {
      if (a.label.toLowerCase().includes(q) || a.keywords.includes(q)) {
        out.push({ source: 'action', label: a.label, sublabel: a.sublabel, route: a.route });
      }
    }

    for (const r of this.runs.runs()) {
      const idMatch = String(r.id).includes(q);
      const tickerMatch = (r.tickers || []).some((t) => t.toLowerCase().includes(q));
      if (idMatch || tickerMatch) {
        out.push({
          source: 'run',
          label: `Run #${r.id} · ${(r.tickers || []).join(', ') || '—'}`,
          sublabel: `${r.status} · ${r.as_of_date}`,
          route: ['/runs', r.id],
        });
      }
    }

    for (const s of this.strategies.strategies()) {
      if (s.name.toLowerCase().includes(q) || s.kind.toLowerCase().includes(q)) {
        out.push({
          source: 'strategy',
          label: s.name,
          sublabel: `${s.kind} · ${s.universe_name}`,
          route: ['/strategies', s.id],
        });
      }
    }

    for (const b of this.backtests.list()) {
      const idMatch = String(b.id).includes(q);
      const nameMatch = b.name.toLowerCase().includes(q);
      const tickerMatch = (b.universe || []).some((t) => t.toLowerCase().includes(q));
      if (idMatch || nameMatch || tickerMatch) {
        out.push({
          source: 'backtest',
          label: `Backtest #${b.id} · ${b.name}`,
          sublabel: `${b.status} · ${b.start_date} → ${b.end_date}`,
          route: ['/backtests', b.id],
        });
      }
    }

    return out.slice(0, MAX_RESULTS);
  });

  /** Cold-load the three sources on first open. Idempotent. */
  ensureLoaded(): void {
    if (this.hydrated) return;
    this.hydrated = true;
    this.loading.set(true);
    forkJoin({
      runs: this.runs.listRuns(),
      strategies: this.strategies.list(),
      backtests: this.backtests.listBacktests(),
    }).subscribe({
      next: () => this.loading.set(false),
      error: () => {
        this.loading.set(false);
        // Allow retry on next open.
        this.hydrated = false;
      },
    });
  }

  reset(): void {
    this.query.set('');
  }
}
