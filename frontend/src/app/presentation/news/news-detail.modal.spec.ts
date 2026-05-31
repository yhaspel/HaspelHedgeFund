import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, beforeEach, it, expect } from 'vitest';

import { NewsDetailModalComponent } from './news-detail.modal';
import { MarketNewsItem } from '../../core/models/news.model';

function makeItem(over: Partial<MarketNewsItem> = {}): MarketNewsItem {
  return {
    id: 1,
    provider: 'fmp',
    headline: 'EN headline',
    summary: 'EN summary',
    url: 'https://x',
    image_url: '',
    source: 'FMP',
    published_at: new Date().toISOString(),
    symbols: [],
    tags: [],
    cluster_size: 1,
    language: 'zh-cn',
    translated_from: 'zh-cn',
    original_headline: '巴中关系迎来新篇章',
    original_summary: '中文摘要',
    sentiment: null,
    sentiment_score: null,
    sentiment_rationale: null,
    sentiment_model: null,
    ...over,
  };
}

describe('NewsDetailModalComponent translation', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [NewsDetailModalComponent],
      providers: [provideRouter([])],
    });
  });

  it('shows the tag and swaps to original then back', () => {
    const f = TestBed.createComponent(NewsDetailModalComponent);
    f.componentInstance.item = makeItem();
    f.detectChanges();
    expect(f.nativeElement.textContent).toContain('Auto-translated from Chinese');
    expect(f.nativeElement.textContent).toContain('EN headline');

    f.componentInstance.toggleOriginal();
    f.detectChanges();
    expect(f.nativeElement.textContent).toContain('巴中关系迎来新篇章');

    f.componentInstance.toggleOriginal();
    f.detectChanges();
    expect(f.nativeElement.textContent).toContain('EN headline');
  });

  it('resets showOriginal when the item input changes', () => {
    const f = TestBed.createComponent(NewsDetailModalComponent);
    f.componentInstance.item = makeItem();
    f.componentInstance.toggleOriginal();
    expect(f.componentInstance.showOriginal()).toBe(true);

    f.componentInstance.item = makeItem({ id: 2 });
    expect(f.componentInstance.showOriginal()).toBe(false);
  });

  it('renders no toggle when not translated', () => {
    const f = TestBed.createComponent(NewsDetailModalComponent);
    f.componentInstance.item = makeItem({
      translated_from: null,
      original_headline: null,
      original_summary: null,
    });
    f.detectChanges();
    expect(f.nativeElement.textContent).not.toContain('View original');
  });
});
