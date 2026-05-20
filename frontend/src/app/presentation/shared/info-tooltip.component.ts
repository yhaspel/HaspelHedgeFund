import { Component, input } from '@angular/core';

/**
 * Small "!" icon with a hover tooltip. Pure CSS — appears on hover/focus.
 */
@Component({
  selector: 'hf-info',
  standalone: true,
  template: `
    <span class="hf-info">
      <button type="button" tabindex="0" class="hf-info-trigger" [attr.aria-label]="text()">!</button>
      <span class="hf-info-tip">{{ text() }}</span>
    </span>
  `,
  styles: [`
    .hf-info {
      position: relative;
      display: inline-block;
      vertical-align: middle;
      margin-left: 4px;
    }
    .hf-info-trigger {
      width: 14px;
      height: 14px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: var(--surface-2);
      border: 1px solid var(--border-2);
      color: var(--text-2);
      font-size: 9px;
      font-weight: 700;
      line-height: 1;
      padding: 0;
      cursor: help;
      transition: background var(--dur-fast), color var(--dur-fast), border-color var(--dur-fast);
    }
    .hf-info-trigger:hover,
    .hf-info-trigger:focus {
      background: var(--acc-info);
      border-color: var(--acc-info);
      color: white;
      outline: none;
    }
    .hf-info-tip {
      visibility: hidden;
      opacity: 0;
      position: absolute;
      z-index: 90;
      left: 20px;
      top: 50%;
      transform: translateY(-50%);
      width: 240px;
      background: var(--surface-3);
      color: var(--text);
      border: 1px solid var(--border-2);
      font-size: 11.5px;
      line-height: 16px;
      border-radius: 4px;
      padding: 8px 10px;
      box-shadow: var(--shadow-2);
      pointer-events: none;
      transition: opacity var(--dur-fast);
    }
    .hf-info:hover .hf-info-tip,
    .hf-info:focus-within .hf-info-tip {
      visibility: visible;
      opacity: 1;
    }
  `],
})
export class InfoTooltipComponent {
  text = input<string>('');
}
