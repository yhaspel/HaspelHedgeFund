import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';

import { InvestorProfileStore } from '../../abstraction/investor-profile.store';
import {
  HORIZON_BAND_LABEL,
  PATIENCE_BAND_LABEL,
  ProfileAnalysis,
  QuestionnaireHistoryItem,
  RISK_BAND_LABEL,
  STRATEGY_KIND_GUIDE_SLUG,
  STRATEGY_KIND_LABEL,
} from '../../core/models/investor-profile.model';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { AppShellComponent } from '../shared/app-shell.component';
import { WatchlistCardComponent } from '../watchlist/watchlist-card.component';
import { QuestionnaireHistoryComponent } from './questionnaire-history.component';
import { TuneBandsChange, TuneBandsComponent } from './tune-bands.component';

@Component({
  selector: 'hf-profile-page',
  standalone: true,
  imports: [
    CommonModule,
    DatePipe,
    RouterLink,
    AppShellComponent,
    EmptyStateComponent,
    QuestionnaireHistoryComponent,
    TuneBandsComponent,
    WatchlistCardComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'Profile'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Profile</div>
          <h1 class="mt-1.5">Your investor profile</h1>
          <p class="text-text-3 text-[12.5px] mt-1 max-w-[640px]">
            Personalize how the council analyses stocks for you. The
            questionnaire is optional — without it, runs use today's neutral
            defaults.
          </p>
        </div>
      </div>

      @if (store.loading()) {
        <div class="card"><div class="card-bd">Loading profile…</div></div>
      } @else if (store.bundle(); as bundle) {
        <div class="grid-2">
          <section class="card identity">
            <div class="card-hd">
              <h2 class="title">Account</h2>
            </div>
            <div class="card-bd">
              <dl class="dl">
                <dt>Email</dt><dd class="mono">{{ bundle.user.email }}</dd>
                <dt>Member since</dt>
                <dd>{{ bundle.user.joined_at ? (bundle.user.joined_at | date: 'mediumDate') : '—' }}</dd>
              </dl>
              <div class="toggle-row">
                <label class="switch">
                  <input type="checkbox"
                         [checked]="bundle.state.apply_to_runs"
                         (change)="onToggleApply($any($event.target).checked)" />
                  <span>Apply my profile to new analyses</span>
                </label>
                <p class="hint text-text-3 text-[11px]">
                  When off, every ad-hoc run behaves like an anonymous
                  account — no profile is sent to the council.
                </p>
              </div>
            </div>
          </section>

          @if (active(); as active) {
            <section class="card profile-card">
              <div class="card-hd">
                <h2 class="title">Investor profile</h2>
                <span class="muted text-text-3 text-[11px]">
                  updated {{ active.analyzed_at ? (active.analyzed_at | date: 'mediumDate') : '—' }}
                </span>
              </div>
              <div class="card-bd">
                <div class="type-row">
                  <span class="type">{{ analysisField('investor_type') }}</span>
                  <span class="badges">
                    <span class="pill">Risk · {{ riskLabel() }}</span>
                    <span class="pill">Horizon · {{ horizonLabel() }}</span>
                    <span class="pill">Patience · {{ patienceLabel() }}</span>
                  </span>
                </div>
                <p class="summary">{{ active.profile_summary }}</p>
                @if (insights().length) {
                  <ul class="insights">
                    @for (it of insights(); track it) {
                      <li>{{ it }}</li>
                    }
                  </ul>
                }
                @if (constraints().length) {
                  <p class="constraints text-text-3 text-[11px]">
                    Constraints: {{ constraints().join(' · ') }}
                  </p>
                }
                <div class="actions">
                  <button type="button"
                          class="btn ghost sm"
                          (click)="onToggleTune()">
                    {{ tuneOpen() ? 'Hide fine-tune' : 'Fine-tune' }}
                  </button>
                  <a routerLink="/profile/questionnaire" class="btn ghost sm">
                    Retake questionnaire
                  </a>
                  <button type="button"
                          class="btn ghost sm"
                          (click)="onToggleHistory()">
                    {{ historyOpen() ? 'Hide history' : 'View history' }}
                  </button>
                </div>
                @if (tuneOpen()) {
                  <div class="tune-wrap">
                    <hf-tune-bands
                      [initialRisk]="active.analysis.risk_band"
                      [initialHorizon]="active.analysis.horizon_band"
                      [initialPatience]="active.analysis.patience_band"
                      [busy]="tuning()"
                      (save)="onTuneSave($event)"
                      (cancel)="onToggleTune()"
                    />
                  </div>
                }
                @if (historyOpen()) {
                  <div class="history-wrap">
                    <hf-questionnaire-history
                      [items]="history()"
                      (view)="onViewHistory($event)"
                    />
                  </div>
                }
              </div>
            </section>

            @if (recommended().length) {
              <section class="card recos">
                <div class="card-hd">
                  <h2 class="title">Recommended strategies</h2>
                </div>
                <div class="card-bd">
                  <ul class="reco-list">
                    @for (s of recommended(); track s.kind) {
                      <li>
                        <div class="reco-hd">
                          <span class="kind">{{ kindLabel[s.kind] }}</span>
                          <span class="fit" [class]="'fit-' + s.fit">{{ s.fit }}</span>
                        </div>
                        <p>{{ s.rationale }}</p>
                        <div class="reco-actions">
                          <a class="link sm"
                             [routerLink]="['/info', kindGuide[s.kind]]">
                            Learn more →
                          </a>
                          <a class="btn primary sm"
                             [routerLink]="['/strategies/new']"
                             [queryParams]="{ kind: s.kind }">
                            Create this strategy
                          </a>
                        </div>
                      </li>
                    }
                  </ul>
                  <p class="cap text-text-3 text-[11px]">
                    Educational suggestions — not financial advice.
                  </p>
                </div>
              </section>
            }
          } @else {
            <section class="card">
              <div class="card-hd"><h2 class="title">Investor profile</h2></div>
              <div class="card-bd">
                @if (latest()?.status === 'failed') {
                  <p class="alert">
                    Your last analysis failed: {{ latest()?.error_message || 'unknown error' }}
                  </p>
                  <a class="btn primary" routerLink="/profile/questionnaire">
                    Retry the questionnaire
                  </a>
                } @else if (latest()?.status === 'running' || latest()?.status === 'pending') {
                  <p>Your profile is being analyzed — refresh in a moment.</p>
                } @else {
                  <hf-empty-state
                    message="You haven't taken the questionnaire yet."
                    detail="Two minutes of questions will personalize every ad-hoc analysis you run."
                  />
                  <div class="cta-row">
                    <a class="btn primary" routerLink="/profile/questionnaire">
                      Take the questionnaire
                    </a>
                  </div>
                }
              </div>
            </section>
          }

          <hf-watchlist-card></hf-watchlist-card>
        </div>
      } @else if (store.error()) {
        <div class="card"><div class="card-bd alert">{{ store.error() }}</div></div>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { margin-bottom: 16px; }
      .grid-2 {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 16px;
      }
      .grid-2 > .profile-card { grid-column: 1 / -1; }
      .grid-2 > .recos { grid-column: 1 / -1; }
      .dl {
        display: grid;
        grid-template-columns: max-content 1fr;
        gap: 4px 12px;
        margin: 0 0 12px;
      }
      .dl dt { color: var(--text-3); font-size: 12px; }
      .dl dd { margin: 0; font-size: 13px; }
      .type-row {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 10px;
        margin-bottom: 8px;
      }
      .type { font-size: 18px; font-weight: 600; }
      .badges { display: flex; gap: 6px; flex-wrap: wrap; }
      .pill {
        font-size: 11px;
        padding: 2px 8px;
        border-radius: var(--r-6);
        background: var(--surface-2);
        color: var(--text-2);
      }
      .summary { font-size: 13.5px; line-height: 1.5; margin: 0 0 8px; }
      .insights {
        margin: 0;
        padding-left: 18px;
        font-size: 12.5px;
      }
      .insights li { margin: 2px 0; }
      .constraints { margin: 8px 0 0; }
      .actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 12px;
      }
      .tune-wrap, .history-wrap {
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px dashed var(--border);
      }
      .reco-list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
      }
      .reco-list li {
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .reco-hd {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .kind { font-weight: 600; font-size: 13px; }
      .fit {
        font-size: 10px;
        text-transform: uppercase;
        padding: 1px 6px;
        border-radius: var(--r-3);
        background: var(--surface-2);
      }
      .fit-strong { background: var(--acc-long-soft); color: var(--acc-long-fg); }
      .fit-good { background: var(--acc-info-soft); color: var(--text); }
      .fit-consider { background: var(--surface-2); color: var(--text-2); }
      .reco-list p { font-size: 12px; margin: 0; color: var(--text-2); }
      .reco-actions { display: flex; justify-content: space-between; gap: 8px; margin-top: auto; }
      .cap { margin-top: 12px; font-style: italic; }
      .cta-row { margin-top: 12px; }
      .switch { display: flex; align-items: center; gap: 8px; font-size: 13px; }
      .alert {
        background: var(--acc-short-soft);
        color: var(--acc-short-fg);
        padding: 10px 12px;
        border-radius: var(--r-6);
      }
      .hint { margin: 4px 0 0; }
    `,
  ],
})
export class ProfilePage implements OnInit {
  readonly store = inject(InvestorProfileStore);
  private readonly router = inject(Router);

  readonly tuneOpen = signal(false);
  readonly tuning = signal(false);
  readonly historyOpen = signal(false);

  readonly active = this.store.active;
  readonly history = this.store.history;
  readonly latest = computed(() => this.store.bundle()?.latest ?? null);

  readonly analysis = computed<ProfileAnalysis | null>(() => {
    const a = this.active();
    if (!a || !a.analysis || Object.keys(a.analysis).length === 0) return null;
    return a.analysis as ProfileAnalysis;
  });
  readonly recommended = computed(
    () => this.analysis()?.recommended_strategies ?? [],
  );
  readonly insights = computed(() => this.analysis()?.insights ?? []);
  readonly constraints = computed(() => this.analysis()?.key_constraints ?? []);

  readonly riskLabel = computed(() => {
    const a = this.analysis();
    return a ? RISK_BAND_LABEL[a.risk_band] ?? a.risk_band : '—';
  });
  readonly horizonLabel = computed(() => {
    const a = this.analysis();
    return a ? HORIZON_BAND_LABEL[a.horizon_band] ?? a.horizon_band : '—';
  });
  readonly patienceLabel = computed(() => {
    const a = this.analysis();
    return a ? PATIENCE_BAND_LABEL[a.patience_band] ?? a.patience_band : '—';
  });

  readonly kindLabel = STRATEGY_KIND_LABEL;
  readonly kindGuide = STRATEGY_KIND_GUIDE_SLUG;

  ngOnInit(): void {
    this.store.load(true).subscribe();
  }

  analysisField<T extends keyof ProfileAnalysis>(key: T): ProfileAnalysis[T] | string {
    const a = this.analysis();
    return a ? a[key] : '';
  }

  onToggleApply(value: boolean): void {
    this.store.setApplyToRuns(value).subscribe();
  }

  onToggleTune(): void {
    this.tuneOpen.update((v) => !v);
  }

  onToggleHistory(): void {
    const next = !this.historyOpen();
    this.historyOpen.set(next);
    if (next) this.store.loadHistory().subscribe();
  }

  onTuneSave(change: TuneBandsChange): void {
    const active = this.active();
    if (!active) return;
    this.tuning.set(true);
    this.store.tune(active.id, change).subscribe({
      next: () => {
        this.tuning.set(false);
        this.tuneOpen.set(false);
      },
      error: () => this.tuning.set(false),
    });
  }

  onViewHistory(it: QuestionnaireHistoryItem): void {
    // No detail modal in v1 — surface the row in the user-friendly way by
    // navigating to /profile (already showing) and refreshing.
    this.store.loadOne(it.id).subscribe();
  }
}
