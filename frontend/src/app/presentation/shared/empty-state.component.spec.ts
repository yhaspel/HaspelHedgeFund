import { Component, ViewChild } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { EmptyStateComponent } from './empty-state.component';

@Component({
  standalone: true,
  imports: [EmptyStateComponent],
  template: `
    <hf-empty-state
      #es
      [message]="message"
      [detail]="detail"
      [compact]="compact">
      @if (showCta) {
        <button type="button" class="btn primary">Take action</button>
      }
    </hf-empty-state>
  `,
})
class HarnessComponent {
  @ViewChild('es', { static: true }) es!: EmptyStateComponent;
  message = 'Nothing here yet.';
  detail?: string;
  compact = false;
  showCta = false;
}

function makeHarness(setup?: (h: HarnessComponent) => void): ComponentFixture<HarnessComponent> {
  const fixture = TestBed.createComponent(HarnessComponent);
  if (setup) setup(fixture.componentInstance);
  fixture.detectChanges();
  return fixture;
}

describe('hf-empty-state', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HarnessComponent] }).compileComponents();
  });

  it('renders the required message', () => {
    const fixture = makeHarness((h) => { h.message = 'You have no runs yet.'; });
    expect(fixture.nativeElement.querySelector('.msg').textContent.trim()).toBe('You have no runs yet.');
  });

  it('omits the detail line when detail is unset', () => {
    const fixture = makeHarness();
    expect(fixture.nativeElement.querySelector('.detail')).toBeNull();
  });

  it('renders the detail line when provided', () => {
    const fixture = makeHarness((h) => { h.detail = 'Strategies appear here once you create them.'; });
    expect(fixture.nativeElement.querySelector('.detail').textContent.trim())
      .toBe('Strategies appear here once you create them.');
  });

  it('projects CTA content into the slot', () => {
    const fixture = makeHarness((h) => { h.showCta = true; });
    const btn = fixture.nativeElement.querySelector('button.btn.primary');
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe('Take action');
  });

  it('applies compact class when compact=true', () => {
    const fixture = makeHarness((h) => { h.compact = true; });
    expect(fixture.nativeElement.querySelector('.es').classList.contains('compact')).toBe(true);
  });

  it('exposes the empty state to assistive tech via role="status"', () => {
    const fixture = makeHarness();
    expect(fixture.nativeElement.querySelector('.es').getAttribute('role')).toBe('status');
  });
});
