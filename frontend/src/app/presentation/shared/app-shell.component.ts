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
      <nav class="sidebar" aria-label="Primary">
        <div class="logo">
          <img src="/icon.svg" alt="Haspel Hedge Fund" width="30" height="30" />
        </div>
        <a class="nav-btn" routerLink="/" [routerLinkActiveOptions]="{exact:true}"
           routerLinkActive="active" #navHome="routerLinkActive"
           [attr.aria-current]="navHome.isActive ? 'page' : null"
           aria-label="Dashboard"
           (mouseenter)="popHome.show()" (mouseleave)="popHome.maybeHide()"
           (focus)="popHome.show()" (blur)="popHome.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-home" /></svg>
          <hf-popover #popHome placement="right" align="center" size="compact" role="tooltip">Dashboard</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/portfolio" routerLinkActive="active" #navPort="routerLinkActive"
           [attr.aria-current]="navPort.isActive ? 'page' : null"
           aria-label="Portfolio"
           (mouseenter)="popPort.show()" (mouseleave)="popPort.maybeHide()"
           (focus)="popPort.show()" (blur)="popPort.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-wallet" /></svg>
          <hf-popover #popPort placement="right" align="center" size="compact" role="tooltip">Portfolio</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/watchlist" routerLinkActive="active" #navWatch="routerLinkActive"
           [attr.aria-current]="navWatch.isActive ? 'page' : null"
           aria-label="Watchlist"
           (mouseenter)="popWatch.show()" (mouseleave)="popWatch.maybeHide()"
           (focus)="popWatch.show()" (blur)="popWatch.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-eye" /></svg>
          <hf-popover #popWatch placement="right" align="center" size="compact" role="tooltip">Watchlist</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/runs" [routerLinkActiveOptions]="{exact:true}"
           routerLinkActive="active" #navRuns="routerLinkActive"
           [attr.aria-current]="navRuns.isActive ? 'page' : null"
           aria-label="Runs"
           (mouseenter)="popRuns.show()" (mouseleave)="popRuns.maybeHide()"
           (focus)="popRuns.show()" (blur)="popRuns.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-pulse" /></svg>
          <hf-popover #popRuns placement="right" align="center" size="compact" role="tooltip">Runs</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/runs/new" routerLinkActive="active" #navNewRun="routerLinkActive"
           [attr.aria-current]="navNewRun.isActive ? 'page' : null"
           aria-label="New run"
           (mouseenter)="popNewRun.show()" (mouseleave)="popNewRun.maybeHide()"
           (focus)="popNewRun.show()" (blur)="popNewRun.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-play" /></svg>
          <hf-popover #popNewRun placement="right" align="center" size="compact" role="tooltip">New run</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/screener" routerLinkActive="active" #navScreener="routerLinkActive"
           [attr.aria-current]="navScreener.isActive ? 'page' : null"
           aria-label="Screener"
           (mouseenter)="popScreener.show()" (mouseleave)="popScreener.maybeHide()"
           (focus)="popScreener.show()" (blur)="popScreener.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-filter" /></svg>
          <hf-popover #popScreener placement="right" align="center" size="compact" role="tooltip">Screener</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/backtests" routerLinkActive="active" #navBt="routerLinkActive"
           [attr.aria-current]="navBt.isActive ? 'page' : null"
           aria-label="Backtests"
           (mouseenter)="popBt.show()" (mouseleave)="popBt.maybeHide()"
           (focus)="popBt.show()" (blur)="popBt.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-beaker" /></svg>
          <hf-popover #popBt placement="right" align="center" size="compact" role="tooltip">Backtests</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/strategies" routerLinkActive="active" #navSt="routerLinkActive"
           [attr.aria-current]="navSt.isActive ? 'page' : null"
           aria-label="Strategies"
           (mouseenter)="popSt.show()" (mouseleave)="popSt.maybeHide()"
           (focus)="popSt.show()" (blur)="popSt.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-layers" /></svg>
          <hf-popover #popSt placement="right" align="center" size="compact" role="tooltip">Strategies</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/news" routerLinkActive="active" #navNews="routerLinkActive"
           [attr.aria-current]="navNews.isActive ? 'page' : null"
           aria-label="News"
           (mouseenter)="popNews.show()" (mouseleave)="popNews.maybeHide()"
           (focus)="popNews.show()" (blur)="popNews.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-news" /></svg>
          <hf-popover #popNews placement="right" align="center" size="compact" role="tooltip">News</hf-popover>
        </a>
        <div class="spacer"></div>
        <a class="nav-btn" routerLink="/profile" routerLinkActive="active" #navProfile="routerLinkActive"
           [attr.aria-current]="navProfile.isActive ? 'page' : null"
           aria-label="Profile"
           (mouseenter)="popProfile.show()" (mouseleave)="popProfile.maybeHide()"
           (focus)="popProfile.show()" (blur)="popProfile.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-user" /></svg>
          <hf-popover #popProfile placement="right" align="center" size="compact" role="tooltip">Profile</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/info" routerLinkActive="active" #navInfo="routerLinkActive"
           [attr.aria-current]="navInfo.isActive ? 'page' : null"
           aria-label="Guides"
           (mouseenter)="popInfo.show()" (mouseleave)="popInfo.maybeHide()"
           (focus)="popInfo.show()" (blur)="popInfo.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-info" /></svg>
          <hf-popover #popInfo placement="right" align="center" size="compact" role="tooltip">Guides</hf-popover>
        </a>
        <a class="nav-btn" routerLink="/settings/models" routerLinkActive="active" #navSet="routerLinkActive"
           [attr.aria-current]="navSet.isActive ? 'page' : null"
           aria-label="Settings"
           (mouseenter)="popSet.show()" (mouseleave)="popSet.maybeHide()"
           (focus)="popSet.show()" (blur)="popSet.maybeHide()">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-settings" /></svg>
          <hf-popover #popSet placement="right" align="center" size="compact" role="tooltip">Settings</hf-popover>
        </a>
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
            <button class="icon-btn" (click)="toggleTheme()" [attr.aria-label]="themeToggleLabel()">
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
              <hf-popover #popEmail placement="bottom" align="end" size="compact" role="tooltip">
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
