import { CommonModule } from '@angular/common';
import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { FundStore } from '../../abstraction/fund.store';
import {
  FundBlockingMember,
  FundCandidate,
  FundMembersChange,
  FundOverview,
} from '../../core/models/autopilot.model';
import { ConfirmService } from '../shared/confirm.service';

interface Row {
  candidate: FundCandidate;
  selected: boolean;
  pct: number | null;
}

// P14 — the roster editor: pick which strategies run as the fund and how the
// shared account's pool is split between them (equal by default, editable,
// must total 100%). Saving replaces the roster (PUT /fund/members/).
@Component({
  selector: 'hf-fund-members-editor',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <section class="card editor">
      <div class="head">
        <h2>Strategies in the fund</h2>
        <div class="head-actions">
          <button type="button" class="btn sm" (click)="equalSplit()" [disabled]="!selectedCount()">
            Equal split
          </button>
          <a class="btn sm" routerLink="/strategies/new">New strategy →</a>
        </div>
      </div>
      <p class="muted lead">
        Tick the strategies that should trade, then give each a share of the pool. Shares apply to
        the account's cash when you <b>Reset</b> the fund (or when a strategy joins, from any
        unallocated cash); after that each slice floats with its own P&amp;L.
      </p>

      @if (!rows().length) {
        <p class="muted">
          You have no strategies yet — <a routerLink="/strategies/new" class="link">create one</a>
          to add it to the fund.
        </p>
      }
      @if (rows().length) {
        <div class="tbl-scroll">
          <table class="tbl roster">
            <thead>
              <tr>
                <th scope="col" class="chk"><span class="sr-only">In fund</span></th>
                <th scope="col">Strategy</th>
                <th scope="col">Kind</th>
                <th scope="col">Validation</th>
                <th scope="col" class="right">Share of pool</th>
              </tr>
            </thead>
            <tbody>
              @for (r of rows(); track r.candidate.id) {
                <tr [class.sel]="r.selected" [class.archived]="!r.candidate.is_active">
                  <td class="chk">
                    <input
                      type="checkbox"
                      [checked]="r.selected"
                      (change)="toggle(r, $event)"
                      [attr.aria-label]="'Include ' + r.candidate.name"
                    />
                  </td>
                  <td>
                    <a [routerLink]="['/strategies', r.candidate.id]" class="name">{{
                      r.candidate.name
                    }}</a>
                    @if (!r.candidate.is_active) {
                      <span class="pill">archived</span>
                    }
                  </td>
                  <td class="muted">{{ r.candidate.kind_display }}</td>
                  <td>
                    @if (r.candidate.validation_passed) {
                      <span class="pill ok"><span class="dot"></span>validated</span>
                    } @else {
                      <span class="pill warn" title="Run a validation backtest to unlock enabling"
                        ><span class="dot"></span>needs backtest</span
                      >
                    }
                  </td>
                  <td class="num pct">
                    @if (r.selected) {
                      <span class="pct-wrap">
                        <input
                          class="input"
                          type="number"
                          min="0"
                          max="100"
                          step="0.01"
                          [ngModel]="r.pct"
                          (ngModelChange)="setPct(r, $event)"
                          [name]="'pct-' + r.candidate.id"
                          [attr.aria-label]="'Share of pool for ' + r.candidate.name"
                        /><span class="suffix">%</span>
                      </span>
                    } @else {
                      <span class="muted">—</span>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <div class="foot">
        <div class="total" [class.bad]="selectedCount() && !totalOk()" data-testid="alloc-total">
          @if (selectedCount()) {
            {{ selectedCount() }} selected · total <b>{{ total() | number: '1.0-2' }}%</b>
            @if (!totalOk()) {
              <span class="bad-note">— must total 100%</span>
            }
          } @else {
            No strategies selected — the fund will have no members.
          }
        </div>
        <div class="foot-actions">
          @if (dirty()) {
            <button type="button" class="btn sm" (click)="reset()" [disabled]="busy()">
              Discard
            </button>
          }
          <button
            type="button"
            class="btn primary"
            (click)="save()"
            [disabled]="busy() || !dirty() || (selectedCount() > 0 && !totalOk())"
          >
            {{ busy() ? 'Saving…' : 'Save strategies' }}
          </button>
        </div>
      </div>

      @if (blocking().length) {
        <div class="block" role="alert">
          <b>Still holding positions:</b>
          @for (b of blocking(); track b.strategy_id) {
            <span class="blk">{{ b.name }} ({{ b.positions.join(', ') }})</span>
          }
          <p class="muted">
            A strategy can only leave the fund once its slice is flat. Queue its closing orders and
            remove it — fills keep attributing to it until it's flat.
          </p>
          <button
            type="button"
            class="btn sm danger"
            (click)="saveWithFlatten()"
            [disabled]="busy()"
          >
            Flatten &amp; remove
          </button>
        </div>
      }
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      @if (warnings().length) {
        <ul class="warnings" data-testid="members-warnings">
          @for (w of warnings(); track w) {
            <li>{{ w }}</li>
          }
        </ul>
      }
      @if (savedNote()) {
        <p class="ok" data-testid="members-saved">{{ savedNote() }}</p>
      }
    </section>
  `,
  styles: [
    `
      .editor {
        padding: 16px;
        margin: 12px 0;
      }
      .head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        margin-bottom: 6px;
      }
      .head h2 {
        font-size: var(--fs-11);
        line-height: 16px;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--text-3);
        font-weight: 500;
        margin: 0;
      }
      .head-actions {
        display: flex;
        gap: 8px;
      }
      a.btn {
        text-decoration: none;
      }
      .lead {
        font-size: 12.5px;
        margin: 0 0 12px;
        max-width: 78ch;
      }
      .muted {
        color: var(--text-3);
      }
      .link {
        text-decoration: underline;
        color: var(--acc-info-fg);
      }
      table.roster {
        width: 100%;
      }
      .roster .chk {
        width: 32px;
      }
      .roster tr.archived .name {
        color: var(--text-3);
      }
      .roster .name {
        font-weight: 600;
      }
      .roster .pill {
        margin-left: 6px;
      }
      .pct-wrap {
        position: relative;
        display: inline-block;
        width: 110px;
      }
      .pct-wrap .input {
        text-align: right;
        padding-right: 24px;
      }
      .pct-wrap .suffix {
        position: absolute;
        right: 8px;
        top: 50%;
        transform: translateY(-50%);
        color: var(--text-3);
        font-size: 12px;
        pointer-events: none;
      }
      .foot {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        margin-top: 12px;
        flex-wrap: wrap;
      }
      .foot-actions {
        display: flex;
        gap: 8px;
      }
      .total {
        font-size: 12.5px;
        color: var(--text-2);
      }
      .total.bad,
      .bad-note {
        color: var(--acc-short-fg);
      }
      .block {
        margin-top: 12px;
        padding: 10px 12px;
        border: 1px solid var(--acc-short);
        border-radius: 6px;
        font-size: 12.5px;
        background: color-mix(in srgb, var(--acc-short-fg) 8%, transparent);
      }
      .block .blk {
        display: inline-block;
        margin: 0 6px;
      }
      .block p {
        margin: 6px 0 8px;
      }
      .error {
        color: var(--acc-short-fg);
        font-size: 12.5px;
        margin: 10px 0 0;
      }
      .warnings {
        margin: 10px 0 0;
        padding-left: 18px;
        font-size: 12.5px;
        color: var(--acc-short-fg);
      }
      .ok {
        color: var(--acc-long-fg);
        font-size: 12.5px;
        margin: 10px 0 0;
      }
      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
      }
    `,
  ],
})
export class FundMembersEditorComponent {
  private readonly store = inject(FundStore);
  private readonly confirm = inject(ConfirmService);

  readonly fund = input<FundOverview | null>(null);
  readonly saved$ = output<FundOverview>();

  readonly rows = signal<Row[]>([]);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly warnings = signal<string[]>([]);
  readonly blocking = signal<FundBlockingMember[]>([]);
  readonly savedNote = signal<string | null>(null);

  readonly selectedCount = computed(() => this.rows().filter((r) => r.selected).length);
  readonly total = computed(() =>
    this.rows()
      .filter((r) => r.selected)
      .reduce((acc, r) => acc + (r.pct ?? 0), 0),
  );
  readonly totalOk = computed(() => Math.abs(this.total() - 100) <= 0.05);
  readonly dirty = computed(() => {
    const f = this.fund();
    const current = new Map((f?.members ?? []).map((m) => [m.strategy_id, +m.allocation_pct]));
    const rows = this.rows();
    const selected = rows.filter((r) => r.selected);
    if (selected.length !== current.size) return true;
    return selected.some(
      (r) =>
        !current.has(r.candidate.id) ||
        Math.abs((r.pct ?? 0) - current.get(r.candidate.id)!) > 0.004,
    );
  });

  constructor() {
    // Rebuild the rows whenever the candidates or the fund's roster change
    // server-side (initial load, a save, a reset).
    effect(() => {
      const cands = this.store.candidates();
      const f = this.fund();
      this.rows.set(this.buildRows(cands, f));
    });
    this.store.loadCandidates().subscribe({ error: () => {} });
  }

  private buildRows(cands: FundCandidate[], f: FundOverview | null): Row[] {
    const current = new Map((f?.members ?? []).map((m) => [m.strategy_id, +m.allocation_pct]));
    return (
      cands
        // Archived strategies that aren't members stay out of the picker.
        .filter((c) => c.is_active || current.has(c.id))
        .map((c) => ({
          candidate: c,
          selected: current.has(c.id),
          pct: current.has(c.id) ? current.get(c.id)! : null,
        }))
    );
  }

  toggle(row: Row, ev: Event): void {
    const on = (ev.target as HTMLInputElement).checked;
    this.rows.update((rows) =>
      rows.map((r) => (r === row ? { ...r, selected: on, pct: on ? r.pct ?? 0 : null } : r)),
    );
    if (on) this.joinAtEqualShare(row);
    this.clearNotes();
  }

  /** A freshly ticked strategy joins at 1/n of the pool; the others keep their
   *  relative proportions and shrink to make room (equal split if they had none). */
  private joinAtEqualShare(row: Row): void {
    const selected = this.rows().filter((r) => r.selected);
    const n = selected.length;
    if (n <= 1) {
      this.equalSplit();
      return;
    }
    const share = Math.floor((100 / n) * 100) / 100;
    const others = selected.filter((r) => r !== row && r.candidate.id !== row.candidate.id);
    const oldTotal = others.reduce((acc, r) => acc + (r.pct ?? 0), 0);
    if (oldTotal <= 0) {
      this.equalSplit();
      return;
    }
    const scale = (100 - share) / oldTotal;
    let running = share;
    const ids = others.map((r) => r.candidate.id);
    this.rows.update((rows) =>
      rows.map((r) => {
        if (!r.selected) return r;
        if (r.candidate.id === row.candidate.id) return { ...r, pct: share };
        const isLast = ids[ids.length - 1] === r.candidate.id;
        const pct = isLast
          ? +(100 - running).toFixed(2)
          : Math.round((r.pct ?? 0) * scale * 100) / 100;
        running += pct;
        return { ...r, pct };
      }),
    );
  }

  setPct(row: Row, value: number | string | null): void {
    const n = value === null || value === '' ? null : Math.max(0, Math.min(100, +value));
    this.rows.update((rows) => rows.map((r) => (r === row ? { ...r, pct: n } : r)));
    this.clearNotes();
  }

  /** n equal whole-percent shares that sum to exactly 100 (last takes the residual). */
  equalSplit(): void {
    const n = this.selectedCount();
    if (!n) return;
    const base = Math.floor((100 / n) * 100) / 100;
    const last = +(100 - base * (n - 1)).toFixed(2);
    let i = 0;
    this.rows.update((rows) =>
      rows.map((r) => {
        if (!r.selected) return r;
        i += 1;
        return { ...r, pct: i === n ? last : base };
      }),
    );
    this.clearNotes();
  }

  reset(): void {
    this.rows.set(this.buildRows(this.store.candidates(), this.fund()));
    this.clearNotes();
  }

  private clearNotes(): void {
    this.error.set(null);
    this.warnings.set([]);
    this.blocking.set([]);
    this.savedNote.set(null);
  }

  private members() {
    return this.rows()
      .filter((r) => r.selected)
      .map((r) => ({ strategy_id: r.candidate.id, allocation_pct: +(r.pct ?? 0).toFixed(2) }));
  }

  async save(force = false): Promise<void> {
    const removed = (this.fund()?.members ?? []).filter(
      (m) => !this.rows().some((r) => r.selected && r.candidate.id === m.strategy_id),
    );
    if (removed.length && !force) {
      const ok = await this.confirm.ask({
        title: `Remove ${removed.length} strateg${
          removed.length === 1 ? 'y' : 'ies'
        } from the fund?`,
        body:
          removed.map((m) => m.name).join(', ') +
          ' — its autopilot is disabled and its slice hands its cash back to the pool. ' +
          'A slice that still holds positions has to be flattened first.',
        confirmLabel: 'Remove',
        danger: true,
      });
      if (!ok) return;
    }
    this.clearNotes();
    this.busy.set(true);
    this.store.setMembers(this.members(), force).subscribe({
      next: (f) => {
        this.busy.set(false);
        this.describe(f.changes);
        this.saved$.emit(f);
      },
      error: (e) => {
        this.busy.set(false);
        const body = e?.error ?? {};
        if (e?.status === 409 && Array.isArray(body.blocking)) {
          this.blocking.set(body.blocking);
        }
        this.error.set(body.detail ?? 'Could not save the roster.');
      },
    });
  }

  async saveWithFlatten(): Promise<void> {
    const names = this.blocking()
      .map((b) => b.name)
      .join(', ');
    const ok = await this.confirm.ask({
      title: 'Flatten and remove?',
      body:
        `Closing orders are queued for ${names} (held for the next open if the market is closed). ` +
        'Their autopilots stop now; they leave the roster as their slices flatten.',
      confirmLabel: 'Flatten & remove',
      danger: true,
    });
    if (!ok) return;
    await this.save(true);
  }

  private describe(c: FundMembersChange | undefined): void {
    if (!c) {
      this.savedNote.set('Saved.');
      return;
    }
    const bits: string[] = [];
    if (c.added.length) bits.push(`${c.added.length} added`);
    if (c.removed.length) bits.push(`${c.removed.length} removed`);
    if (c.updated.length)
      bits.push(`${c.updated.length} share${c.updated.length === 1 ? '' : 's'} changed`);
    if (c.flattening.length) bits.push(`${c.flattening.length} flattening`);
    this.savedNote.set(bits.length ? `Saved — ${bits.join(', ')}.` : 'Saved — no changes.');
    this.warnings.set(c.warnings ?? []);
  }
}
