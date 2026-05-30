import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';

import { AppShellComponent } from '../shared/app-shell.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { ScreenerStore } from '../../abstraction/screener.store';
import {
  AssetClass,
  Preset,
  SavedScreen,
  ScreenCriterion,
  ScreenResultRow,
  WatchlistItem,
} from '../../core/models/screener.model';
import { EnterPositionModalComponent } from '../portfolio/enter-position.modal';
import { FilterEditorComponent } from './filter-editor.component';
import { PresetBarComponent } from './preset-bar.component';
import { ResultsTableComponent } from './results-table.component';
import { SavedScreensComponent } from './saved-screens.component';
import { WatchlistPanelComponent } from '../watchlist/watchlist-panel.component';

type ViewMode = 'results' | 'watchlist';

@Component({
  selector: 'hf-screener-page',
  standalone: true,
  imports: [
    CommonModule,
    AppShellComponent,
    EmptyStateComponent,
    EnterPositionModalComponent,
    FilterEditorComponent,
    PresetBarComponent,
    ResultsTableComponent,
    SavedScreensComponent,
    WatchlistPanelComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'Screener'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Discovery</div>
          <h1 class="mt-1.5">Market Screener</h1>
          <p class="text-text-3 text-[12.5px] mt-1 max-w-[640px]">
            Filter the investable universe by the fields traders actually use,
            run a predefined screen, or save your own.
          </p>
        </div>
        <div class="head-right" role="radiogroup" aria-label="Screener view">
          <button
            type="button"
            class="seg-btn"
            role="radio"
            [attr.aria-checked]="view() === 'results'"
            [class.active]="view() === 'results'"
            (click)="setView('results')"
          >Results</button>
          <button
            type="button"
            class="seg-btn"
            role="radio"
            [attr.aria-checked]="view() === 'watchlist'"
            [class.active]="view() === 'watchlist'"
            (click)="setView('watchlist')"
          >
            Watchlist
            <span class="count">({{ store.watchlist().length }})</span>
          </button>
        </div>
      </div>

      @if (view() === 'results') {
        <section class="card mb-4">
          <div class="card-bd flex flex-col gap-3">
            <hf-screener-preset-bar
              [presets]="store.presets()"
              [activeId]="store.activePresetId()"
              (picked)="onPresetPicked($event)"
            />
            <hf-screener-saved-screens
              [saved]="store.saved()"
              [selectedId]="selectedSavedId()"
              (loaded)="onSavedLoaded($event)"
              (deleted)="onSavedDeleted($event)"
              (saveRequested)="onSaveRequested($event)"
            />
          </div>
        </section>

        <hf-screener-filter-editor
          class="block mb-4"
          [fields]="store.fields()"
          [criteria]="store.activeCriteria()"
          [assetClass]="store.activeAssetClass()"
          (criteriaChange)="onCriteriaChange($event)"
          (assetClassChange)="onAssetClassChange($event)"
          (reset)="onReset()"
        />

        <section class="card">
          <div class="card-hd run-bar">
            <h2 class="title">Results</h2>
            @if (store.result(); as r) {
              <span class="muted">
                {{ r.returned_count }} of {{ r.universe_size }} ·
                <span class="mono">{{ r.as_of | date: 'medium' }}</span>
                @if (r.truncated) {
                  · <span class="pill warn"><span class="dot"></span>truncated</span>
                }
              </span>
            }
            <div class="actions">
              <button
                type="button"
                class="btn primary"
                [disabled]="store.running()"
                (click)="runScreen()"
              >
                {{ store.running() ? 'Running…' : 'Run screen' }}
              </button>
            </div>
          </div>
          <div class="card-bd p-0">
            @if (store.error()) {
              <p
                class="alert"
                role="alert"
                aria-live="polite"
              >{{ store.error() }}</p>
            }
            @if (store.result(); as r) {
              @if (r.warnings.length) {
                @for (w of r.warnings; track w) {
                  <p class="warn-banner">{{ w }}</p>
                }
              }
              @if (r.rows.length === 0) {
                <div class="p-4">
                  <hf-empty-state
                    message="No matches."
                    detail="Try loosening your filters or pick a different preset."
                  />
                </div>
              } @else {
                <hf-screener-results-table
                  [rows]="r.rows"
                  [initialSort]="store.activeSort()"
                  (toggleWatchlist)="onToggleWatchlist($event)"
                  (addToPortfolio)="onAddToPortfolio($event)"
                  (sendToRun)="onSendToRun($event)"
                />
              }
            } @else if (store.running()) {
              <div class="p-4 flex flex-col gap-2" aria-busy="true">
                @for (_ of [1,2,3,4,5]; track $index) {
                  <div class="skel h-[24px] w-full"></div>
                }
              </div>
            } @else {
              <div class="p-4">
                <hf-empty-state
                  message="Pick a preset or build a filter, then hit Run screen."
                  detail="Predefined screens load common setups into the editor — click Run when you're ready."
                />
              </div>
            }
          </div>
        </section>
      } @else {
        <hf-screener-watchlist-panel
          [items]="store.watchlist()"
          (add)="onWatchlistAdd($event)"
          (remove)="onWatchlistRemove($event)"
          (addToPortfolio)="onAddToPortfolio($event)"
          (sendToRun)="onSendToRun($event)"
        />
      }

      <hf-enter-position-modal
        [open]="entryOpen()"
        [prefill]="entryPrefill()"
        (closed)="onEntryClosed($event)"
      />
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 16px;
      }
      .head-right {
        display: inline-flex;
        gap: 0;
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        overflow: hidden;
      }
      .seg-btn {
        background: transparent;
        border: 0;
        padding: 7px 14px;
        font-size: 12px;
        color: var(--text-2);
        cursor: pointer;
      }
      .seg-btn.active {
        background: var(--acc-info-soft);
        color: var(--text);
      }
      .count {
        margin-left: 4px;
        color: var(--text-3);
      }
      .run-bar {
        align-items: center;
      }
      .run-bar .muted {
        color: var(--text-3);
        font-size: var(--fs-12);
      }
      .run-bar .actions {
        margin-left: auto;
      }
      .alert {
        padding: 10px 14px;
        border-bottom: 1px solid var(--border);
        color: var(--acc-short-fg);
        background: var(--acc-short-soft);
        margin: 0;
      }
      .warn-banner {
        padding: 8px 14px;
        border-bottom: 1px solid var(--border);
        background: var(--acc-hold-soft);
        color: var(--acc-hold-fg);
        font-size: var(--fs-12);
        margin: 0;
      }
      .pill .dot { background: var(--acc-hold); }
    `,
  ],
})
export class ScreenerPage implements OnInit {
  readonly store = inject(ScreenerStore);
  private readonly router = inject(Router);

  readonly view = signal<ViewMode>('results');
  readonly entryOpen = signal(false);
  readonly entryPrefill = signal<{ ticker: string; initialPrice?: number } | null>(
    null,
  );

  readonly selectedSavedId = computed<number | ''>(() => {
    // No explicit selection — the dropdown reflects whichever screen was
    // last loaded (best-effort match by current criteria signature).
    return '';
  });

  ngOnInit(): void {
    this.store.loadFields().subscribe();
    this.store.loadPresets().subscribe();
    this.store.loadSaved().subscribe();
    this.store.loadWatchlist().subscribe();
  }

  setView(v: ViewMode): void {
    this.view.set(v);
    if (v === 'watchlist') {
      this.store.loadWatchlist().subscribe();
    }
  }

  runScreen(): void {
    this.store.runScreen().subscribe();
  }

  onPresetPicked(p: Preset): void {
    // Apply the preset's filter set into the editor but do NOT auto-run —
    // the user clicks "Run screen" when they're ready.
    this.store.applyPreset(p);
  }

  onCriteriaChange(criteria: Record<string, ScreenCriterion>): void {
    // Replace the active criteria signal-by-signal (each setter handles delete).
    const active = this.store.activeCriteria();
    // Find keys to remove
    for (const k of Object.keys(active)) {
      if (!(k in criteria)) this.store.setCriterion(k, null);
    }
    for (const [k, v] of Object.entries(criteria)) {
      this.store.setCriterion(k, v);
    }
  }

  onAssetClassChange(ac: AssetClass): void {
    this.store.setAssetClass(ac);
  }

  onReset(): void {
    this.store.resetCriteria();
  }

  onSavedLoaded(s: SavedScreen): void {
    // Load the saved screen into the editor; let the user click Run.
    this.store.loadSavedInto(s);
  }

  onSavedDeleted(s: SavedScreen): void {
    this.store.deleteSaved(s.id).subscribe();
  }

  onSaveRequested(ev: { name: string }): void {
    this.store.saveScreen(ev.name).subscribe({
      error: (e) => {
        const detail = e?.error?.detail || e?.error?.name || 'Save failed';
        this.store.setError(String(detail));
      },
    });
  }

  onToggleWatchlist(row: ScreenResultRow): void {
    if (row.in_watchlist) {
      this.store.removeFromWatchlist(row.ticker).subscribe();
    } else {
      this.store.addToWatchlist(row.ticker).subscribe();
    }
  }

  onAddToPortfolio(row: ScreenResultRow | WatchlistItem): void {
    const ticker = (row as ScreenResultRow).ticker.toUpperCase();
    const price =
      (row as ScreenResultRow).price !== undefined
        ? Number((row as ScreenResultRow).price)
        : Number((row as WatchlistItem).price);
    this.entryPrefill.set({
      ticker,
      initialPrice: Number.isFinite(price) && price > 0 ? price : undefined,
    });
    this.entryOpen.set(true);
  }

  onSendToRun(row: ScreenResultRow | WatchlistItem): void {
    const ticker = (row as ScreenResultRow).ticker.toUpperCase();
    this.router.navigate(['/runs/new'], { queryParams: { ticker } });
  }

  onEntryClosed(_e: { saved: boolean }): void {
    this.entryOpen.set(false);
    this.entryPrefill.set(null);
  }

  onWatchlistAdd(ticker: string): void {
    this.store.addToWatchlist(ticker).subscribe();
  }

  onWatchlistRemove(it: WatchlistItem): void {
    this.store.removeFromWatchlist(it.ticker).subscribe();
  }
}
