import type { ChartOptions } from 'chart.js';
import { ALL_PERSONAS, personaColorVar } from '../../core/models/run.model';

function readVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export interface ChartTheme {
  grid: string;
  axis: string;
  tipBg: string;
  tipBorder: string;
  textMuted: string;
  long: string;
  short: string;
  info: string;
}

export function readChartTheme(): ChartTheme {
  return {
    grid: readVar('--chart-grid', '#1A1F28'),
    axis: readVar('--chart-axis', '#5C6470'),
    tipBg: readVar('--chart-tip-bg', '#161A21'),
    tipBorder: readVar('--chart-tip-bd', '#2C313D'),
    textMuted: readVar('--text-3', '#8B95A4'),
    long: readVar('--acc-long', '#16A974'),
    short: readVar('--acc-short', '#E5484D'),
    info: readVar('--acc-info', '#5B8DEF'),
  };
}

export const ENTRY_ANIMATION = {
  duration: 600,
  easing: 'easeOutCubic' as const,
};

export function personaPaletteResolved(): string[] {
  return ALL_PERSONAS.map((p) => readVar(`--c${p.monogramVariant}`, '#5B8DEF'));
}

export function personaColorById(id: string): string {
  const cssVar = personaColorVar(id).match(/--c\d/)?.[0];
  return cssVar ? readVar(cssVar, '#5B8DEF') : '#5B8DEF';
}

export function baseAxisOptions(theme: ChartTheme): NonNullable<ChartOptions<'line'>['scales']>[string] {
  return {
    grid: { color: theme.grid },
    ticks: { color: theme.axis, font: { family: 'JetBrains Mono', size: 10 } as any },
  } as any;
}

export function baseLegend(theme: ChartTheme) {
  return {
    position: 'bottom' as const,
    labels: { color: theme.textMuted, font: { size: 11 } },
  };
}
