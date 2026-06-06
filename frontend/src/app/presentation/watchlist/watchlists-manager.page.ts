import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiClient } from '../../core/api/api-client';
import { AppShellComponent } from '../shared/app-shell.component';
import { ConfirmService } from '../shared/confirm.service';

interface WatchlistMeta {
  id: number;
  name: string;
  is_default: boolean;
  ticker_count: number;
}
interface TickerRow {
  id: number;
  ticker: string;
  note: string;
  price?: string | null;
  change_pct?: number | null;
}
interface ListDetail {
  id: number;
  name: string;
  is_default: boolean;
  items: TickerRow[];
}

@Component({
  selector: 'hf-watchlists-manager-page',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Watchlists' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Discovery</div>
          <h1 class="mt-1.5">Watchlists</h1>
          <p class="sub">
            Named ticker sets you can schedule independently. Star tickers from
            the Screener, type a symbol below, or let the questionnaire pre-fill
            your favorites into the default list.
          </p>
        </div>
      </div>

      @if (error()) { <p class="alert" role="alert">{{ error() }}</p> }

      <div class="list-bar">
        @for (w of lists(); track w.id) {
          <button
            class="chip"
            [class.active]="w.id === selectedId()"
            (click)="select(w.id)"
          >
            {{ w.name }}<span class="cnt">{{ w.ticker_count }}</span>
            @if (w.is_default) { <span class="def">★</span> }
          </button>
        }
        @if (creating()) {
          <span class="new-inline">
            <input [(ngModel)]="newName" placeholder="List name" (keyup.enter)="createList()" />
            <button class="btn sm primary" (click)="createList()">Add</button>
            <button class="btn sm ghost" (click)="creating.set(false)">×</button>
          </span>
        } @else {
          <button class="chip add" (click)="creating.set(true)">+ New list</button>
        }
      </div>

      @if (detail()) {
        <section class="card">
          <div class="card-hd">
            <h2 class="title">{{ detail()?.name }}</h2>
            <div class="hd-actions">
              <button class="btn sm" (click)="scheduleThis()">Schedule this list</button>
              <button class="btn sm ghost" (click)="rename()">Rename</button>
              @if (!detail()?.is_default) {
                <button class="btn sm danger" (click)="deleteList()">Delete list</button>
              }
            </div>
          </div>

          <div class="add-row">
            <input [(ngModel)]="newTicker" placeholder="Add ticker (e.g. AAPL)" (keyup.enter)="addTicker()" />
            <button class="btn primary" (click)="addTicker()">Add</button>
          </div>

          @if ((detail()?.items?.length ?? 0) === 0) {
            <p class="empty">No tickers in this list yet.</p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead><tr><th>Ticker</th><th class="r">Price</th><th class="r">Δ%</th><th></th></tr></thead>
                <tbody>
                  @for (t of detail()!.items; track t.id) {
                    <tr>
                      <td>{{ t.ticker }}</td>
                      <td class="r">{{ t.price ?? '—' }}</td>
                      <td class="r" [class.up]="(t.change_pct ?? 0) > 0" [class.dn]="(t.change_pct ?? 0) < 0">
                        {{ t.change_pct !== null && t.change_pct !== undefined ? (t.change_pct + '%') : '—' }}
                      </td>
                      <td class="r">
                        <button class="btn sm" (click)="sendToRun(t.ticker)">Analyze</button>
                        <button class="btn sm danger" (click)="removeTicker(t.ticker)">Remove</button>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { margin-bottom:16px; }
      .sub { color: var(--text-3); font-size:12.5px; margin-top:4px; max-width:640px; }
      .list-bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:14px; }
      .chip { padding:6px 12px; border-radius:16px; background: var(--surface-2); color: var(--text-2); border:1px solid var(--border); cursor:pointer; font-size:13px; }
      .chip.active { background: var(--acc-info-soft); color: var(--acc-info-fg); border-color: var(--acc-info); }
      .chip.add { border-style:dashed; color: var(--text-3); }
      .chip .cnt { margin-left:6px; font-size:11px; color: var(--text-3); }
      .chip .def { margin-left:4px; color: var(--acc-info); }
      .new-inline { display:inline-flex; gap:6px; align-items:center; }
      .new-inline input { padding:6px 10px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text); border:1px solid var(--border); }
      .card { margin-bottom:16px; }
      .hd-actions { display:flex; gap:6px; }
      .add-row { display:flex; gap:8px; margin:8px 0 14px; }
      .add-row input { flex:1; padding:8px 12px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text); border:1px solid var(--border); }
      .empty { color: var(--text-3); font-size:13px; padding:8px 2px; }
      .alert { padding:10px 14px; background: var(--acc-short-soft); color: var(--acc-short-fg); border-radius: var(--r-6); margin:0 0 12px; }
      .tbl .r { text-align:right; } .tbl .up { color: var(--acc-long-fg); } .tbl .dn { color: var(--acc-short-fg); }
    `,
  ],
})
export class WatchlistsManagerPage implements OnInit {
  private readonly api = inject(ApiClient);
  private readonly router = inject(Router);
  private readonly confirm = inject(ConfirmService);

  readonly lists = signal<WatchlistMeta[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly detail = signal<ListDetail | null>(null);
  readonly creating = signal(false);
  readonly error = signal<string | null>(null);
  newName = '';
  newTicker = '';

  ngOnInit(): void {
    this.reloadLists(true);
  }

  reloadLists(selectDefault = false): void {
    this.api.get<WatchlistMeta[]>('/watchlists/').subscribe((ls) => {
      this.lists.set(ls);
      if (selectDefault && ls.length) {
        const def = ls.find((l) => l.is_default) ?? ls[0];
        this.select(def.id);
      } else if (this.selectedId() === null && ls.length) {
        this.select(ls[0].id);
      }
    });
  }

  select(id: number): void {
    this.selectedId.set(id);
    this.api.get<ListDetail>(`/watchlists/${id}/`).subscribe((d) => this.detail.set(d));
  }

  createList(): void {
    const name = this.newName.trim();
    if (!name) return;
    this.api.post<WatchlistMeta>('/watchlists/', { name }).subscribe({
      next: (w) => {
        this.creating.set(false);
        this.newName = '';
        this.reloadLists();
        this.select(w.id);
      },
      error: (e) => this.error.set(e?.error?.detail ?? 'Failed to create list'),
    });
  }

  async rename(): Promise<void> {
    const d = this.detail();
    if (!d) return;
    const name = await this.confirm.askText({ title: 'Rename list', label: 'List name', initialValue: d.name });
    if (!name) return;
    this.api.patch(`/watchlists/${d.id}/`, { name }).subscribe({
      next: () => {
        this.reloadLists();
        this.select(d.id);
      },
      error: (e) => this.error.set(e?.error?.detail ?? 'Rename failed'),
    });
  }

  async deleteList(): Promise<void> {
    const d = this.detail();
    if (!d) return;
    const ok = await this.confirm.ask({ title: `Delete list "${d.name}"?`, confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    this.api.delete(`/watchlists/${d.id}/`).subscribe(() => {
      this.selectedId.set(null);
      this.detail.set(null);
      this.reloadLists(true);
    });
  }

  addTicker(): void {
    const id = this.selectedId();
    const ticker = this.newTicker.trim().toUpperCase();
    if (!id || !ticker) return;
    this.api.post(`/watchlists/${id}/tickers/`, { ticker }).subscribe({
      next: () => {
        this.newTicker = '';
        this.select(id);
        this.reloadLists();
      },
      error: (e) => this.error.set(e?.error?.detail ?? 'Failed to add ticker'),
    });
  }

  removeTicker(ticker: string): void {
    const id = this.selectedId();
    if (!id) return;
    this.api.delete(`/watchlists/${id}/tickers/${ticker}/`).subscribe(() => {
      this.select(id);
      this.reloadLists();
    });
  }

  sendToRun(ticker: string): void {
    this.router.navigate(['/runs/new'], { queryParams: { ticker: ticker.toUpperCase() } });
  }

  scheduleThis(): void {
    this.router.navigate(['/schedules']);
  }
}
