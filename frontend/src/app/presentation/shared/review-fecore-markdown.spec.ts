/**
 * Review (fecore) — hf-markdown renders `marked` output through
 * DomSanitizer.bypassSecurityTrustHtml with NO sanitisation step, so raw HTML
 * (including event-handler attributes) in the source reaches the live DOM.
 * Today the only consumer is the static /guides/*.md files, so exploitability
 * is low — but the primitive itself is unsafe for any future dynamic source
 * (agent output, news summaries, user notes).
 */
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { MarkdownComponent } from './markdown.component';

@Component({
  standalone: true,
  imports: [MarkdownComponent],
  template: `<hf-markdown [source]="src" [dropLeadingHeading]="false" />`,
})
class HostCmp {
  src = '';
}

describe('review-fecore: hf-markdown sanitisation', () => {
  it('evidence: inline HTML with an onerror handler survives rendering unsanitised', async () => {
    const fixture = TestBed.createComponent(HostCmp);
    fixture.componentInstance.src =
      'Hello\n\n<img src=x onerror="window.__pwned=1">\n\n<a href="javascript:alert(1)">link</a>';
    await fixture.whenStable();
    fixture.detectChanges();
    await fixture.whenStable();

    const root: HTMLElement = fixture.nativeElement;
    const img = root.querySelector('img');
    expect(img).not.toBeNull();
    // Angular's default [innerHTML] sanitiser would have stripped `onerror`;
    // bypassSecurityTrustHtml (markdown.component.ts:64) turns that off.
    expect(img?.getAttribute('onerror')).toBe('window.__pwned=1');
    const a = root.querySelector('a');
    expect(a?.getAttribute('href')).toBe('javascript:alert(1)');
  });
});
