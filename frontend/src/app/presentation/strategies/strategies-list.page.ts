import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { ConfirmService } from '../shared/confirm.service';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { StrategiesStore } from '../../abstraction/strategies.store';

@Component({
  selector: 'hf-strategies-list',
  standalone: true,
  imports: [CommonModule, RouterLink, DatePipe, AppShellComponent, EmptyStateComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Your strategies</div>
          <h1 class="mt-1.5">Strategies</h1>
        </div>
        <div class="head-actions">
          <a class="btn primary" routerLink="/strategies/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New strategy
          </a>
        </div>
      </div>

      <section class="card">
        <!-- P10 §D1: archive semantics. The list defaults to ACTIVE strategies;
             archived ones (38/40 can never pass the delete gate, so archive is
             the cleanup verb) sit behind the chip. -->
        @if (archivedCount() > 0) {
          <div class="card-hd">
            <h2 class="title">{{ showArchived() ? 'All strategies' : 'Active strategies' }}</h2>
            <button type="button" class="chip" [class.chip-on]="showArchived()"
                    (click)="showArchived.set(!showArchived())"
                    data-test="archived-toggle">
              Archived ({{ archivedCount() }})
            </button>
          </div>
        }
        @if (visible().length === 0 && !showArchived()) {
          <hf-empty-state
            message="No active strategies."
            detail="Create one to start the autonomous long-short engine, or check the Archived chip.">
            <a class="btn primary" routerLink="/strategies/new">Create a strategy</a>
          </hf-empty-state>
        } @else {
          <div class="tbl-scroll">
          <table class="tbl">
            <thead><tr>
              <th>Name</th>
              <th>Universe</th>
              <th>Gross / Net</th>
              <th>K longs/shorts</th>
              <th>Preset</th>
              <th>Last run</th>
              <th></th>
            </tr></thead>
            <tbody>
              @for (s of visible(); track s.id) {
                <tr [class.archived-row]="!s.is_active">
                  <td class="text-text font-medium">
                    {{ s.name }}
                    @if (!s.is_active) {
                      <span class="pill warn ml-1"><span class="dot"></span>archived</span>
                    }
                  </td>
                  <td class="text-text-2">{{ s.universe_name }}</td>
                  <td class="mono">{{ s.target_gross_pct }} / {{ s.target_net_pct }}</td>
                  <td class="mono">{{ s.top_k_longs }} / {{ s.top_k_shorts }}</td>
                  <td>{{ s.model_preset }}</td>
                  <td class="mono text-[11.5px] text-text-3">
                    {{ s.last_run_at ? (s.last_run_at | date: 'short') : '—' }}
                  </td>
                  <td class="right whitespace-nowrap">
                    <a [routerLink]="['/strategies', s.id]" class="text-[var(--acc-info-fg)] text-2xs">Open</a>
                    <!-- Archive replaces the mostly-unusable Delete as the cleanup verb. -->
                    <button type="button" class="btn ghost sm ml-2"
                            (click)="toggleArchive(s.id, s.name, s.is_active)"
                            [disabled]="archivingId() === s.id"
                            [attr.data-test]="'archive-strategy-' + s.id">
                      {{ s.is_active ? 'Archive' : 'Unarchive' }}
                    </button>
                    @if ((s.targets_count_active ?? 0) === 0) {
                      <button type="button" class="btn danger sm ml-2"
                              (click)="confirmDelete(s.id, s.name)"
                              [disabled]="deletingId() === s.id"
                              [attr.data-test]="'delete-strategy-' + s.id">
                        Delete
                      </button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
          </div>
        }
      </section>
    </hf-app-shell>
  `,
  styles: [`
    .archived-row { opacity: 0.6; }
    .chip {
      background: transparent; border: 1px solid var(--border); color: var(--text-2);
      font-size: 11px; line-height: 1; padding: 0 10px; min-height: 24px;
      display: inline-flex; align-items: center; border-radius: var(--r-full); cursor: pointer;
    }
    .chip:hover { background: var(--surface-2); }
    .chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }
    .chip-on { background: var(--surface-2); color: var(--text); border-color: var(--text-3); }
  `],
})
export class StrategiesListPage implements OnInit {
  readonly store = inject(StrategiesStore);
  private readonly confirm = inject(ConfirmService);
  readonly deletingId = signal<number | null>(null);
  readonly archivingId = signal<number | null>(null);
  readonly showArchived = signal(false);

  readonly archivedCount = computed(
    () => this.store.strategies().filter((s) => !s.is_active).length,
  );
  readonly visible = computed(() => {
    const all = this.store.strategies();
    return this.showArchived() ? all : all.filter((s) => s.is_active);
  });

  // Load EVERYTHING (the server's default list is active-only) so the chip
  // count and the toggle work without a second round trip.
  ngOnInit(): void { this.store.list({ includeArchived: true }).subscribe(); }

  // P10 §D1: archive/unarchive — the viable cleanup verb (delete is 409-gated
  // once a strategy has any non-cancelled cycle).
  toggleArchive(id: number, name: string, isActive: boolean): void {
    if (this.archivingId() !== null) return;
    this.archivingId.set(id);
    this.store.setActive(id, !isActive).subscribe({
      next: () => {
        this.archivingId.set(null);
        this.store.list({ includeArchived: true }).subscribe();
      },
      error: () => {
        this.archivingId.set(null);
        void this.confirm.notify({ title: `Could not ${isActive ? 'archive' : 'unarchive'} "${name}".` });
      },
    });
  }

  // P4 WS-C: delete a strategy with no non-cancelled cycles. Ask first —
  // this also removes the freshly-seeded strategy book.
  async confirmDelete(id: number, name: string): Promise<void> {
    if (this.deletingId() !== null) return;
    const ok = await this.confirm.ask({
      title: `Delete strategy "${name}"?`,
      body: 'This also removes its (empty) strategy book. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    this.deletingId.set(id);
    this.store.deleteStrategy(id).subscribe({
      next: () => { this.deletingId.set(null); this.store.list({ includeArchived: true }).subscribe(); },
      error: () => { this.deletingId.set(null); void this.confirm.notify({ title: 'Could not delete this strategy.' }); },
    });
  }
}
