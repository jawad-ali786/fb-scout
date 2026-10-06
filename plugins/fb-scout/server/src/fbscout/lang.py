"""Language of a post or comment, with simple, reproducible rules (no model, no download).

Codes:
  en       English
  ur       Urdu in Arabic script
  ur-Latn  Roman Urdu (Urdu written in Latin letters, usually mixed with English words)
  ar / fa  Arabic / Persian
  hi / bn  Hindi (Devanagari) / Bengali
  tl       Tagalog / Filipino
  und      undetermined (too short, only numbers/emoji, or no known function words)

1. Count letters per script; the majority script decides Urdu/Arabic/Hindi/... .
2. For Latin script, count common words of English, Roman Urdu and Tagalog.
   Roman Urdu or Tagalog wins with 2+ hits, because such posts typically use
   Urdu/Tagalog grammar around English product words ("solar panels ko regular
   cleaning ki zaroorat hai", "minimum panel need 3pcs, mga kaya paganahin").
Extend the word lists below when you see misclassified records.
"""

from __future__ import annotations

import re
import unicodedata

ENGLISH = frozenset("""
the and is are was were be been being for to of in on at with from by about into over after before
you your yours we our us it its this that these those they them their there here what which who whom
how why when where any all more most some no not only just very can could will would should may must
have has had do does did if or but so than then also my mine me he she his her him i an
anyone someone anything know good near need want please get buy sell best new now call contact price
free like make help thanks thank looking used great quality service services work working home today
much many well out
""".split())

# Left out on purpose because they are also English words: main, me, men, to, the, so.
ROMAN_URDU = frozenset("""
hai hain hy hay hn ka ki ke ko se mein mai aur nahi nahin nai nhi kya kia bhi ho hon hun kar karo
karen karein karna krna kr kro wala wali wale walay aap ap apna apni apne hum ham tum yeh ye woh wo
tha thi thay thy raha rahe rahi rha rhe rhi sath saath liye liay lye abhi sirf bohat bahut bht acha achi
acchi achay kitna kitni kitne chahiye chahye chaiye pe jo ky kay tou agar lekin magar phir phr sab kuch
ab par koi jab tak hoga hogi honge gaya gayi gaye gya diya diye dein den mujhe mujhy hamare hamari humari aapka
apka aapki apki apke kaise kaisay kese kahan yahan wahan bilkul zaroor zaroorat zarurat
""".split())

TAGALOG = frozenset("""
ang ng mga lang po naman yung kung ito iyan siya kami tayo kayo sila natin namin ninyo hindi wala meron
mayroon ngayon dito doon salamat kaya pero rin lamang nga ba ating aming sana talaga
""".split())

_URDU_ONLY = set("ٹڈڑںےۓ")          # letters used in Urdu but not in Arabic/Persian
_PERSIAN = set("پچژگ")

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _script(ch: str) -> str | None:
    o = ord(ch)
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
        return "arabic"
    if 0x0900 <= o <= 0x097F:
        return "devanagari"
    if 0x0980 <= o <= 0x09FF:
        return "bengali"
    if ch.isascii() or 0x00C0 <= o <= 0x024F:
        return "latin"
    return "other"


def detect_language(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            s = _script(ch)
            if s:
                counts[s] = counts.get(s, 0) + 1
    if sum(counts.values()) < 3:
        return "und"
    script = max(counts, key=counts.get)

    if script == "arabic":
        letters = set(text)
        if letters & _URDU_ONLY:
            return "ur"
        return "fa" if letters & _PERSIAN else "ar"
    if script == "devanagari":
        return "hi"
    if script == "bengali":
        return "bn"
    if script != "latin":
        return "und"

    words = [w.lower() for w in _WORD_RE.findall(text) if w.isascii()]
    en = sum(w in ENGLISH for w in words)
    ru = sum(w in ROMAN_URDU for w in words)
    tl = sum(w in TAGALOG for w in words)
    if ru >= 2 or tl >= 2:
        return "ur-Latn" if ru >= tl else "tl"
    if en >= 1 and en >= ru and en >= tl:
        return "en"
    if ru >= 1:
        return "ur-Latn"
    if tl >= 1:
        return "tl"
    return "und"
