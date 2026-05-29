import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import {
  AssetClass,
  ScreenCriterion,
  ScreenerField,
} from '../../core/models/screener.model';
import { PopoverComponent } from '../shared/popover.component';

const GROUP_ORDER = [
  'Descriptive',
  'Liquidity & Volume',
  'Performance',
  'Fundamental',
];

@Component({
  selector: 'hf-screener-filter-editor',
  standalone: true,
  imports: [CommonModule, FormsModule, PopoverComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <div class="card-hd">
        <h2 class="title">Filters</h2>
        <div class="actions">
          <button
            type="button"
            class="btn ghost sm"
            (click)="reset.emit()"
          >Reset</button>
          <button
            type="button"
            class="btn ghost sm"
            [attr.aria-expanded]="!collapsed()"
            (click)="collapsed.set(!collapsed())"
          >
            {{ collapsed() ? 'Show' : 'Hide' }}
          </button>
        </div>
      </div>

      @if (!collapsed()) {
        <div class="card-bd flex flex-col gap-4">
          <div class="field-row">
            <label class="lbl">Asset class</label>
            <div role="radiogroup" aria-label="Asset class" class="seg">
              @for (opt of assetClassOptions; track opt.value) {
                <button
                  type="button"
                  class="seg-btn"
                  role="radio"
                  [attr.aria-checked]="assetClass === opt.value"
                  [class.active]="assetClass === opt.value"
                  (click)="emitAssetClass(opt.value)"
                >{{ opt.label }}</button>
              }
            </div>
          </div>

          @for (group of orderedGroups(); track group) {
            <fieldset class="group">
              <legend class="legend">{{ group }}</legend>
              <div class="group-grid">
                @for (f of fieldsForGroup(group); track f.id) {
                  <div
                    class="field"
                    [class.disabled-field]="!f.available"
                  >
                    <label class="lbl" [for]="'fe-' + f.id">
                      <span class="lbl-text">{{ f.label }}</span>
                      @if (f.description) {
                        <span class="help-wrap">
                          <button
                            type="button"
                            class="help-trigger"
                            [attr.aria-label]="'About ' + f.label"
                            [attr.aria-describedby]="pop.open() ? pop.popoverId : null"
                            (click)="$event.preventDefault(); pop.toggle()"
                            (mouseenter)="pop.show()"
                            (mouseleave)="pop.maybeHide()"
                            (focus)="pop.show()"
                            (blur)="pop.maybeHide()"
                          >?</button>
                          <hf-popover #pop placement="top" align="start">
                            <span class="help-body">{{ f.description }}</span>
                          </hf-popover>
                        </span>
                      }
                      @if (!f.available) {
                        <span
                          class="lock-mark"
                          [attr.title]="lockTooltip(f)"
                          aria-label="Field disabled"
                        >(locked)</span>
                      }
                    </label>

                    @switch (f.kind) {
                      @case ('range') {
                        <div class="range-row">
                          <input
                            class="input mono"
                            type="number"
                            [id]="'fe-' + f.id"
                            [attr.aria-label]="f.label + ' minimum'"
                            placeholder="min"
                            [disabled]="!f.available"
                            [ngModel]="rangeMin(f.id)"
                            (ngModelChange)="setRange(f.id, 'min', $event)"
                          />
                          <span class="sep" aria-hidden="true">–</span>
                          <input
                            class="input mono"
                            type="number"
                            [attr.aria-label]="f.label + ' maximum'"
                            placeholder="max"
                            [disabled]="!f.available"
                            [ngModel]="rangeMax(f.id)"
                            (ngModelChange)="setRange(f.id, 'max', $event)"
                          />
                          @if (f.unit) {
                            <span class="unit">{{ unitLabel(f.unit) }}</span>
                          }
                        </div>
                      }
                      @case ('enum') {
                        <select
                          class="input"
                          [id]="'fe-' + f.id"
                          [disabled]="!f.available"
                          [ngModel]="enumValue(f.id)"
                          (ngModelChange)="setEnum(f.id, $event)"
                        >
                          <option value="">— any —</option>
                          @for (v of f.enum_values; track v) {
                            <option [value]="v">{{ v }}</option>
                          }
                        </select>
                      }
                      @case ('bool') {
                        <div role="radiogroup" [attr.aria-label]="f.label" class="seg">
                          <button
                            type="button"
                            class="seg-btn"
                            role="radio"
                            [attr.aria-checked]="boolValue(f.id) === null"
                            [class.active]="boolValue(f.id) === null"
                            [disabled]="!f.available"
                            (click)="setBool(f.id, null)"
                          >Any</button>
                          <button
                            type="button"
                            class="seg-btn"
                            role="radio"
                            [attr.aria-checked]="boolValue(f.id) === true"
                            [class.active]="boolValue(f.id) === true"
                            [disabled]="!f.available"
                            (click)="setBool(f.id, true)"
                          >Yes</button>
                          <button
                            type="button"
                            class="seg-btn"
                            role="radio"
                            [attr.aria-checked]="boolValue(f.id) === false"
                            [class.active]="boolValue(f.id) === false"
                            [disabled]="!f.available"
                            (click)="setBool(f.id, false)"
                          >No</button>
                        </div>
                      }
                    }
                  </div>
                }
              </div>
            </fieldset>
          }
        </div>
      }
    </section>
  `,
  styles: [
    `
      .field-row {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .group {
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        padding: 12px 14px;
        margin: 0;
      }
      .legend {
        padding: 0 6px;
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-3);
        font-weight: 500;
      }
      .group-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 12px 16px;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .field .lbl {
        font-size: var(--fs-11);
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-3);
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .field.disabled-field .lbl {
        color: var(--text-3);
      }
      .lock-mark {
        font-size: 10px;
        color: var(--text-3);
        font-weight: 500;
        text-transform: none;
        letter-spacing: 0;
        cursor: help;
        position: relative;
      }
      .help-wrap {
        position: relative;
        display: inline-flex;
        align-items: center;
      }
      .help-trigger {
        width: 14px;
        height: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        background: var(--surface-2);
        border: 1px solid var(--border-2);
        color: var(--text-2);
        font-size: 10px;
        font-weight: 700;
        line-height: 1;
        padding: 0;
        cursor: help;
        font-family: inherit;
        text-transform: none;
        letter-spacing: 0;
      }
      .help-trigger:hover,
      .help-trigger:focus-visible {
        background: var(--acc-info);
        border-color: var(--acc-info);
        color: white;
        outline: none;
      }
      .help-body {
        display: block;
        max-width: 260px;
        font-size: 11.5px;
        line-height: 1.4;
        white-space: normal;
        text-transform: none;
        letter-spacing: 0;
        color: var(--text);
        font-weight: 400;
      }
      .range-row {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .range-row .input {
        flex: 1 1 0;
        min-width: 0;
      }
      .range-row .sep {
        color: var(--text-3);
      }
      .unit {
        font-size: var(--fs-11);
        color: var(--text-3);
      }
      .seg {
        display: inline-flex;
        gap: 0;
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        overflow: hidden;
      }
      .seg-btn {
        background: transparent;
        border: 0;
        padding: 6px 12px;
        font-size: 12px;
        color: var(--text-2);
        cursor: pointer;
      }
      .seg-btn.active {
        background: var(--acc-info-soft);
        color: var(--text);
      }
      .seg-btn:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
      .input {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--r-4);
        padding: 6px 8px;
        color: var(--text);
        font-size: var(--fs-12);
      }
      .input:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
    `,
  ],
})
export class FilterEditorComponent {
  @Input() fields: ScreenerField[] = [];
  @Input() criteria: Record<string, ScreenCriterion> = {};
  @Input() assetClass: AssetClass = 'equity';
  @Output() criteriaChange = new EventEmitter<Record<string, ScreenCriterion>>();
  @Output() assetClassChange = new EventEmitter<AssetClass>();
  @Output() reset = new EventEmitter<void>();

  readonly collapsed = signal(false);

  readonly assetClassOptions = [
    { value: 'equity' as const, label: 'Equity' },
    { value: 'etf' as const, label: 'ETF' },
    { value: 'all' as const, label: 'Both' },
  ];

  // Plain method (not `computed`) — @Input is a property, not a signal,
  // so a computed signal would never invalidate on field-load.
  orderedGroups(): string[] {
    const present = new Set<string>(this.fields.map((f) => f.group));
    return GROUP_ORDER.filter((g) => present.has(g));
  }

  fieldsForGroup(group: string): ScreenerField[] {
    return this.fields.filter(
      (f) =>
        f.group === group &&
        f.asset_classes.includes(this.assetClass === 'all' ? 'equity' : this.assetClass),
    );
  }

  rangeMin(id: string): number | null {
    const c = this.criteria[id] as { min?: number; max?: number } | undefined;
    return c?.min ?? null;
  }
  rangeMax(id: string): number | null {
    const c = this.criteria[id] as { min?: number; max?: number } | undefined;
    return c?.max ?? null;
  }
  enumValue(id: string): string {
    const c = this.criteria[id] as { values?: string[] } | undefined;
    return c?.values?.[0] ?? '';
  }
  boolValue(id: string): boolean | null {
    const c = this.criteria[id] as { value?: boolean } | undefined;
    return c?.value === undefined ? null : c.value;
  }

  setRange(id: string, key: 'min' | 'max', raw: number | string | null): void {
    const next = { ...this.criteria };
    const existing = (next[id] as { min?: number; max?: number } | undefined) || {};
    const value =
      raw === null || raw === '' || Number.isNaN(Number(raw))
        ? undefined
        : Number(raw);
    const updated: { min?: number; max?: number } = { ...existing };
    if (value === undefined) {
      delete (updated as any)[key];
    } else {
      updated[key] = value;
    }
    if (updated.min === undefined && updated.max === undefined) {
      delete next[id];
    } else {
      next[id] = updated;
    }
    this.criteriaChange.emit(next);
  }

  setEnum(id: string, value: string): void {
    const next = { ...this.criteria };
    if (!value) {
      delete next[id];
    } else {
      next[id] = { values: [value] };
    }
    this.criteriaChange.emit(next);
  }

  setBool(id: string, value: boolean | null): void {
    const next = { ...this.criteria };
    if (value === null) {
      delete next[id];
    } else {
      next[id] = { value };
    }
    this.criteriaChange.emit(next);
  }

  emitAssetClass(ac: AssetClass): void {
    this.assetClassChange.emit(ac);
  }

  lockTooltip(f: ScreenerField): string {
    const requires = (f.requires && f.requires[0]) || 'data';
    return `Requires a ${requires} data source — not yet available.`;
  }

  unitLabel(unit: string): string {
    if (unit === 'usd') return '$';
    if (unit === 'shares') return 'sh';
    if (unit === 'pct') return '%';
    if (unit === 'ratio') return '×';
    return '';
  }
}
