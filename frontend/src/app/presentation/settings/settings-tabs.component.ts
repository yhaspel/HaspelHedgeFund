import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  QueryList,
  ViewChildren,
  inject,
} from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';

interface SettingsSection {
  path: string;
  label: string;
  icon: string;
}

interface SettingsGroup {
  label: string;
  icon: string;
  sections: SettingsSection[];
}

/**
 * Shared sub-navigation across the /settings/* pages.
 *
 * P10 §D5 — the settings nav shows TWO top-level entries instead of five:
 * "General" (data & news, notifications, your profile) and "Models & advanced"
 * (model tiers, providers, personas — council-era operator tooling). Deep
 * links to the original /settings/* routes keep working; the active group's
 * sections render as a secondary row.
 *
 * Implemented as a real ARIA tablist (roving tabindex + arrow keys) on the
 * group row; the section row is a plain nav.
 */
@Component({
  selector: 'hf-settings-tabs',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav class="tabs settings-tabs" role="tablist" aria-label="Settings sections">
      @for (g of groups; track g.label; let i = $index) {
        <a
          class="tab"
          role="tab"
          [routerLink]="g.sections[0].path"
          [class.active]="isGroupActive(g)"
          [attr.aria-selected]="isGroupActive(g)"
          [attr.aria-current]="isGroupActive(g) ? 'page' : null"
          [tabindex]="isGroupActive(g) ? 0 : -1"
          (keydown)="onKey($event, i)"
          #tabEl
        >
          <svg width="15" height="15" aria-hidden="true">
            <use [attr.href]="'/icons.svg#' + g.icon" />
          </svg>
          {{ g.label }}
        </a>
      }
    </nav>
    @if (activeGroup(); as g) {
      <nav class="settings-sections" aria-label="Sections">
        @for (s of g.sections; track s.path) {
          <a class="sec" [routerLink]="s.path" routerLinkActive="active"
             #rla="routerLinkActive"
             [attr.aria-current]="rla.isActive ? 'page' : null">
            <svg width="13" height="13" aria-hidden="true">
              <use [attr.href]="'/icons.svg#' + s.icon" />
            </svg>
            {{ s.label }}
          </a>
        }
      </nav>
    }
  `,
  styles: [
    `
      .settings-tabs {
        margin: -4px 0 10px;
      }
      .settings-tabs svg,
      .settings-sections svg {
        flex: none;
        opacity: 0.85;
      }
      .settings-sections {
        display: flex;
        gap: 6px;
        margin: 0 0 16px;
      }
      .settings-sections .sec {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 3px 10px;
        border: 1px solid var(--border);
        border-radius: var(--r-full);
        color: var(--text-2);
        font-size: 12px;
        text-decoration: none;
      }
      .settings-sections .sec:hover {
        background: var(--surface-2);
      }
      .settings-sections .sec.active {
        background: var(--surface-2);
        color: var(--text);
        border-color: var(--text-3);
      }
      /* Keep a visible focus ring on keyboard navigation. */
      .settings-tabs .tab:focus-visible,
      .settings-sections .sec:focus-visible {
        outline: none;
        box-shadow: var(--focus-ring);
        border-radius: var(--r-4);
      }
    `,
  ],
})
export class SettingsTabsComponent {
  @ViewChildren('tabEl') tabEls!: QueryList<ElementRef<HTMLAnchorElement>>;
  private readonly router = inject(Router);

  readonly groups: SettingsGroup[] = [
    {
      label: 'General',
      icon: 'i-settings',
      sections: [
        { path: '/settings/data-news', label: 'Data & News', icon: 'i-pulse' },
        { path: '/settings/notifications', label: 'Notifications', icon: 'i-bell' },
        // P10 §D5: the investor profile folds into Settings (it left the sidebar).
        { path: '/profile', label: 'Your profile', icon: 'i-user' },
      ],
    },
    {
      label: 'Models & advanced',
      icon: 'i-cpu',
      sections: [
        { path: '/settings/models', label: 'Models', icon: 'i-cpu' },
        { path: '/settings/providers', label: 'Providers', icon: 'i-key' },
        { path: '/settings/personas', label: 'Personas', icon: 'i-layers' },
      ],
    },
  ];

  isGroupActive(g: SettingsGroup): boolean {
    const url = this.router.url;
    return g.sections.some((s) => url.startsWith(s.path));
  }

  activeGroup(): SettingsGroup | null {
    return this.groups.find((g) => this.isGroupActive(g)) ?? null;
  }

  /** Roving-focus keyboard navigation per the ARIA tabs pattern (manual
   *  activation: arrows move focus, Enter/Space follows the link). */
  onKey(e: KeyboardEvent, i: number): void {
    const n = this.groups.length;
    let target = -1;
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        target = (i + 1) % n;
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        target = (i - 1 + n) % n;
        break;
      case 'Home':
        target = 0;
        break;
      case 'End':
        target = n - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    this.tabEls?.toArray()[target]?.nativeElement.focus();
  }
}
