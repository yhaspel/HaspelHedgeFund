import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-app-shell',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive],
  template: `
    <div class="app">
      <aside class="sidebar">
        <div class="logo" aria-label="Haspel">
          <svg width="16" height="16"><use href="/icons.svg#i-logo" /></svg>
        </div>
        <a class="nav-btn" routerLink="/" [routerLinkActiveOptions]="{exact:true}" routerLinkActive="active" data-tip="Dashboard">
          <svg width="18" height="18"><use href="/icons.svg#i-home" /></svg>
        </a>
        <a class="nav-btn" routerLink="/runs/new" routerLinkActive="active" data-tip="Runs">
          <svg width="18" height="18"><use href="/icons.svg#i-pulse" /></svg>
        </a>
        <a class="nav-btn" routerLink="/backtests" routerLinkActive="active" data-tip="Backtests">
          <svg width="18" height="18"><use href="/icons.svg#i-beaker" /></svg>
        </a>
        <a class="nav-btn" routerLink="/strategies" routerLinkActive="active" data-tip="Strategies">
          <svg width="18" height="18"><use href="/icons.svg#i-layers" /></svg>
        </a>
        <div class="spacer"></div>
        <a class="nav-btn" routerLink="/settings/models" routerLinkActive="active" data-tip="Settings">
          <svg width="18" height="18"><use href="/icons.svg#i-settings" /></svg>
        </a>
      </aside>
      <main>
        <div class="topbar">
          <nav class="crumbs">
            <a routerLink="/">Haspel</a>
            <span class="sep">/</span>
            <ng-container *ngFor="let c of crumbs; let last = last">
              <a *ngIf="!last" [routerLink]="c.link">{{ c.label }}</a>
              <span class="cur" *ngIf="last">{{ c.label }}</span>
              <span class="sep" *ngIf="!last">/</span>
            </ng-container>
          </nav>
          <div style="margin-left:auto;display:flex;align-items:center;gap:12px">
            <button class="icon-btn" (click)="toggleTheme()" aria-label="Toggle theme">
              <svg width="16" height="16"><use href="/icons.svg#i-moon" /></svg>
            </button>
            <span *ngIf="auth.user() as u" class="mono" style="font-size:12px;color:var(--text-2)">{{ u.email }}</span>
            <button *ngIf="auth.user()" class="btn ghost sm" (click)="auth.logout()">Log out</button>
          </div>
        </div>
        <div class="page">
          <ng-content></ng-content>
        </div>
      </main>
    </div>
  `,
})
export class AppShellComponent {
  @Input() crumbs: { label: string; link?: string }[] = [];
  readonly auth = inject(AuthStore);

  toggleTheme() {
    const root = document.documentElement;
    root.dataset['theme'] = root.dataset['theme'] === 'light' ? 'dark' : 'light';
  }
}
