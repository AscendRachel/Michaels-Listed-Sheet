"""Does the Amazon listing still describe the same item as the catalog?

Amazon often rewords a title without changing the product, so plain word overlap raises false alarms.
This works in two parts:

1. FACTS: pack counts, dimensions and sizes are compared (11.8x15.7 inch equals 30x40 cm). If both titles state
   one and none of the values agree, the listing really changed ("conflict").
2. WORDS: the words of the catalog name are looked for in the Amazon title, weighted by how identifying they are
   (rare words such as a brand or a product type count a lot, filler like "set" or "decor" almost nothing).
   Plurals, joined words (pillowcase / pillow case), small typos and the team's own synonym list all count as the
   same word.

Everything here is pure Python. `rebuild_model()` (in the database layer) supplies the word weights.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter

# Words that appear in almost every listing and say nothing about which product it is
STOP = set("""a an and the for with of to in on by or at from as is it this that your you our
set sets kit kits pack packs pcs pc piece pieces count ct lot bundle new
adult adults kid kids boy boys girl girls women men beginner beginners
home decor decoration decorations decorative gift gifts party diy craft crafts art arts
inch inches cm mm ft feet size large small mini big x premium quality
perfect great best ideal suitable use using""".split())

DEFAULT_SYNONYMS = ["sticker, decal"]

TRUNCATED = re.compile(r"(\.{3,}|…)\s*$")
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_LEN = r"(?:inches|inch|in|cm|mm|ft|feet|foot|yards|yard|yd|m)"
PACK_RES = [
    re.compile(r"(?<![\d.])(\d{1,5})\s*[- ]?\s*(?:pcs|pc|pieces|piece|packs|pack|count|ct|sets|set|rolls|roll|pairs|pair)\b"),
    re.compile(r"\b(?:pack|set|lot|bundle|box)\s+of\s+(\d{1,5})\b"),
]
_U = r"(?:\"|”|inches|inch|in|cm|mm|ft|feet|foot|yards|yard|yd|m)(?![a-z])"
_N = r"\d+(?:\.\d+)?"
# 11.8x15.7 inch  /  1.5" x 100 Yards  /  9.5 X 6 X 3 inch : each number may carry its own unit, or share the last one
DIM_RE = re.compile(rf"(?P<n1>{_N})\s*(?P<u1>{_U})?\s*[x×*]\s*(?P<n2>{_N})\s*(?P<u2>{_U})?"
                    rf"(?:\s*[x×*]\s*(?P<n3>{_N})\s*(?P<u3>{_U})?)?")
SIZE_RE = re.compile(rf"{_NUM}\s*-?\s*(\"|”|inches|inch|in(?!\s*\d)|cm|mm|ft|feet|foot|yards|yard|yd|oz|ounces|ounce|lbs|lb|pounds|pound|ml|kg|gallons|gallon|gal)(?![a-z])")
TO_INCH = {"": 1.0, "in": 1.0, "inch": 1.0, "inches": 1.0, '"': 1.0, "”": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "ft": 12.0, "feet": 12.0,
           "foot": 12.0, "yd": 36.0, "yard": 36.0, "yards": 36.0, "m": 39.37}
SIZE_CLASS = {"oz": ("oz", 1.0), "ounce": ("oz", 1.0), "ounces": ("oz", 1.0), "lb": ("oz", 16.0), "lbs": ("oz", 16.0),
              "pound": ("oz", 16.0), "pounds": ("oz", 16.0), "kg": ("oz", 35.274), "ml": ("ml", 1.0),
              "gal": ("ml", 3785.0), "gallon": ("ml", 3785.0), "gallons": ("ml", 3785.0)}
TOL = 0.08          # 8% difference still counts as the same size
MIXED_FRACTION = re.compile(r"(?<![\d/.])(\d+)(?:\s*-\s*|\s+)(\d+)/(\d+)(?![\d/])")
SIMPLE_FRACTION = re.compile(r"(?<![\d/.-])(\d+)/(\d+)(?![\d/])(?=\s*(?:\"|”|in\b|inch|inches|cm\b|mm\b|ft\b|feet|yd\b|yard|oz\b|lb\b|[x×*]))")


def _num(x: float) -> str:
    return ("%.4f" % x).rstrip("0").rstrip(".")


def stem(w: str) -> str:
    """Tiny plural stripper: candles -> candle, boxes -> box, berries -> berry."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ches", "shes", "xes", "zes", "sses")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


class Model:
    """Word weights (from the whole catalog) and the synonym groups."""

    def __init__(self, df: Counter | None = None, n_docs: int = 0, synonyms: list[str] | None = None):
        self.df = df or Counter()
        self.n = n_docs
        self.canon: dict[str, str] = {}
        for line in (synonyms if synonyms is not None else DEFAULT_SYNONYMS):
            words = [stem(w) for w in re.findall(r"[a-z0-9]+", line.lower())]
            if len(words) > 1:
                for w in words[1:]:
                    self.canon[w] = words[0]

    def word(self, w: str) -> str:
        w = stem(w)
        return self.canon.get(w, w)

    def weight(self, w: str) -> float:
        if not self.n:
            return 1.0
        # rarer words say more about which item it is; the square root keeps one rare word from outweighing
        # all the plain words that describe the same product
        return math.sqrt(math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 1.0)


MODEL = Model()


def set_model(m: Model) -> None:
    global MODEL
    MODEL = m


# ------------------------------------------------------------------ reading a title
def _fmt(x: float) -> str:
    return ("%g" % round(x, 2))


class Fingerprint:
    def __init__(self, text: str | None, model: Model):
        t = (text or "").lower().replace("…", " ").replace("&", " and ").replace("’", "'").replace("''", '"')
        t = re.sub(r"\.{3,}\s*$", " ", t)
        t = MIXED_FRACTION.sub(lambda m: _num(int(m.group(1)) + int(m.group(2)) / max(int(m.group(3)), 1)), t)   # 1-1/2" -> 1.5"
        t = SIMPLE_FRACTION.sub(lambda m: _num(int(m.group(1)) / max(int(m.group(2)), 1)), t)                    # 3/4 inch -> 0.75 inch
        self.truncated = bool(TRUNCATED.search(text or ""))
        self.packs: set[int] = set()
        self.dims: list[tuple[list[float], str]] = []
        self.sizes: dict[str, list[float]] = {}
        self.lens: list[float] = []              # every length with a unit, in inches (from "1.5\" x 100 yards" style dimensions)
        # facts first; cut them out of the text so their numbers don't count as words
        for rx in PACK_RES:
            for m in rx.finditer(t):
                if 0 < int(m.group(1)) < 100000:
                    self.packs.add(int(m.group(1)))
        # a name cut off mid-number ("17 Inch X 3...") must not be read as a real measurement
        cut = lambda m, s: self.truncated and m.end() >= len(s.rstrip()) - 1
        end = t
        def dim(m):
            if cut(m, end):
                return " "
            nums = [m.group(k) for k in ("n1", "n2", "n3") if m.group(k)]
            units = [m.group(k) for k in ("u1", "u2", "u3")][:len(nums)]
            last = next((u for u in reversed(units) if u), "")        # a trailing unit applies to numbers without their own
            vals = [float(v) for v in nums]
            self.dims.append((sorted(vals), last))
            for v, u in zip(vals, units):
                k = TO_INCH.get(u or last)
                if k is not None and (u or last):
                    self.lens.append(v * k)
            return " "
        t = DIM_RE.sub(dim, t)
        end = t
        def size(m):
            unit = m.group(2)
            if cut(m, end):
                return " "
            if unit in SIZE_CLASS:
                cls, k = SIZE_CLASS[unit]
                self.sizes.setdefault(cls, []).append(float(m.group(1)) * k)
            else:
                self.sizes.setdefault("len", []).append(float(m.group(1)) * TO_INCH.get(unit, 1.0))
            return " "
        t = SIZE_RE.sub(size, t)
        for rx in PACK_RES:
            t = rx.sub(" ", t)
        raw = [w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP and (len(w) > 1 or w.isdigit())]
        raw = [w for w in raw if not (w.isdigit() and len(w) == 1)]
        self.words = [model.word(w) for w in raw]
        self.set = set(self.words)
        self.joined = {self.words[i] + self.words[i + 1] for i in range(len(self.words) - 1)}   # tie + dye -> tiedye


def _dim_inches(vals, unit):
    k = TO_INCH.get(unit, None)
    return [v * k for v in vals] if k is not None else None


def _close(x: float, y: float) -> bool:
    return abs(x - y) <= TOL * max(x, y, 1e-9)


def _contained(small: list[float], big: list[float]) -> bool:
    """Every value of `small` matches a different value of `big` (a typo that drops one number still agrees)."""
    rest = list(big)
    for v in small:
        hit = next((i for i, w in enumerate(rest) if _close(v, w)), None)
        if hit is None:
            return False
        rest.pop(hit)
    return True


def _same_dim(a, b) -> bool:
    (va, ua), (vb, ub) = a, b
    small, big = (va, vb) if len(va) <= len(vb) else (vb, va)
    if len(small) < 2:
        return False
    if _contained(small, big):                                    # same numbers (units equal or not stated)
        return True
    ia, ib = _dim_inches(va, ua or "in"), _dim_inches(vb, ub or "in")
    if not (ia and ib):
        return False
    return _contained(*((ia, ib) if len(ia) <= len(ib) else (ib, ia)))


def _conflict(a: Fingerprint, b: Fingerprint) -> str:
    """A plain-English reason if the stated facts disagree, else ''."""
    if a.packs and b.packs and not (a.packs & b.packs):
        return (f"Pack size differs: catalog says {', '.join(map(str, sorted(a.packs)))}, "
                f"Amazon says {', '.join(map(str, sorted(b.packs)))}.")
    if a.dims and b.dims and not any(_same_dim(x, y) for x in a.dims for y in b.dims):
        show = lambda ds: " or ".join(f"{'×'.join(_fmt(v) for v in vals)}{(' ' + u) if u else ''}" for vals, u in ds[:3])
        return f"Size differs: catalog says {show(a.dims)}, Amazon says {show(b.dims)}."
    for cls, va in a.sizes.items():
        vb = b.sizes.get(cls)
        if vb and not any(abs(x - y) <= TOL * max(x, y, 1e-9) for x in va for y in vb):
            unit = {"len": "in", "oz": "oz", "ml": "ml"}[cls]
            return (f"Size differs: catalog says {', '.join(_fmt(v) for v in va[:3])} {unit}, "
                    f"Amazon says {', '.join(_fmt(v) for v in vb[:3])} {unit}.")
    # one title writes "1.5 inch wide x 100 yards", the other "1.5\" x 100 yards": compare all their lengths together
    la, lb = _all_lengths(a), _all_lengths(b)
    if la and lb and not any(_close(x, y) for x in la for y in lb):
        return (f"Size differs: catalog says {', '.join(_fmt(v) for v in la[:3])} in, "
                f"Amazon says {', '.join(_fmt(v) for v in lb[:3])} in.")
    return ""


def _all_lengths(f: Fingerprint) -> list[float]:
    return f.sizes.get("len", []) + f.lens


def _agreement(a: Fingerprint, b: Fingerprint) -> int:
    """Points for facts both titles state and agree on: a matching size or pack count is evidence of the same item."""
    pts = 6 if (a.packs & b.packs) else 0
    if a.dims and b.dims and any(_same_dim(x, y) for x in a.dims for y in b.dims):
        pts += 12
    for cls, va in a.sizes.items():
        if cls == "len":
            continue
        vb = b.sizes.get(cls)
        if vb and any(_close(x, y) for x in va for y in vb):
            pts += 12
    la, lb = _all_lengths(a), _all_lengths(b)
    if la and lb and any(_close(x, y) for x in la for y in lb):
        pts += 12
    return min(pts, 18)


def _recall(a: Fingerprint, b: Fingerprint, model: Model) -> tuple[float, list[str]]:
    """How much of `a` (weighted by how identifying each word is) is found in `b`."""
    words = list(a.words)
    if a.truncated and len(words) > 1:                     # the last word of a cut-off title may be incomplete
        words = words[:-1]
    if not words:
        return -1.0, []
    total = got = 0.0
    missing: list[tuple[float, str]] = []
    amazon_words = list(b.set)
    for i, w in enumerate(words):
        wt = model.weight(w) * (1.4 if i == 0 else 1.0)    # the first word is usually the brand
        total += wt
        if w in b.set or w in b.joined:
            got += wt
        elif i + 1 < len(words) and (w + words[i + 1]) in b.set:      # pillow + case == pillowcase
            got += wt
        elif i > 0 and (words[i - 1] + w) in b.set:
            got += wt
        elif len(w) >= 5 and any(x[:2] == w[:2] and abs(len(x) - len(w)) <= 1 and
                                 difflib.SequenceMatcher(None, w, x).ratio() >= 0.86 for x in amazon_words):
            got += 0.8 * wt                                                  # a typo or a spelling variant
        else:
            missing.append((wt, w))
    missing.sort(reverse=True)
    return got / total, [w for _, w in missing]


# ------------------------------------------------------------------ the checks
def check(catalog: str | None, amazon: str | None, model: Model | None = None) -> tuple[int | None, str, int]:
    """(score 0-100, note, conflict 0/1). (None, '', 0) when there is nothing to compare.
    conflict = 1 means the listing really changed (pack count or size no longer agrees)."""
    model = model or MODEL
    if not (amazon or "").strip() or not (catalog or "").strip():
        return None, "", 0
    a, b = Fingerprint(catalog, model), Fingerprint(amazon, model)
    fwd, missing = _recall(a, b, model)          # catalog words found on Amazon (Amazon reworded or added keywords)
    back, extra = _recall(b, a, model)           # Amazon words found in the catalog name (Amazon trimmed a long title)
    reason = _conflict(a, b)
    if fwd < 0 and not reason:
        return None, "", 0
    ratio = max(fwd, 0.92 * back if len(b.words) >= 4 else 0.0, 0.0)
    score = round(100 * ratio)
    if reason:
        return min(score, 40), reason, 1
    score = min(100, score + _agreement(a, b))
    if score >= 100 or not (missing or extra):
        return score, "", 0
    parts = []
    if missing:
        parts.append("Key words not on Amazon: " + ", ".join(missing[:5]))
    if extra and score < 70:
        parts.append("New on Amazon: " + ", ".join(extra[:4]))
    return score, " · ".join(parts), 0


def same_listing(earlier: str | None, later: str | None, threshold: float = 55, model: Model | None = None) -> bool:
    """True when two Amazon titles are the same listing reworded (no conflicting facts, enough shared words)."""
    model = model or MODEL
    if not (earlier or "").strip() or not (later or "").strip():
        return False
    s1, _, c1 = check(earlier, later, model)
    s2, _, c2 = check(later, earlier, model)
    return not (c1 or c2) and max(s1 or 0, s2 or 0) >= threshold

