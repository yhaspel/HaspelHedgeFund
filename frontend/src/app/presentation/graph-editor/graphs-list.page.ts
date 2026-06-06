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
import { AgentGraphSummary } from '../../core/models/graph.model';

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
              <button type="button" class="btn ghost sm" (click)="archive(g)">Archive</button>
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
      h1 { margin: 0; font-size: 20px; } .sub { color: var(--text-dim, #93a0b5); font-size: 13px; margin: 4px 0 0; max-width: 560px; }
      .sec { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim, #93a0b5); margin: 22px 0 10px; }
      .card { border: 1px solid var(--border, #2a3142); border-radius: 10px; background: var(--surface, #151b26); padding: 14px; }
      .create-row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
      .create-row input, .clone-row input { padding: 7px 10px; border-radius: 7px; border: 1px solid var(--border, #2a3142);
              background: var(--surface-2, #0e1117); color: var(--text, #e6ebf5); font-size: 13px; }
      .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
      .g-hd { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
      .g h3 { margin: 0; font-size: 14.5px; color: var(--text, #e6ebf5); }
      .desc { font-size: 12px; color: var(--text-dim, #93a0b5); margin: 6px 0; line-height: 1.4; }
      .meta { font-size: 11px; color: var(--text-dim, #93a0b5); margin: 4px 0 10px; }
      .pill { font-size: 10px; padding: 2px 8px; border-radius: 999px; background: #3a1b1b; color: #f08a8a; }
      .pill.ok { background: #163a2c; color: #5fd6a6; } .pill.tmpl-pill { background: #1d2740; color: #8fb0ff; }
      .g-act, .clone-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
      .btn { padding: 7px 13px; border-radius: 8px; font-size: 12.5px; cursor: pointer; border: 1px solid var(--border, #2a3142); }
      .btn.sm { padding: 5px 10px; font-size: 12px; }
      .btn.ghost { background: var(--surface-2, #0e1117); color: var(--text, #e6ebf5); }
      .btn.primary { background: var(--acc-info); color: #fff; border-color: var(--acc-info); }
      .btn:disabled { opacity: .45; cursor: not-allowed; }
      .muted { color: var(--text-dim, #93a0b5); font-size: 13px; }
      .err { color: #f08a8a; font-size: 12px; }
    `,
  ],
})
export class GraphsListPage implements OnInit {
  readonly store = inject(GraphsStore);
  private readonly router = inject(Router);

  readonly creating = signal(false);
  readonly cloningId = signal<number | null>(null);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  newName = '';
  cloneName = '';

  ngOnInit(): void {
    this.store.loadGraphs().subscribe();
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

  archive(g: AgentGraphSummary): void {
    this.store.archiveGraph(g.id).subscribe();
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
