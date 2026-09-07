import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

/**
 * WAVE 3 item 9 — `/backtests/:id/compare` is now a THIN WRAPPER.
 *
 * The comparison UI moved into the backtest detail page as a side-by-side
 * panel (`<hf-backtest-compare-panel>`) so it renders inside the app shell,
 * keeps the breadcrumb and nav, and does not make you leave the backtest you
 * were reading. The old route stays registered — bookmarks, the run-history
 * links and anything a user pasted into a doc must not 404 — and redirects
 * onto the detail page with the panel already open.
 *
 * `?b=<id>` (or `?compare=<id>`) on the old url is carried through as the
 * pre-selected B side, so a link that named a specific pair still lands on
 * exactly that comparison.
 */
@Component({
  selector: 'hf-backtests-compare',
  standalone: true,
  template: `
    <p class="redirect" data-test="compare-redirect" role="status">
      Opening the comparison on the backtest page…
    </p>
  `,
  styles: [
    `
      .redirect {
        padding: 24px;
        font-size: var(--fs-12);
        color: var(--text-3);
      }
    `,
  ],
})
export class BacktestsComparePage implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    const q = this.route.snapshot.queryParamMap;
    const raw = q.get('b') ?? q.get('compare');
    const b = raw !== null ? Number(raw) : NaN;
    // `compare=1` opens the panel with no B chosen; a real id pre-selects it.
    const compare = Number.isFinite(b) && b > 1 ? String(b) : '1';
    if (!Number.isFinite(id) || id <= 0) {
      void this.router.navigate(['/backtests'], { replaceUrl: true });
      return;
    }
    void this.router.navigate(['/backtests', id], {
      queryParams: { compare },
      replaceUrl: true,
    });
  }
}
