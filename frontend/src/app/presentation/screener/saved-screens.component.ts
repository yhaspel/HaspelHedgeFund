import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { SavedScreen } from '../../core/models/screener.model';
import { ConfirmService } from '../shared/confirm.service';
import { ModalComponent } from '../shared/modal.component';

@Component({
  selector: 'hf-screener-saved-screens',
  standalone: true,
  imports: [CommonModule, FormsModule, ModalComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="saved-bar">
      <label class="lbl" for="saved-select">Saved screens</label>
      <select
        id="saved-select"
        class="input"
        [ngModel]="selectedId"
        (ngModelChange)="onSelect($event)"
      >
        <option [value]="''">— none —</option>
        @for (s of saved; track s.id) {
          <option [value]="s.id">{{ s.name }}</option>
        }
      </select>
      <button
        type="button"
        class="btn ghost sm save-current-btn"
        (click)="openSave.set(true)"
      >Save current</button>
      @if (selectedId) {
        <button
          type="button"
          class="btn ghost sm danger"
          (click)="deleteCurrent()"
        >Delete</button>
      }
    </div>

    @if (openSave()) {
      <hf-modal [titleId]="'save-screen-title'" (closed)="openSave.set(false)">
        <div class="card save-modal" (click)="$event.stopPropagation()">
          <div class="card-hd">
            <h2 class="title" id="save-screen-title">Save screen</h2>
            <div class="actions">
              <button class="icon-btn" (click)="openSave.set(false)" aria-label="Close">
                <svg width="16" height="16" aria-hidden="true"><use href="/icons.svg#i-x" /></svg>
              </button>
            </div>
          </div>
          <div class="card-bd">
            <div class="field">
              <label class="lbl" for="save-name">Name</label>
              <input
                id="save-name"
                class="input"
                type="text"
                [(ngModel)]="newName"
                placeholder="My screen"
                autocomplete="off"
              />
            </div>
            @if (saveError()) {
              <p class="text-[var(--acc-short-fg)] text-2xs mt-2">{{ saveError() }}</p>
            }
          </div>
          <div class="footer">
            <button class="btn ghost" (click)="openSave.set(false)">Cancel</button>
            <button
              class="btn primary"
              [disabled]="!newName.trim()"
              (click)="onSave()"
            >Save</button>
          </div>
        </div>
      </hf-modal>
    }
  `,
  styles: [
    `
      .saved-bar {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .saved-bar > .lbl {
        white-space: nowrap;
        flex-shrink: 0;
      }
      .saved-bar > .input {
        flex: 1 1 220px;
        min-width: 0;
        max-width: 360px;
      }
      .save-current-btn {
        white-space: nowrap;
        flex-shrink: 0;
        padding: 0 12px;
      }
      .save-modal { min-width: 360px; }
      .save-modal .field {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .footer {
        padding: 10px 14px;
        border-top: 1px solid var(--border);
        display: flex;
        gap: 8px;
        justify-content: flex-end;
      }
      .btn.danger {
        color: var(--acc-short-fg);
      }
    `,
  ],
})
export class SavedScreensComponent {
  @Input() saved: SavedScreen[] = [];
  @Input() selectedId: number | '' = '';
  @Output() loaded = new EventEmitter<SavedScreen>();
  @Output() deleted = new EventEmitter<SavedScreen>();
  @Output() saveRequested = new EventEmitter<{ name: string }>();

  private readonly confirm = inject(ConfirmService);

  readonly openSave = signal(false);
  readonly saveError = signal<string | null>(null);
  newName = '';

  onSelect(id: string | number): void {
    if (id === '' || id === null) return;
    const s = this.saved.find((r) => String(r.id) === String(id));
    if (s) this.loaded.emit(s);
  }

  onSave(): void {
    const name = this.newName.trim();
    if (!name) return;
    this.saveError.set(null);
    this.saveRequested.emit({ name });
    this.newName = '';
    this.openSave.set(false);
  }

  async deleteCurrent(): Promise<void> {
    const s = this.saved.find((r) => String(r.id) === String(this.selectedId));
    if (!s) return;
    const ok = await this.confirm.ask({
      title: `Delete saved screen "${s.name}"?`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (ok) {
      this.deleted.emit(s);
    }
  }
}
