// P4-pre-news-xlate — language code → display name for the "Auto-translated"
// tag. Covers the codes the detector produces; unmapped codes fall back to the
// uppercased code.

const LANGUAGE_NAMES: Record<string, string> = {
  'zh-cn': 'Chinese',
  'zh-tw': 'Chinese',
  he: 'Hebrew',
  fr: 'French',
  de: 'German',
  es: 'Spanish',
  it: 'Italian',
  nl: 'Dutch',
  da: 'Danish',
  no: 'Norwegian',
  sv: 'Swedish',
  tr: 'Turkish',
  pt: 'Portuguese',
  ru: 'Russian',
  ja: 'Japanese',
  ko: 'Korean',
  ar: 'Arabic',
  'und-nonlatin': 'another language',
};

export function languageName(code: string | null | undefined): string {
  if (!code) return 'another language';
  return LANGUAGE_NAMES[code] ?? code.toUpperCase();
}
