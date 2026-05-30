import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Shared sub-navigation across the /settings/* pages. */
@Component({
  selector: 'hf-settings-tabs',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav class="settings-tabs" aria-label="Settings sections">
      <a routerLink="/settings/models" routerLinkActive="active">Models</a>
      <a routerLink="/settings/notifications" routerLinkActive="active">Notifications</a>
    </nav>
  `,
  styles: [
    `
      .settings-tabs {
        display: flex;
        gap: 4px;
        margin: -4px 0 16px;
        border-bottom: 1px solid var(--border);
      }
      .settings-tabs a {
        padding: 8px 14px;
        font-size: 13px;
        color: var(--text-3);
        text-decoration: none;
        border-bottom: 2px solid transparent;
      }
      .settings-tabs a:hover {
        color: var(--text-1);
      }
      .settings-tabs a.active {
        color: var(--text-1);
        border-bottom-color: var(--accent);
      }
    `,
  ],
})
export class SettingsTabsComponent {}
