import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { ModalComponent } from '../shared/modal.component';
import { RangeRailComponent } from '../shared/range-rail.component';
import { SparklineComponent } from '../shared/sparkline.component';
import { SparkbarComponent } from '../shared/sparkbar.component';
import { GrossNetMeterComponent } from '../shared/gross-net-meter.component';
import { ConfidenceMeterComponent } from '../shared/confidence-meter.component';
import { TickerComponent } from '../shared/ticker.component';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { EmptyStateComponent } from '../shared/empty-state.component';

/**
 * Phase 8 WS-18 — dev/`ct`-only component-in-harness page (ADR 0020). Mounts a
 * single shared widget in isolation with controlled inputs, addressed by
 * `/__ct/:component`, so Playwright can drive real-browser layout / pointer /
 * canvas behaviour that happy-dom can't reach. NOT registered in the production
 * build (environment.ctHarness === false there).
 */
@Component({
  selector: 'hf-ct-harness',
  standalone: true,
  imports: [
    ModalComponent,
    RangeRailComponent,
    SparklineComponent,
    SparkbarComponent,
    GrossNetMeterComponent,
    ConfidenceMeterComponent,
    TickerComponent,
    KpiTileComponent,
    EmptyStateComponent,
  ],
  template: `
    <main id="main-content" tabindex="-1" class="p-6 max-w-[520px]">
      <h1 data-testid="ct-name">CT · {{ component() }}</h1>
      @switch (component()) {
        @case ('modal') {
          <button type="button" class="btn primary" (click)="modalOpen.set(true)" data-testid="ct-open">
            Open modal
          </button>
          @if (modalOpen()) {
            <hf-modal titleId="ct-modal-title" (closed)="modalOpen.set(false)">
              <div class="card p-4">
                <h2 id="ct-modal-title" class="title">CT Modal</h2>
                <button type="button" data-testid="ct-first">First</button>
                <button type="button" data-testid="ct-second">Second</button>
              </div>
            </hf-modal>
          }
        }
        @case ('range-rail') {
          <hf-range-rail [min]="0" [max]="100" [value]="62" [bandLo]="40" [bandHi]="80"
                         ariaLabel="CT range rail" />
        }
        @case ('sparkline') {
          <div data-testid="ct-series"><hf-sparkline [points]="series" /></div>
          <div data-testid="ct-empty"><hf-sparkline [points]="[]" /></div>
          <div data-testid="ct-single"><hf-sparkline [points]="[5]" /></div>
        }
        @case ('sparkbar') {
          <hf-sparkbar [points]="series" />
        }
        @case ('gross-net-meter') {
          <hf-gross-net-meter [longPct]="70" [shortPct]="20" />
        }
        @case ('confidence-meter') {
          <hf-confidence-meter [value]="72" tone="buy" />
        }
        @case ('ticker') {
          <hf-ticker ticker="AAPL" />
        }
        @case ('kpi-tile') {
          <hf-kpi-tile eyebrow="NAV · Manual Book" value="$1.20M" sub="9 positions"
                       [spark]="series" tone="up" />
          <hf-empty-state message="Nothing yet" detail="Add one to begin" />
        }
        @case ('empty-state') {
          <hf-empty-state message="No data" detail="Try adjusting your filters" />
        }
        @default {
          <p data-testid="ct-unknown">Unknown component: {{ component() }}</p>
        }
      }
    </main>
  `,
})
export class CtHarnessPage {
  private readonly route = inject(ActivatedRoute);
  readonly component = signal(this.route.snapshot.paramMap.get('component') ?? '');
  readonly modalOpen = signal(false);
  readonly series = [1, 3, 2, 5, 4, 6, 5, 7, 6, 8];
}
