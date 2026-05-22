import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Output,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ModalComponent } from './modal.component';
import { CommandPaletteService, PaletteResult } from '../../abstraction/command-palette.service';

/**
 * ADR 0004 — global ⌘K command palette.
 *
 * Rendered conditionally by AppShell; closes via Esc / overlay click / Enter
 * on a result / external trigger. Substring matching against runs +
 * strategies + backtests.
 */
@Component({
  selector: 'hf-command-palette',
  standalone: true,
  imports: [CommonModule, FormsModule, ModalComponent],
  template: `
    <hf-modal (closed)="close()">
      <div class="hf-palette">
        <div class="hf-palette-hd">
          <svg width="14" height="14" aria-hidden="true">
            <use href="/icons.svg#i-search" />
          </svg>
          <input
            #input
            type="text"
            class="hf-palette-input"
            placeholder="Search runs, strategies, backtests…"
            aria-label="Search runs, strategies, backtests"
            aria-controls="hf-palette-results"
            [attr.aria-activedescendant]="activeId()"
            [(ngModel)]="queryModel"
            (ngModelChange)="onQueryChange($event)"
            (keydown)="onInputKeydown($event)" />
          <span class="kbd">esc</span>
        </div>
        <div
          id="hf-palette-results"
          role="listbox"
          aria-label="Search results"
          class="hf-palette-results">
          @if (palette.loading()) {
            <div class="hf-palette-msg">Loading…</div>
          } @else if (!palette.query().trim()) {
            <div class="hf-palette-msg">
              Type to search runs, strategies, or backtests.
            </div>
          } @else if (!results().length) {
            <div class="hf-palette-msg">No results for "{{ palette.query() }}".</div>
          } @else {
            @for (r of results(); track $index; let i = $index) {
              <button
                type="button"
                role="option"
                class="hf-palette-row"
                [class.active]="i === activeIndex()"
                [id]="'hf-palette-row-' + i"
                [attr.aria-selected]="i === activeIndex()"
                (mouseenter)="activeIndex.set(i)"
                (click)="follow(r)">
                <span class="hf-palette-kind">{{ r.source }}</span>
                <span class="hf-palette-label">{{ r.label }}</span>
                <span class="hf-palette-sub">{{ r.sublabel }}</span>
              </button>
            }
          }
        </div>
      </div>
    </hf-modal>
  `,
  styles: [
    `
      .hf-palette {
        width: min(640px, 90vw);
        background: var(--surface);
        border: 1px solid var(--border-2);
        border-radius: var(--r-12);
        box-shadow: var(--shadow-3);
        overflow: hidden;
        display: flex;
        flex-direction: column;
      }
      .hf-palette-hd {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 14px;
        border-bottom: 1px solid var(--border);
        color: var(--text-3);
      }
      .hf-palette-input {
        flex: 1;
        background: transparent;
        border: 0;
        outline: 0;
        color: var(--text);
        font-size: var(--fs-14);
      }
      .hf-palette-input::placeholder {
        color: var(--text-3);
      }
      .hf-palette-results {
        max-height: 60vh;
        overflow-y: auto;
        padding: 4px;
      }
      .hf-palette-msg {
        padding: 16px;
        color: var(--text-3);
        font-size: var(--fs-13);
        text-align: center;
      }
      .hf-palette-row {
        width: 100%;
        display: grid;
        grid-template-columns: 68px 1fr auto;
        align-items: center;
        gap: 12px;
        padding: 10px 12px;
        background: transparent;
        border: 0;
        border-radius: var(--r-6);
        text-align: left;
        color: var(--text);
        font-size: var(--fs-13);
        cursor: pointer;
      }
      .hf-palette-row:hover,
      .hf-palette-row.active,
      .hf-palette-row:focus-visible {
        background: var(--surface-2);
        outline: none;
      }
      .hf-palette-kind {
        font-family: var(--font-mono);
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: var(--tracking-mono);
        color: var(--text-3);
      }
      .hf-palette-label {
        color: var(--text);
      }
      .hf-palette-sub {
        font-family: var(--font-mono);
        font-size: var(--fs-11);
        color: var(--text-3);
      }
    `,
  ],
})
export class CommandPaletteComponent implements AfterViewInit {
  readonly palette = inject(CommandPaletteService);
  private readonly router = inject(Router);

  @Output() closed = new EventEmitter<void>();
  @ViewChild('input', { static: true }) inputRef!: ElementRef<HTMLInputElement>;

  queryModel = '';
  readonly activeIndex = signal(0);
  readonly results = this.palette.results;
  readonly activeId = computed(() => {
    if (!this.results().length) return null;
    return `hf-palette-row-${this.activeIndex()}`;
  });

  ngAfterViewInit(): void {
    this.palette.ensureLoaded();
    this.queryModel = this.palette.query();
    queueMicrotask(() => this.inputRef.nativeElement.focus());
  }

  onQueryChange(v: string): void {
    this.palette.query.set(v);
    this.activeIndex.set(0);
  }

  onInputKeydown(ev: KeyboardEvent): void {
    const n = this.results().length;
    if (ev.key === 'ArrowDown' && n) {
      ev.preventDefault();
      this.activeIndex.update((i) => (i + 1) % n);
      this.scrollActiveIntoView();
    } else if (ev.key === 'ArrowUp' && n) {
      ev.preventDefault();
      this.activeIndex.update((i) => (i - 1 + n) % n);
      this.scrollActiveIntoView();
    } else if (ev.key === 'Enter' && n) {
      ev.preventDefault();
      this.follow(this.results()[this.activeIndex()]);
    }
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    this.close();
  }

  follow(r: PaletteResult): void {
    void this.router.navigate(r.route);
    this.close();
  }

  close(): void {
    this.palette.reset();
    this.queryModel = '';
    this.closed.emit();
  }

  private scrollActiveIntoView(): void {
    queueMicrotask(() => {
      const el = document.getElementById(`hf-palette-row-${this.activeIndex()}`);
      el?.scrollIntoView({ block: 'nearest' });
    });
  }
}
