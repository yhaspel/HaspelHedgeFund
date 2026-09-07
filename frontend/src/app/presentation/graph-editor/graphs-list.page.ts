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
import { AppShellComponent } from '../shared/app-shell.component';
import { GraphsStore } from '../../abstraction/graphs.store';
import { ConfirmService } from '../shared/confirm.service';
import { AgentGraphSummary } from '../../core/models/graph.model';
import { apiErrorMessage } from '../../core/api/api-error';

@Component({
  selector: 'hf-graphs-list',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Graphs' }]">
      <div class="page-head">
        <div>
          <h1>Agent graphs</h1>
          <p class="sub">Compose your own agent council — pick analytical agents and personas, set each one's model, and run it.</p>
        </div>
        <button type="button" class="btn primary" (click)="creating.set(!creating())" data-testid="new-graph-btn">
          + New graph
        </button>
      </div>

      @if (creating()) {
        <form class="create-row card" (ngSubmit)="create()">
          <input type="text" [(ngModel)]="newName" name="newName" placeholder="Graph name"
                 maxlength="200" data-testid="new-graph-name" aria-label="New graph name" />
          <button type="submit" class="btn primary" [disabled]="!newName.trim() || busy()">Create</button>
          <button type="button" class="btn ghost" (click)="creating.set(false)">Cancel</button>
          @if (error()) { <span class="err">{{ error() }}</span> }
        </form>
      }

      <h2 class="sec">Your graphs</h2>
      @if (store.ownGraphs().length === 0) {
        <p class="muted" data-testid="no-own">No graphs yet — create one, or clone a template below.</p>
      }
      <div class="grid">
        @for (g of store.ownGraphs(); track g.id) {
          <article class="card g" [attr.data-testid]="'graph-' + g.id">
            <div class="g-hd">
              <h3>{{ g.name }}</h3>
              @if (g.latest_version) {
                <span class="pill" [class.ok]="g.latest_version.validation_status === 'valid'">
                  {{ g.latest_version.validation_status }}
                </span>
              }
            </div>
            @if (g.description) { <p class="desc">{{ g.description }}</p> }
            <p class="meta">{{ g.version_count }} version{{ g.version_count === 1 ? '' : 's' }}</p>
            <div class="g-act">
              <button type="button" class="btn primary sm" (click)="edit(g)" [attr.data-testid]="'edit-' + g.id">Edit</button>
              <button type="button" class="btn ghost sm" (click)="archive(g)"
                      [attr.data-testid]="'delete-graph-' + g.id">Delete</button>
            </div>
          </article>
        }
      </div>

      <h2 class="sec">Templates</h2>
      <div class="grid">
        @for (t of store.templates(); track t.id) {
          <article class="card g tmpl" [attr.data-testid]="'template-' + t.id">
            <div class="g-hd"><h3>{{ t.name }}</h3><span class="pill tmpl-pill">template</span></div>
            @if (t.description) { <p class="desc">{{ t.description }}</p> }
            @if (cloningId() === t.id) {
              <form class="clone-row" (ngSubmit)="confirmClone(t)">
                <input type="text" [(ngModel)]="cloneName" name="cloneName" placeholder="New graph name"
                       [attr.data-testid]="'clone-name-' + t.id" aria-label="Clone name" />
                <button type="submit" class="btn primary sm" [disabled]="!cloneName.trim() || busy()">Clone</button>
                <button type="button" class="btn ghost sm" (click)="cloningId.set(null)">✕</button>
              </form>
            } @else {
              <div class="g-act">
                <button type="button" class="btn ghost sm" (click)="startClone(t)" [attr.data-testid]="'clone-' + t.id">
                  Clone &amp; edit
                </button>
              </div>
            }
          </article>
        }
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 8px; }
      h1 { margin: 0; font-size: 20px; } .sub { color: var(--text-3); font-size: 13px; margin: 4px 0 0; max-width: 560px; }
      .sec { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-3); margin: 22px 0 10px; }
      .card { border: 1px solid var(--border); border-radius: 10px; background: var(--surface); padding: 14px; }
      .create-row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
      .create-row input, .clone-row input { padding: 7px 10px; border-radius: 7px; border: 1px solid var(--border);
              background: var(--surface-2); color: var(--text); font-size: 13px; }
      .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
      .g-hd { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
      .g h3 { margin: 0; font-size: 14.5px; color: var(--text); }
      .desc { font-size: 12px; color: var(--text-3); margin: 6px 0; line-height: 1.4; }
      .meta { font-size: 11px; color: var(--text-3); margin: 4px 0 10px; }
      .pill { font-size: 10px; padding: 2px 8px; border-radius: 999px; background: var(--acc-short-soft); color: var(--acc-short-fg); }
      .pill.ok { background: var(--acc-long-soft); color: var(--acc-long-fg); }
      .pill.tmpl-pill { background: var(--acc-info-soft); color: var(--acc-info-fg); }
      .g-act, .clone-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
      .btn { padding: 7px 13px; border-radius: 8px; font-size: 12.5px; cursor: pointer; border: 1px solid var(--border); }
      .btn.sm { padding: 5px 10px; font-size: 12px; }
      .btn.ghost { background: var(--surface-2); color: var(--text); }
      .btn.primary { background: var(--acc-long); color: var(--bg); border-color: var(--acc-long); font-weight: 600; }
      .btn:disabled { opacity: .45; cursor: not-allowed; }
      .muted { color: var(--text-3); font-size: 13px; }
      .err { color: var(--acc-short-fg); font-size: 12px; }
    `,
  ],
})
export class GraphsListPage implements OnInit {
  readonly store = inject(GraphsStore);
  private readonly router = inject(Router);
  private readonly confirm = inject(ConfirmService);

  readonly creating = signal(false);
  readonly cloningId = signal<number | null>(null);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  newName = '';
  cloneName = '';

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.error.set(null);
    this.store.loadGraphs().subscribe({
      error: (e: unknown) => this.error.set(apiErrorMessage(e, 'Could not load your graphs.')),
    });
  }

  create(): void {
    const name = this.newName.trim();
    if (!name || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    this.store.createGraph({ name }).subscribe({
      next: (g) => {
        this.busy.set(false);
        this.creating.set(false);
        this.newName = '';
        this.router.navigate(['/graphs', g.id, 'edit']);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.name?.[0] || 'Could not create graph.');
      },
    });
  }

  edit(g: AgentGraphSummary): void {
    this.router.navigate(['/graphs', g.id, 'edit']);
  }

  /**
   * Labelled "Archive", but GraphsStore.archiveGraph is a
   * `DELETE /graphs/<id>/` — the graph and its saved versions go away. That
   * needs a confirmation that says so, and a failure that reaches the user.
   */
  async archive(g: AgentGraphSummary): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Delete the graph "${g.name}"?`,
      body:
        'This removes the graph and all of its saved versions. Runs and backtests already '
        + 'produced with it keep their results, but you will not be able to open or re-run '
        + 'this graph. This cannot be undone.',
      confirmLabel: 'Delete graph',
      danger: true,
    });
    if (!ok) return;
    this.error.set(null);
    this.store.archiveGraph(g.id).subscribe({
      error: (e: unknown) =>
        this.error.set(apiErrorMessage(e, `Could not delete "${g.name}".`)),
    });
  }

  startClone(t: AgentGraphSummary): void {
    this.cloneName = `My ${t.name}`;
    this.cloningId.set(t.id);
  }

  confirmClone(t: AgentGraphSummary): void {
    const name = this.cloneName.trim();
    if (!name || this.busy()) return;
    this.busy.set(true);
    this.store.fromTemplate(t.id, name).subscribe({
      next: (g) => {
        this.busy.set(false);
        this.cloningId.set(null);
        this.router.navigate(['/graphs', g.id, 'edit']);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.name?.[0] || 'Could not clone template.');
      },
    });
  }
}
