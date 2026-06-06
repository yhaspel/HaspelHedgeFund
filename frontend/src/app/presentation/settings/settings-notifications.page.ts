import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiClient } from '../../core/api/api-client';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';

interface Channel {
  id: number;
  kind: string;
  name: string;
  is_active: boolean;
  config_summary: Record<string, string>;
}

@Component({
  selector: 'hf-settings-notifications-page',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AppShellComponent, SettingsTabsComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Settings', link: '/settings/models' }, { label: 'Notifications' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">Notifications</h1>
          <p class="sub">
            Where scheduled-run alerts are delivered. Email works out of the box;
            Telegram needs a bot token + chat id — see the
            <a routerLink="/info/telegram-setup">Telegram setup guide</a>.
          </p>
        </div>
      </div>

      <hf-settings-tabs />

      <div role="tabpanel" aria-label="Notifications settings">

      @if (msg()) { <p class="note" role="status">{{ msg() }}</p> }
      @if (error()) { <p class="alert" role="alert">{{ error() }}</p> }

      <section class="card">
        <div class="card-hd"><h2 class="title">Add a channel</h2></div>
        <div class="grid">
          <label>Type
            <select [(ngModel)]="kind">
              <option value="email">Email</option>
              <option value="telegram">Telegram</option>
            </select>
          </label>
          <label>Label (optional)
            <input [(ngModel)]="name" placeholder="My phone" />
          </label>
          @if (kind === 'email') {
            <label>Email address (blank = account email)
              <input [(ngModel)]="address" placeholder="you@example.com" />
            </label>
          } @else {
            <label>Bot token
              <input [(ngModel)]="botToken" placeholder="123456:ABC-..." />
            </label>
            <label>Chat id
              <input [(ngModel)]="chatId" placeholder="123456789" />
            </label>
          }
        </div>
        <div class="form-actions">
          <button class="btn primary" (click)="add()" [disabled]="busy()">Add channel</button>
        </div>
      </section>

      <section class="card">
        <div class="card-hd"><h2 class="title">Your channels</h2></div>
        @if (channels().length === 0) {
          <p class="empty">No channels yet.</p>
        } @else {
          <div class="tbl-scroll">
            <table class="tbl">
              <thead>
                <tr><th>Type</th><th>Label</th><th>Target</th><th>Active</th><th></th></tr>
              </thead>
              <tbody>
                @for (c of channels(); track c.id) {
                  <tr>
                    <td>{{ c.kind }}</td>
                    <td>{{ c.name || '—' }}</td>
                    <td class="muted">{{ target(c) }}</td>
                    <td>{{ c.is_active ? 'yes' : 'no' }}</td>
                    <td class="actions">
                      <button class="btn sm" (click)="test(c)">Send test</button>
                      <button class="btn sm danger" (click)="remove(c)">Delete</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </section>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { margin-bottom:16px; }
      .sub { color: var(--text-3); font-size:12.5px; margin-top:4px; max-width:640px; }
      .card { margin-bottom:16px; }
      .grid { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:12px; }
      label { display:flex; flex-direction:column; gap:4px; font-size:12px; color: var(--text-3); }
      input, select { padding:7px 10px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text); border:1px solid var(--border); }
      .form-actions { margin-top:14px; }
      .empty { color: var(--text-3); font-size:13px; padding:8px 2px; }
      .note { padding:10px 14px; background: var(--acc-long-soft); color: var(--acc-long-fg); border-radius: var(--r-6); margin:0 0 12px; }
      .alert { padding:10px 14px; background: var(--acc-short-soft); color: var(--acc-short-fg); border-radius: var(--r-6); margin:0 0 12px; }
      .tbl .muted { color: var(--text-3); } .actions { display:flex; gap:6px; }
    `,
  ],
})
export class SettingsNotificationsPage implements OnInit {
  private readonly api = inject(ApiClient);

  readonly channels = signal<Channel[]>([]);
  readonly busy = signal(false);
  readonly msg = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  kind = 'email';
  name = '';
  address = '';
  botToken = '';
  chatId = '';

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.api.get<Channel[]>('/notification-channels/').subscribe((c) => this.channels.set(c));
  }

  target(c: Channel): string {
    if (c.kind === 'email') return c.config_summary?.['address'] || '(account email)';
    return `chat ${c.config_summary?.['chat_id'] || '?'}`;
  }

  add(): void {
    this.error.set(null);
    this.msg.set(null);
    this.busy.set(true);
    const config =
      this.kind === 'email'
        ? { address: this.address }
        : { bot_token: this.botToken, chat_id: this.chatId };
    this.api
      .post('/notification-channels/', { kind: this.kind, name: this.name, config })
      .subscribe({
        next: () => {
          this.busy.set(false);
          this.name = this.address = this.botToken = this.chatId = '';
          this.reload();
        },
        error: (e) => {
          this.busy.set(false);
          this.error.set(e?.error?.detail ?? 'Failed to add channel');
        },
      });
  }

  test(c: Channel): void {
    this.error.set(null);
    this.msg.set(null);
    this.api.post<{ delivery_status: string; error: string }>(
      `/notification-channels/${c.id}/test/`,
      {},
    ).subscribe({
      next: (r) => this.msg.set(`Test ${r.delivery_status}.`),
      error: (e) =>
        this.error.set(
          `Test failed: ${e?.error?.error ?? e?.error?.detail ?? 'delivery error'}`,
        ),
    });
  }

  remove(c: Channel): void {
    if (!confirm('Delete this channel?')) return;
    this.api.delete(`/notification-channels/${c.id}/`).subscribe(() => this.reload());
  }
}
