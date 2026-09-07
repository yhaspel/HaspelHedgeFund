import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { FundStore } from '../../abstraction/fund.store';
import { FundActivityEntry } from '../../core/models/fund-activity.model';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { ErrorStateComponent } from '../shared/error-state.component';

/**
 * WAVE 3 item 3 — the fund's activity feed.
 *
 * One merged, newest-first record of everything the fund did: broker orders
 * through their whole lifecycle, autopilot dispatches, halts / resumes /
 * resets / flattens, guardrail transitions and sleeve reallocations. Before
 * this, an owner watching 18 orders held for Tuesday's open had no single
 * place that said what happened and when.
 *
 * Pagination is a cursor (`next_before`), not a page number, because entries
 * are merged from five sources at read time and offsets would skip rows.
 * `notes` is rendered as a footnote — it is where the backend admits which
 * event kinds are recorded forward-only.
 */
@Component({
  selector: 'hf-fund-activity',
  standalone: true,
  imports: [CommonModule, RouterLink, EmptyStateComponent, ErrorStateComponent],
  template: `
    <section class="card" data-test="fund-activity">
      <div class="card-hd">
        <h2 class="title">Activity</h2>
        <div class="actions">
          <button
            type="button"
            class="btn sm"
            data-test="fund-activity-reload"
            [disabled]="store.activityLoading()"
            (click)="reload()"
          >
            {{ store.activityLoading() ? 'Loading…' : 'Refresh' }}
          </button>
        </div>
      </div>

      <div class="card-bd">
        @if (store.activityError(); as e) {
          <hf-error-state
            [compact]="true"
            title="Couldn’t load the activity feed"
            [detail]="e"
            (retry)="reload()"
          ></hf-error-state>
        } @else if (store.activityLoading() && !entries().length) {
          <div class="flex flex-col gap-2" aria-busy="true" aria-label="Loading activity">
            @for (_ of [1, 2, 3, 4]; track $index) {
              <div class="skel h-[18px] w-full"></div>
            }
          </div>
        } @else if (unavailableReason(); as why) {
          <hf-empty-state
            [compact]="true"
            message="No fund activity"
            [detail]="why"
            data-test="fund-activity-unavailable"
          ></hf-empty-state>
        } @else if (!entries().length) {
          <hf-empty-state
            [compact]="true"
            message="Nothing has happened yet"
            detail="Orders, autopilot dispatches, halts and reallocations land here as they occur."
            data-test="fund-activity-empty"
          ></hf-empty-state>
        } @else {
          <ol class="feed" data-test="fund-activity-list">
            @for (e of entries(); track e.at + '|' + e.kind + '|' + e.title) {
              <li
                class="entry"
                [class.warn]="e.severity === 'warn'"
                [class.error]="e.severity === 'error'"
                [attr.data-test]="'fund-activity-entry-' + e.kind"
              >
                <div class="when mono">{{ e.at | date: 'MMM d, HH:mm' }}</div>
                <div class="body">
                  <div class="hd">
                    <span
                      class="sev"
                      [class.warn]="e.severity === 'warn'"
                      [class.error]="e.severity === 'error'"
                      [attr.data-test]="'fund-activity-severity-' + e.severity"
                      >{{ e.severity }}</span
                    >
                    <span class="kind mono">{{ e.kind }}</span>
                    <span class="ttl">{{ e.title }}</span>
                  </div>
                  @if (e.detail) {
                    <p class="detail">{{ e.detail }}</p>
                  }
                  @if (hasLinks(e)) {
                    <div class="links">
                      @if (e.links.strategy_id) {
                        <a
                          [routerLink]="['/strategies', e.links.strategy_id]"
                          data-test="fund-activity-link-strategy"
                          >Strategy #{{ e.links.strategy_id }}</a
                        >
                      }
                      @if (e.links.run_id) {
                        <a [routerLink]="['/runs', e.links.run_id]" data-test="fund-activity-link-run"
                          >Run #{{ e.links.run_id }}</a
                        >
                      }
                      @if (e.links.order_id) {
                        <a
                          routerLink="/broker-accounts/pending"
                          [queryParams]="{ order: e.links.order_id }"
                          data-test="fund-activity-link-order"
                          >Order #{{ e.links.order_id }}</a
                        >
                      }
                    </div>
                  }
                </div>
              </li>
            }
          </ol>

          @if (store.activityNextBefore()) {
            <button
              type="button"
              class="btn sm load-more"
              data-test="fund-activity-load-more"
              [disabled]="store.activityLoadingMore()"
              (click)="loadMore()"
            >
              {{ store.activityLoadingMore() ? 'Loading…' : 'Load more' }}
            </button>
          } @else {
            <p class="muted" data-test="fund-activity-end">That is the whole record held.</p>
          }
        }

        @if (notes().length) {
          <ul class="notes" data-test="fund-activity-notes">
            @for (n of notes(); track n) {
              <li>{{ n }}</li>
            }
          </ul>
        }
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .card-bd {
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .feed {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
      }
      .entry {
        display: grid;
        grid-template-columns: 110px 1fr;
        gap: 10px;
        padding: 8px 0 8px 8px;
        border-top: 1px solid var(--border);
        border-left: 3px solid transparent;
      }
      .entry:first-child {
        border-top: 0;
      }
      .entry.warn {
        border-left-color: var(--acc-hold);
        background: var(--acc-hold-soft);
      }
      .entry.error {
        border-left-color: var(--acc-short);
        background: var(--acc-short-soft);
      }
      .when {
        font-size: var(--fs-11);
        color: var(--text-3);
        white-space: nowrap;
      }
      .hd {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: baseline;
      }
      .sev {
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-3);
        border: 1px solid var(--border-2);
        border-radius: var(--r-full);
        padding: 0 6px;
      }
      .sev.warn {
        color: var(--acc-hold-fg);
        border-color: var(--acc-hold);
      }
      .sev.error {
        color: var(--acc-short-fg);
        border-color: var(--acc-short);
      }
      .kind {
        font-size: var(--fs-11);
        color: var(--text-3);
      }
      .ttl {
        font-size: var(--fs-13);
        color: var(--text);
        font-weight: 500;
      }
      .detail {
        margin: 2px 0 0;
        font-size: var(--fs-12);
        color: var(--text-2);
      }
      .links {
        display: flex;
        gap: 10px;
        margin-top: 3px;
      }
      .links a {
        font-size: var(--fs-11);
        color: var(--acc-info-fg);
        text-decoration: underline;
      }
      .mono {
        font-family: var(--font-mono);
        letter-spacing: var(--tracking-mono);
      }
      .load-more {
        align-self: flex-start;
      }
      .muted {
        margin: 0;
        font-size: var(--fs-11);
        color: var(--text-3);
      }
      .notes {
        margin: 0;
        padding-left: 16px;
        border-top: 1px solid var(--border);
        padding-top: 8px;
        font-size: var(--fs-11);
        color: var(--text-3);
      }
      @media (max-width: 700px) {
        .entry {
          grid-template-columns: 1fr;
        }
      }
    `,
  ],
})
export class FundActivityComponent implements OnInit {
  readonly store = inject(FundStore);

  readonly entries = this.store.activity;

  readonly notes = computed(() => this.store.activityMeta()?.notes ?? []);

  /** The `{available:false, reason}` body — no fund yet, so no record. */
  readonly unavailableReason = computed(() => {
    const m = this.store.activityMeta();
    if (!m || m.available) return null;
    return m.reason ?? 'This account has no fund yet.';
  });

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.store.loadActivity().subscribe();
  }

  loadMore(): void {
    this.store.loadMoreActivity().subscribe();
  }

  hasLinks(e: FundActivityEntry): boolean {
    const l = e.links ?? {};
    return !!(l.strategy_id || l.run_id || l.order_id);
  }
}
