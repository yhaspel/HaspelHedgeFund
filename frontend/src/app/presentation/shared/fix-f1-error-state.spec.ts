import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { ErrorStateComponent } from './error-state.component';

/**
 * WP F1 — <hf-error-state> is the counterpart to <hf-empty-state>.
 *
 * Every list and detail page used to collapse "the request failed" into
 * "there is nothing here" ("You haven't started any runs yet." on a 500), with
 * no way to retry short of a page reload.
 */
@Component({
  standalone: true,
  imports: [ErrorStateComponent],
  template: `
    <hf-error-state
      [title]="title()"
      [detail]="detail()"
      [showRetry]="showRetry()"
      (retry)="retries.set(retries() + 1)"
    ></hf-error-state>
  `,
})
class Host {
  readonly title = signal('Couldn’t load your runs');
  readonly detail = signal<string | null>('upstream down');
  readonly showRetry = signal(true);
  readonly retries = signal(0);
}

describe('fix-f1 · ErrorStateComponent', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [Host] });
  });

  it('announces itself as an alert and shows the server message', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const box = el.querySelector('[data-test="error-state"]')!;
    expect(box.getAttribute('role')).toBe('alert');
    expect(box.textContent).toContain('Couldn’t load your runs');
    expect(el.querySelector('[data-test="error-state-detail"]')!.textContent).toContain(
      'upstream down',
    );
  });

  it('emits retry when the button is pressed', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const btn = fixture.nativeElement.querySelector(
      '[data-test="error-state-retry"]',
    ) as HTMLButtonElement;
    btn.click();
    btn.click();
    expect(fixture.componentInstance.retries()).toBe(2);
  });

  it('omits the detail line and the retry button when they are not wanted', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.detail.set(null);
    fixture.componentInstance.showRetry.set(false);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('[data-test="error-state-detail"]')).toBeNull();
    expect(el.querySelector('[data-test="error-state-retry"]')).toBeNull();
  });
});
