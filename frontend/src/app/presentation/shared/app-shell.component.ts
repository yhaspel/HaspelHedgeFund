import { Component, HostListener, Input, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';
import { InvestorProfileStore } from '../../abstraction/investor-profile.store';
import { NewsStore } from '../../abstraction/news.store';
import { MarketNewsItem } from '../../core/models/news.model';
import { CommandPaletteComponent } from './command-palette.component';
import { NewsChyronComponent } from '../news/news-chyron.component';
import { NeedsPromptModalComponent } from '../profile/needs-prompt.modal';
import { PopoverComponent } from './popover.component';

type Theme = 'light' | 'dark';
const THEME_KEY = 'hf.theme';

// P10 §C1/§D3 — the sidebar config. Fund-first ordering: the product (the
// autonomous fund) is the first destination; council-era research surfaces
// live in a "Research" group (collapsed by default — §D3).
export interface NavItem {
  label: string;
  icon: string;
  link: string;
  exact?: boolean;
}
export interface NavGroup {
  label: string;
  items: NavItem[];
  /** P10 §D3: collapsed-by-default group (the council-era research surfaces). */
  collapsible?: boolean;
}

const RESEARCH_COLLAPSED_KEY = 'hf.nav.researchCollapsed';

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Fund',
    items: [
      { label: 'Autonomous Fund', icon: 'i-shield', link: '/', exact: true },
      { label: 'Portfolios', icon: 'i-wallet', link: '/portfolios' },
      { label: 'Broker accounts', icon: 'i-link', link: '/broker-accounts' },
      { label: 'Watchlist', icon: 'i-eye', link: '/watchlist' },
    ],
  },
  {
    label: 'Desk',
    items: [
      { label: 'Strategies', icon: 'i-layers', link: '/strategies' },
      { label: 'Backtests', icon: 'i-beaker', link: '/backtests' },
      { label: 'News', icon: 'i-news', link: '/news' },
    ],
  },
  {
    // P10 §D3: council-era research tooling, collapsed by default — shrinks
    // the perceived surface without deleting capability.
    label: 'Research',
    collapsible: true,
    items: [
      { label: 'Runs', icon: 'i-pulse', link: '/runs' },
      { label: 'Screener', icon: 'i-filter', link: '/screener' },
      { label: 'Agent graphs', icon: 'i-graph', link: '/graphs' },
      { label: 'Leaderboard', icon: 'i-trophy', link: '/leaderboard' },
      { label: 'Schedules', icon: 'i-calendar', link: '/schedules' },
    ],
  },
];

// P10 §D5: Profile folded into Settings → General (no sidebar entry; still
// reachable via the top-bar email link and deep links).
const SYSTEM_ITEMS: NavItem[] = [
  { label: 'Guides', icon: 'i-info', link: '/info' },
  { label: 'Settings', icon: 'i-settings', link: '/settings/data-news' },
];

@Component({
  selector: 'hf-app-shell',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    RouterLinkActive,
    CommandPaletteComponent,
    NewsChyronComponent,
    NeedsPromptModalComponent,
    PopoverComponent,
  ],
  template: `
    <div class="app">
      <a class="skip-link" href="#main-content">Skip to main content</a>
      <!-- P10 §C1/§D3 — fund-first, data-driven sidebar with VISIBLE labels
           (icon-only + hover popovers made 16 unlabeled icons a memory test).
           The fund is the first destination; config lives in NAV_GROUPS. -->
      <nav class="sidebar" aria-label="Primary">
        <div class="logo">
          <img src="/icon.svg" alt="Haspel Hedge Fund" width="30" height="30" />
        </div>
        <!-- Scrollable destination groups — scrolls on short viewports so the
             pinned System group below never clips (HHF-02). -->
        <div class="nav-scroll">
          @for (g of navGroups; track g.label) {
            <div class="nav-group" role="group" [attr.aria-label]="g.label">
              @if (g.collapsible) {
                <button type="button" class="nav-group-toggle"
                        (click)="toggleResearch()"
                        [attr.aria-expanded]="researchOpen()"
                        [attr.aria-label]="(researchOpen() ? 'Collapse ' : 'Expand ') + g.label + ' group'">
                  <span class="nav-group-label">{{ g.label }}</span>
                  <svg width="10" height="10" aria-hidden="true" class="chev"
                       [class.open]="researchOpen()"><use href="/icons.svg#i-chevron-dn" /></svg>
                </button>
              } @else {
                <span class="nav-group-label" aria-hidden="true">{{ g.label }}</span>
              }
              @if (!g.collapsible || researchOpen()) {
                @for (item of g.items; track item.link) {
                  <a class="nav-btn" [routerLink]="item.link"
                     [routerLinkActiveOptions]="{ exact: item.exact ?? false }"
                     routerLinkActive="active" #rla="routerLinkActive"
                     [attr.aria-current]="rla.isActive ? 'page' : null">
                    <svg width="18" height="18" aria-hidden="true"><use [attr.href]="'/icons.svg#' + item.icon" /></svg>
                    <span class="nav-label">{{ item.label }}</span>
                  </a>
                }
              }
            </div>
          }
        </div>
        <!-- Pinned bottom — never scrolls off (HHF-02). -->
        <div class="nav-group nav-system" role="group" aria-label="System">
          <span class="nav-group-label" aria-hidden="true">System</span>
          @for (item of systemItems; track item.link) {
            <a class="nav-btn" [routerLink]="item.link"
               routerLinkActive="active" #rlaSys="routerLinkActive"
               [attr.aria-current]="rlaSys.isActive ? 'page' : null">
              <svg width="18" height="18" aria-hidden="true"><use [attr.href]="'/icons.svg#' + item.icon" /></svg>
              <span class="nav-label">{{ item.label }}</span>
            </a>
          }
        </div>
      </nav>
      <main>
        <div class="topbar">
          <nav class="crumbs" aria-label="Breadcrumb">
            <a routerLink="/">Haspel Hedge Fund</a>
            <span class="sep" aria-hidden="true">/</span>
            <ng-container *ngFor="let c of crumbs; let last = last">
              <a *ngIf="!last && c.link" [routerLink]="c.link">{{ c.label }}</a>
              <span *ngIf="!last && !c.link">{{ c.label }}</span>
              <span class="cur" *ngIf="last" aria-current="page">{{ c.label }}</span>
              <span class="sep" *ngIf="!last" aria-hidden="true">/</span>
            </ng-container>
          </nav>
          <button type="button" class="gsearch" (click)="openPalette()"
                  aria-label="Open command palette ⌘K">
            <svg width="14" height="14" aria-hidden="true">
              <use href="/icons.svg#i-search" />
            </svg>
            <span class="flex-1 text-left">Search runs, strategies, backtests…</span>
            <span class="kbd">{{ paletteShortcutHint() }}</span>
          </button>
          <div class="flex items-center gap-3">
            <button class="icon-btn" (click)="toggleTheme()"
                    [attr.aria-label]="themeToggleLabel()"
                    [attr.aria-pressed]="theme() === 'dark'">
              <svg width="16" height="16" aria-hidden="true">
                <use [attr.href]="theme() === 'dark' ? '/icons.svg#i-sun' : '/icons.svg#i-moon'" />
              </svg>
            </button>
            @if (auth.user(); as u) {
              <a routerLink="/profile"
                 class="email-link mono text-2xs text-text-2"
                 aria-label="Your profile"
                 (mouseenter)="popEmail.show()" (mouseleave)="popEmail.maybeHide()"
                 (focus)="popEmail.show()" (blur)="popEmail.maybeHide()">
                {{ u.email }}
              </a>
              <hf-popover #popEmail placement="bottom" align="end" size="compact">
                @if (profile.hasProfile()) {
                  <strong>{{ profileType() }}</strong>
                  <span class="block text-text-3 text-[11px]">View profile</span>
                } @else {
                  Personalize your analyses — take the 2-minute questionnaire.
                }
              </hf-popover>
            }
            <button *ngIf="auth.user()" class="btn ghost sm" (click)="auth.logout()">Log out</button>
          </div>
        </div>
        @if (showBanner()) {
          <div class="nudge-banner" role="status">
            <span>
              Personalize your analyses — take the 2-minute investor
              questionnaire and the council will calibrate to you.
            </span>
            <div class="actions">
              <a class="btn primary sm" routerLink="/profile/questionnaire">
                Take it
              </a>
              <button type="button"
                      class="btn ghost sm"
                      (click)="dismissNudge()"
                      aria-label="Dismiss reminder for ~30 days">
                Dismiss
              </button>
            </div>
          </div>
        }
        @if (news.chyronEnabled()) {
          <hf-news-chyron
            [items]="news.chyronItems()"
            (open)="onChyronOpen($event)"
            (hide)="onChyronHide()"
          ></hf-news-chyron>
        }
        <div class="page" id="main-content" tabindex="-1">
          <ng-content></ng-content>
        </div>
      </main>
      @if (paletteOpen()) {
        <hf-command-palette (closed)="paletteOpen.set(false)" />
      }
      <hf-needs-prompt-modal
        [open]="showWelcomeModal()"
        (dismissed)="dismissNudge()"
        (take)="dismissNudge()"
      />
    </div>
  `,
  styles: [
    `
      .skip-link {
        position: absolute;
        left: 8px;
        top: -100px;
        z-index: 1000;
        padding: 8px 14px;
        background: var(--surface);
        border: 1px solid var(--border-2);
        border-radius: var(--r-6);
        color: var(--text);
        font-size: 13px;
        text-decoration: none;
        box-shadow: var(--shadow-2);
      }
      .skip-link:focus,
      .skip-link:focus-visible {
        top: 8px;
        outline: none;
        box-shadow: var(--focus-ring);
      }
      .page[tabindex='-1']:focus {
        outline: none;
      }
      .email-link {
        text-decoration: none;
        cursor: pointer;
        position: relative;
      }
      .email-link:hover { color: var(--text); text-decoration: underline; }
      .nudge-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 8px 16px;
        background: var(--acc-info-soft);
        color: var(--text);
        border-bottom: 1px solid var(--border);
        font-size: 12.5px;
      }
      .nudge-banner .actions { display: flex; gap: 8px; }
    `,
  ],
})
export class AppShellComponent implements OnInit {
  @Input() crumbs: { label: string; link?: string }[] = [];
  readonly navGroups = NAV_GROUPS;
  readonly systemItems = SYSTEM_ITEMS;

  // P10 §D3: the Research group is collapsed by default; the choice persists.
  // Auto-expands when the current route lives inside it (active page must
  // never be hidden).
  readonly researchOpen = signal(this.initialResearchOpen());

  toggleResearch(): void {
    const next = !this.researchOpen();
    this.researchOpen.set(next);
    try {
      localStorage.setItem(RESEARCH_COLLAPSED_KEY, next ? '0' : '1');
    } catch {
      // localStorage may be unavailable — degrade silently.
    }
  }

  private initialResearchOpen(): boolean {
    const research = NAV_GROUPS.find((g) => g.collapsible);
    const url = typeof location !== 'undefined' ? location.pathname : '';
    if (research?.items.some((i) => url.startsWith(i.link))) return true;
    try {
      return localStorage.getItem(RESEARCH_COLLAPSED_KEY) === '0';
    } catch {
      return false;
    }
  }

  readonly auth = inject(AuthStore);
  readonly news = inject(NewsStore);
  readonly profile = inject(InvestorProfileStore);
  private readonly router = inject(Router);

  readonly theme = signal<Theme>('dark');
  readonly themeToggleLabel = computed(() =>
    this.theme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
  );

  // ADR 0004: global command palette state.
  readonly paletteOpen = signal(false);
  readonly paletteShortcutHint = computed(() => this.isMac() ? '⌘K' : 'Ctrl K');

  readonly showWelcomeModal = computed(
    () => this.profile.nudge().form === 'modal' && this.profile.nudge().due,
  );
  readonly showBanner = computed(
    () => this.profile.nudge().form === 'banner' && this.profile.nudge().due,
  );
  readonly profileType = computed(
    () => this.profile.active()?.analysis?.investor_type ?? '',
  );

  openPalette(): void {
    this.paletteOpen.set(true);
  }

  @HostListener('window:keydown', ['$event'])
  onWindowKeydown(ev: KeyboardEvent): void {
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      this.paletteOpen.set(true);
    }
  }

  private isMac(): boolean {
    if (typeof navigator === 'undefined') return false;
    return /Mac|iPod|iPhone|iPad/.test(navigator.platform);
  }

  ngOnInit(): void {
    // Read initial theme — was applied pre-paint by main.ts; keep signal in sync.
    const stored = (typeof localStorage !== 'undefined' ? localStorage.getItem(THEME_KEY) : null) as Theme | null;
    const current = (document.documentElement.dataset['theme'] as Theme | undefined) ?? stored ?? 'dark';
    this.theme.set(current === 'light' ? 'light' : 'dark');
    document.documentElement.dataset['theme'] = this.theme();

    // Load news prefs + first page of feed so the global chyron is populated
    // across the app. Both are TTL-cached + in-flight-deduped, so re-mounting
    // the shell on route change does not re-fetch.
    this.news.loadPreferences().subscribe({
      next: (r) => {
        if (r.preferences?.chyron_enabled) {
          this.news.loadFeed().subscribe();
        }
      },
      error: () => { /* not fatal — chyron just stays hidden */ },
    });

    // Load profile bundle so the welcome modal / banner / hover popover have
    // data. TTL-cached + in-flight-deduped so re-mounting doesn't re-fetch.
    this.profile.load().subscribe({ error: () => undefined });
  }

  /** Click a chyron headline → deep-link to /news with the article modal open. */
  onChyronOpen(item: MarketNewsItem): void {
    this.router.navigate(['/news'], { queryParams: { article: item.id } });
  }

  // P4 WS-DX-2: persist the per-user "hide ticker" preference. savePreferences
  // optimistically flips chyron_enabled so the bar disappears immediately, and
  // the setting sticks across sessions (re-enable via Settings → News).
  onChyronHide(): void {
    this.news.savePreferences({ chyron_enabled: false }).subscribe();
  }

  dismissNudge(): void {
    this.profile.dismissNudge().subscribe();
  }

  toggleTheme(): void {
    const next: Theme = this.theme() === 'light' ? 'dark' : 'light';
    this.theme.set(next);
    document.documentElement.dataset['theme'] = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // localStorage may be unavailable (private mode, SSR, etc.) — degrade silently.
    }
  }
}
