import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  QueryList,
  ViewChildren,
} from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

interface SettingsTab {
  path: string;
  label: string;
  icon: string;
}

/**
 * Shared sub-navigation across the /settings/* pages.
 *
 * Implemented as a real ARIA tablist: each routed link is a `role="tab"`
 * with `aria-selected` reflecting the active route, roving `tabindex`
 * (only the selected tab is in the tab order), and arrow / Home / End
 * keyboard navigation between tabs. Visible focus rings are preserved.
 * Each /settings/* page wraps its content in a matching `role="tabpanel"`.
 */
@Component({
  selector: 'hf-settings-tabs',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav class="tabs settings-tabs" role="tablist" aria-label="Settings sections">
      @for (t of tabs; track t.path; let i = $index) {
        <a
          class="tab"
          role="tab"
          [routerLink]="t.path"
          routerLinkActive="active"
          #rla="routerLinkActive"
          [attr.aria-selected]="rla.isActive"
          [attr.aria-current]="rla.isActive ? 'page' : null"
          [tabindex]="rla.isActive ? 0 : -1"
          (keydown)="onKey($event, i)"
          #tabEl
        >
          <svg width="15" height="15" aria-hidden="true">
            <use [attr.href]="'/icons.svg#' + t.icon" />
          </svg>
          {{ t.label }}
        </a>
      }
    </nav>
  `,
  styles: [
    `
      .settings-tabs {
        margin: -4px 0 16px;
      }
      .settings-tabs svg {
        flex: none;
        opacity: 0.85;
      }
      /* Keep a visible focus ring on keyboard navigation. */
      .settings-tabs .tab:focus-visible {
        outline: none;
        box-shadow: var(--focus-ring);
        border-radius: var(--r-4);
      }
    `,
  ],
})
export class SettingsTabsComponent {
  @ViewChildren('tabEl') tabEls!: QueryList<ElementRef<HTMLAnchorElement>>;

  readonly tabs: SettingsTab[] = [
    { path: '/settings/models', label: 'Models', icon: 'i-cpu' },
    { path: '/settings/providers', label: 'Providers', icon: 'i-key' },
    { path: '/settings/personas', label: 'Personas', icon: 'i-layers' },
    { path: '/settings/data-news', label: 'Data & News', icon: 'i-pulse' },
    { path: '/settings/notifications', label: 'Notifications', icon: 'i-bell' },
  ];

  /** Roving-focus keyboard navigation per the ARIA tabs pattern (manual
   *  activation: arrows move focus, Enter/Space follows the link). */
  onKey(e: KeyboardEvent, i: number): void {
    const n = this.tabs.length;
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
