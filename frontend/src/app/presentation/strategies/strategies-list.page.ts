import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
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
        @if (store.strategies().length === 0) {
          <hf-empty-state
            message="No strategies yet."
            detail="Create one to start the autonomous long-short engine.">
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
              @for (s of store.strategies(); track s.id) {
                <tr>
                  <td class="text-text font-medium">{{ s.name }}</td>
                  <td class="text-text-2">{{ s.universe_name }}</td>
                  <td class="mono">{{ s.target_gross_pct }} / {{ s.target_net_pct }}</td>
                  <td class="mono">{{ s.top_k_longs }} / {{ s.top_k_shorts }}</td>
                  <td>{{ s.model_preset }}</td>
                  <td class="mono text-[11.5px] text-text-3">
                    {{ s.last_run_at ? (s.last_run_at | date: 'short') : '—' }}
                  </td>
                  <td class="right whitespace-nowrap">
                    <a [routerLink]="['/strategies', s.id]" class="text-[var(--acc-info-fg)] text-2xs">Open</a>
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
})
export class StrategiesListPage implements OnInit {
  readonly store = inject(StrategiesStore);
  readonly deletingId = signal<number | null>(null);
  ngOnInit(): void { this.store.list().subscribe(); }

  // P4 WS-C: delete a strategy with no non-cancelled cycles. Confirm first —
  // this also removes the freshly-seeded strategy book.
  confirmDelete(id: number, name: string): void {
    if (this.deletingId() !== null) return;
    const ok = confirm(
      `Delete strategy "${name}"? This also removes its (empty) strategy book. `
      + 'This cannot be undone.',
    );
    if (!ok) return;
    this.deletingId.set(id);
    this.store.deleteStrategy(id).subscribe({
      next: () => { this.deletingId.set(null); this.store.list().subscribe(); },
      error: () => { this.deletingId.set(null); alert('Could not delete this strategy.'); },
    });
  }
}
