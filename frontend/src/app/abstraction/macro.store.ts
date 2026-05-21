import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import { MarkovConsensus } from '../core/models/regime.model';

export interface MacroSnapshot {
  as_of_date: string;
  growth_quadrant: 'expansion' | 'slowdown' | 'recession' | 'recovery';
  inflation_regime: 'low' | 'moderate' | 'high' | 'accelerating';
  yield_curve_state: 'normal' | 'flat' | 'inverted';
  policy_stance: 'tightening' | 'neutral' | 'easing';
  narrative: string;
  sector_implications: Record<string, 'overweight' | 'neutral' | 'underweight'>;
  series_used: Record<string, number | null>;
  markov_consensus?: MarkovConsensus | null;
}

export interface NewsItem {
  published_at: string;
  headline: string;
  source: string;
  provider: string;
  url: string;
  summary: string;
  materiality_score: number | null;
  materiality_tag: string;
}

export interface TickerNewsResponse {
  ticker: string;
  as_of: string;
  items: NewsItem[];
}

@Injectable({ providedIn: 'root' })
export class MacroStore {
  private readonly api = inject(ApiClient);

  private readonly _snapshot = signal<MacroSnapshot | null>(null);
  private readonly _tickerNews = signal<TickerNewsResponse | null>(null);

  readonly snapshot = this._snapshot.asReadonly();
  readonly tickerNews = this._tickerNews.asReadonly();

  loadSnapshot(asOf?: string): Observable<MacroSnapshot> {
    const q = asOf ? `?as_of=${asOf}` : '';
    return this.api
      .get<MacroSnapshot>(`/macro/snapshot/${q}`)
      .pipe(tap((s) => this._snapshot.set(s)));
  }

  loadTickerNews(ticker: string, asOf?: string): Observable<TickerNewsResponse> {
    const q = asOf ? `?as_of=${asOf}` : '';
    return this.api
      .get<TickerNewsResponse>(`/tickers/${ticker}/news/${q}`)
      .pipe(tap((n) => this._tickerNews.set(n)));
  }
}
