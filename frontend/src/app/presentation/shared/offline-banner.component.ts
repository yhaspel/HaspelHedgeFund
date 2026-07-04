import { Component, computed, inject } from '@angular/core';

import { OfflineState } from '../../core/offline/offline-state.service';

/**
 * P4-OFF WS-4.2 — the global offline banner, mounted once in the app shell.
 *
 * Hidden when online. L1 (local stack up, WAN down): runs use the local model,
 * external data is paused. L2 (backend unreachable): read-only, showing
 * last-synced data. Non-dismissable while offline (§8 risk: stale values must
 * never be misread as live market data). role=status + aria-live=polite.
 */
@Component({
  selector: 'hf-offline-banner',
  standalone: true,
  template: `
    @if (state.mode() !== 'online') {
      <div class="offline-banner" [class.l2]="state.mode() === 'offline-l2'"
           role="status" aria-live="polite" data-test="offline-banner" data-testid="offline-banner">
        <span class="dot" aria-hidden="true"></span>
        <span class="msg">{{ message() }}</span>
      </div>
    }
  `,
  styles: [
    `
      .offline-banner {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 16px;
        font-family: var(--font-sans);
        font-size: 13px;
        color: var(--text);
        background: var(--acc-hold-soft);
        border-bottom: 1px solid var(--border);
      }
      .offline-banner.l2 {
        background: var(--acc-danger-soft);
      }
      .dot {
        width: 8px;
        height: 8px;
        border-radius: 999px;
        background: var(--acc-hold-fg);
        flex: none;
      }
      .offline-banner.l2 .dot {
        background: var(--acc-danger-fg);
      }
      .msg {
        line-height: 1.3;
      }
    `,
  ],
})
export class OfflineBannerComponent {
  readonly state = inject(OfflineState);

  readonly message = computed(() => {
    if (this.state.mode() === 'offline-l1') {
      const model = this.state.backendInfo()?.llm?.local_model ?? 'local model';
      return `Offline mode — runs use the local model (${model}); external data paused.`;
    }
    return 'Offline — backend unreachable. Showing last-synced data (read-only).';
  });
}
