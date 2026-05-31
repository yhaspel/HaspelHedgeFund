"""Language detection for market-news headlines (P4-pre-news-xlate).

A pure helper, called at display time on the page representatives. It does
**not** rely on langdetect alone (langdetect under-detects short CJK and
low-confidence Latin headlines):

  1. A non-Latin-script fast path flags CJK / Hebrew / Cyrillic / Arabic /
     Greek / Thai / Devanagari headlines as definitely-foreign (unambiguous
     even when very short — a 9-character Chinese headline is a full sentence).
  2. langdetect handles the Latin-script editions (fr/de/es/it/…) that a
     script check alone cannot catch, with a sane confidence gate.

This is *today*-only data; it must never be imported by a backtest or
point-in-time agent path (enforced by the PIT-import regression test).
"""
from __future__ import annotations

import re

from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0  # deterministic — langdetect re-seeds per call from this

# Scripts that are unambiguously non-English when present in any quantity.
_NON_LATIN = re.compile(
    r"[　-鿿぀-ヿ가-힯"  # CJK + kana + Hangul
    r"Ѐ-ӿ֐-׿؀-ۿ"  # Cyrillic, Hebrew, Arabic
    r"Ͱ-Ͽ฀-๿ऀ-ॿ]"  # Greek, Thai, Devanagari
)
NONLATIN_MIN = 4  # a few CJK/Hebrew/… chars is already unambiguous
LATIN_MIN_CHARS = 12  # langdetect noise floor for Latin text
LATIN_CONF = 0.85  # confidence gate for Latin-script detection only


def detect_language(text: str) -> str:
    """Detected code ('en','zh-cn','fr',…), '' (treat as English), or
    'und-nonlatin' (definitely foreign, language unnamed). Biased so legitimate
    English — including accented names / smart-quotes — is never flagged."""
    text = (text or "").strip()
    if not text:
        return ""
    # 1) Non-Latin fast path: definitely foreign, regardless of length/conf.
    if len(_NON_LATIN.findall(text)) >= NONLATIN_MIN:
        try:
            return detect_langs(text)[0].lang  # name it (zh-cn/he/ru/…) if able
        except (LangDetectException, IndexError):
            return "und-nonlatin"  # known-foreign even if unnamed
    # 2) Latin-script path: langdetect with a sane confidence gate.
    if len(text) < LATIN_MIN_CHARS:
        return ""
    try:
        top = detect_langs(text)[0]
    except (LangDetectException, IndexError):
        return ""
    return "" if (top.lang == "en" or top.prob < LATIN_CONF) else top.lang
