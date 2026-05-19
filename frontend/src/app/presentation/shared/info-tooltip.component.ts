import { Component, input } from '@angular/core';

/**
 * Small "!" icon with a hover tooltip. Uses Tailwind's `group` so the tooltip
 * appears on hover/focus without any JS.
 */
@Component({
  selector: 'hf-info',
  standalone: true,
  template: `
    <span class="relative inline-block group align-middle ml-1">
      <button type="button" tabindex="0"
        class="w-4 h-4 inline-flex items-center justify-center rounded-full
               bg-gray-300 text-gray-700 text-[10px] font-bold leading-none
               hover:bg-blue-500 hover:text-white focus:outline-none"
        [attr.aria-label]="text()">!</button>
      <span class="invisible group-hover:visible group-focus-within:visible
                   absolute z-10 left-5 top-1/2 -translate-y-1/2
                   w-64 bg-gray-900 text-white text-xs rounded p-2 shadow-lg
                   pointer-events-none">
        {{ text() }}
      </span>
    </span>
  `,
})
export class InfoTooltipComponent {
  text = input<string>('');
}
