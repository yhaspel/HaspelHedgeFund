import { Component, HostListener, Input, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';
import { CommandPaletteComponent } from './command-palette.component';

type Theme = 'light' | 'dark';
const THEME_KEY = 'hf.theme';

@Component({
  selector: 'hf-app-shell',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, CommandPaletteComponent],
  template: `
    <div class="app">
      <a class="skip-link" href="#main-content">Skip to main content</a>
      <nav class="sidebar" aria-label="Primary">
        <div class="logo" title="Haspel Hedge Fund">
          <img src="/icon.svg" alt="Haspel Hedge Fund" width="30" height="30" />
        </div>
        <a class="nav-btn" routerLink="/" [routerLinkActiveOptions]="{exact:true}"
           routerLinkActive="active" #navHome="routerLinkActive"
           [attr.aria-current]="navHome.isActive ? 'page' : null"
           aria-label="Dashboard" data-tip="Dashboard">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-home" /></svg>
        </a>
        <a class="nav-btn" routerLink="/portfolio" routerLinkActive="active" #navPort="routerLinkActive"
           [attr.aria-current]="navPort.isActive ? 'page' : null"
           aria-label="Portfolio" data-tip="Portfolio">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-wallet" /></svg>
        </a>
        <a class="nav-btn" routerLink="/runs" [routerLinkActiveOptions]="{exact:true}"
           routerLinkActive="active" #navRuns="routerLinkActive"
           [attr.aria-current]="navRuns.isActive ? 'page' : null"
           aria-label="Runs" data-tip="Runs">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-pulse" /></svg>
        </a>
        <a class="nav-btn" routerLink="/runs/new" routerLinkActive="active" #navNewRun="routerLinkActive"
           [attr.aria-current]="navNewRun.isActive ? 'page' : null"
           aria-label="New run" data-tip="New run">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-play" /></svg>
        </a>
        <a class="nav-btn" routerLink="/backtests" routerLinkActive="active" #navBt="routerLinkActive"
           [attr.aria-current]="navBt.isActive ? 'page' : null"
           aria-label="Backtests" data-tip="Backtests">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-beaker" /></svg>
        </a>
        <a class="nav-btn" routerLink="/strategies" routerLinkActive="active" #navSt="routerLinkActive"
           [attr.aria-current]="navSt.isActive ? 'page' : null"
           aria-label="Strategies" data-tip="Strategies">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-layers" /></svg>
        </a>
        <div class="spacer"></div>
        <a class="nav-btn" routerLink="/info" routerLinkActive="active" #navInfo="routerLinkActive"
           [attr.aria-current]="navInfo.isActive ? 'page' : null"
           aria-label="Guides" data-tip="Guides">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-info" /></svg>
        </a>
        <a class="nav-btn" routerLink="/settings/models" routerLinkActive="active" #navSet="routerLinkActive"
           [attr.aria-current]="navSet.isActive ? 'page' : null"
           aria-label="Settings" data-tip="Settings">
          <svg width="18" height="18" aria-hidden="true"><use href="/icons.svg#i-settings" /></svg>
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
            <span style="flex:1;text-align:left">Search runs, strategies, backtests…</span>
            <span class="kbd">{{ paletteShortcutHint() }}</span>
          </button>
          <div style="display:flex;align-items:center;gap:12px">
            <button class="icon-btn" (click)="toggleTheme()" [attr.aria-label]="themeToggleLabel()">
              <svg width="16" height="16" aria-hidden="true">
                <use [attr.href]="theme() === 'dark' ? '/icons.svg#i-sun' : '/icons.svg#i-moon'" />
              </svg>
            </button>
            <span *ngIf="auth.user() as u" class="mono" style="font-size:12px;color:var(--text-2)">{{ u.email }}</span>
            <button *ngIf="auth.user()" class="btn ghost sm" (click)="auth.logout()">Log out</button>
          </div>
        </div>
        <div class="page" id="main-content" tabindex="-1">
          <ng-content></ng-content>
        </div>
      </main>
      @if (paletteOpen()) {
        <hf-command-palette (closed)="paletteOpen.set(false)" />
      }
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
    `,
  ],
})
export class AppShellComponent implements OnInit {
  @Input() crumbs: { label: string; link?: string }[] = [];
  readonly auth = inject(AuthStore);

  readonly theme = signal<Theme>('dark');
  readonly themeToggleLabel = computed(() =>
    this.theme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
  );

  // ADR 0004: global command palette state.
  readonly paletteOpen = signal(false);
  readonly paletteShortcutHint = computed(() => this.isMac() ? '⌘K' : 'Ctrl K');

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
