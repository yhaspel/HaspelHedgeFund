import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * The counterpart to <hf-empty-state>.
 *
 * Before this existed, every list and detail page collapsed "the request
 * failed" into "there is nothing here": a 500 on /runs/ rendered "You haven't
 * started any runs yet.", a 503 on /backtests/ rendered "No backtests yet."
 * A user cannot tell a broken backend from an empty account, and there is no
 * way to retry short of a full page reload. This renders the failure honestly
 * and offers the retry.
 */
@Component({
  selector: 'hf-error-state',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="err" [class.compact]="compact" role="alert" data-test="error-state">
      <p class="msg">{{ title }}</p>
      @if (detail) {
        <p class="detail" data-test="error-state-detail">{{ detail }}</p>
      }
      @if (showRetry) {
        <button type="button" class="btn sm" data-test="error-state-retry" (click)="retry.emit()">
          {{ retryLabel }}
        </button>
      }
      <ng-content></ng-content>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
        width: 100%;
      }
      .err {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 12px;
        padding: 28px 12px;
        text-align: center;
        border: 1px solid var(--acc-short);
        border-radius: var(--r-8);
        background: var(--acc-short-soft);
        color: var(--text-2);
      }
      .err.compact {
        padding: 14px 8px;
        gap: 8px;
      }
      .msg {
        margin: 0;
        font-size: 13px;
        font-weight: 600;
        color: var(--acc-short-fg);
      }
      .err.compact .msg {
        font-size: 12px;
      }
      .detail {
        margin: 0;
        font-size: 11.5px;
        color: var(--text-2);
        max-width: 60ch;
        overflow-wrap: anywhere;
      }
    `,
  ],
})
export class ErrorStateComponent {
  /** Headline: what failed, in the user's terms. */
  @Input() title = 'Something went wrong';
  /** The server's own message (`detail` / flattened field errors), when there is one. */
  @Input() detail?: string | null;
  @Input() retryLabel = 'Retry';
  @Input() showRetry = true;
  @Input() compact = false;
  @Output() readonly retry = new EventEmitter<void>();
}
