import {
  Component,
  ContentChild,
  TemplateRef,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';
import { TokenStorage } from '../../core/auth/token-storage';
import { AuthStore } from '../../abstraction/auth.store';
import { AppShellComponent } from './app-shell.component';

/**
 * Auth-adaptive page chrome for `/info` pages.
 *
 * Consumers pass content via a content child `<ng-template #body>`:
 *
 *   <hf-guide-shell [crumbs]="[...]">
 *     <ng-template #body>
 *       ...page content...
 *     </ng-template>
 *   </hf-guide-shell>
 *
 * Logged in (access token present) → wraps the body in the standard
 * `hf-app-shell` (sidebar, topbar, breadcrumbs).
 * Logged out → renders a minimal public header (logo, breadcrumb, theme
 * toggle, Log in / Sign up).
 *
 * The branch is on token presence (not `AuthStore.user()`), so a logged-in
 * hard refresh does not flash the public header while `refreshMe()` resolves.
 */
@Component({
  selector: 'hf-guide-shell',
  standalone: true,
  imports: [RouterLink, AppShellComponent, NgTemplateOutlet],
  template: `
    @if (isLoggedIn()) {
      <hf-app-shell [crumbs]="crumbs()">
        <ng-container *ngTemplateOutlet="body || null"></ng-container>
      </hf-app-shell>
    } @else {
      <div class="guide-public">
        <header class="public-topbar">
          <a class="public-logo" routerLink="/info" aria-label="Haspel Hedge Fund">
            <img src="/icon.svg" alt="" width="22" height="22" />
            <span>Haspel</span>
          </a>
          <nav class="crumbs" aria-label="Breadcrumb">
            @for (c of crumbs(); track c.label; let last = $last; let first = $first) {
              @if (!first) {
                <span class="sep">/</span>
              }
              @if (!last && c.link) {
                <a [routerLink]="c.link">{{ c.label }}</a>
              } @else {
                <span class="cur">{{ c.label }}</span>
              }
            }
          </nav>
          <div class="public-actions">
            <button class="icon-btn" (click)="toggleTheme()"
                    [attr.aria-label]="themeToggleLabel()"
                    [attr.aria-pressed]="theme() === 'dark'">
              <svg width="16" height="16" aria-hidden="true">
                <use [attr.href]="theme() === 'dark' ? '/icons.svg#i-sun' : '/icons.svg#i-moon'" />
              </svg>
            </button>
            <a class="btn ghost sm" routerLink="/login">Log in</a>
            <a class="btn primary sm" routerLink="/signup">Sign up</a>
          </div>
        </header>
        <div class="page">
          <ng-container *ngTemplateOutlet="body || null"></ng-container>
        </div>
      </div>
    }
  `,
  styles: [`
    .guide-public {
      min-height: 100vh;
      background: var(--bg);
    }
    .public-topbar {
      position: sticky;
      top: 0;
      height: 48px;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 0 28px;
      z-index: var(--z-nav);
    }
    .public-logo {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--text);
      font-weight: 600;
      font-size: var(--fs-14);
    }
    .public-logo img {
      border-radius: var(--r-4);
      border: 1px solid var(--border-2);
    }
    .public-actions {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 10px;
    }
  `],
})
export class GuideShellComponent {
  crumbs = input<{ label: string; link?: string }[]>([]);

  @ContentChild('body', { static: true })
  body: TemplateRef<unknown> | null = null;

  private readonly tokens = inject(TokenStorage);
  readonly auth = inject(AuthStore);

  readonly isLoggedIn = computed(() => {
    // Track the user signal so the layout can re-evaluate after login.
    void this.auth.user();
    return !!this.tokens.getAccess();
  });

  // P4 WS-DA-7: track theme reactively so the toggle exposes aria-pressed +
  // a state-appropriate name (matching the main app-shell toggle).
  readonly theme = signal<'light' | 'dark'>(
    (document.documentElement.dataset['theme'] as 'light' | 'dark') || 'dark',
  );
  readonly themeToggleLabel = computed(() =>
    this.theme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
  );

  toggleTheme() {
    const root = document.documentElement;
    const next = root.dataset['theme'] === 'light' ? 'dark' : 'light';
    root.dataset['theme'] = next;
    this.theme.set(next);
  }
}
