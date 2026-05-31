import { TestBed } from '@angular/core/testing';
import { describe, beforeEach, it, expect } from 'vitest';

import { NewsTileComponent } from './news-tile.component';
import { MarketNewsItem } from '../../core/models/news.model';

function makeItem(over: Partial<MarketNewsItem> = {}): MarketNewsItem {
  return {
    id: 1,
    provider: 'fmp',
    headline: 'EN headline',
    summary: 'summary',
    url: 'https://x',
    image_url: '',
    source: 'FMP',
    published_at: new Date().toISOString(),
    symbols: [],
    tags: [],
    cluster_size: 1,
    language: null,
    translated_from: null,
    sentiment: null,
    sentiment_score: null,
    sentiment_rationale: null,
    sentiment_model: null,
    ...over,
  };
}

describe('NewsTileComponent translated tag', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [NewsTileComponent] });
  });

  it('renders the tag with the mapped language name', () => {
    const f = TestBed.createComponent(NewsTileComponent);
    f.componentInstance.item = makeItem({ translated_from: 'zh-cn' });
    f.detectChanges();
    expect(f.nativeElement.textContent).toContain('Auto-translated from Chinese');
  });

  it('falls back to the uppercased code for an unmapped language', () => {
    const f = TestBed.createComponent(NewsTileComponent);
    f.componentInstance.item = makeItem({ translated_from: 'xx' });
    f.detectChanges();
    expect(f.nativeElement.textContent).toContain('Auto-translated from XX');
  });

  it('renders no tag when not translated', () => {
    const f = TestBed.createComponent(NewsTileComponent);
    f.componentInstance.item = makeItem({ translated_from: null });
    f.detectChanges();
    expect(f.nativeElement.textContent).not.toContain('Auto-translated');
  });
});
