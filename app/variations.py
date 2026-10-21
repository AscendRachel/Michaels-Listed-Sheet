"""Is the variation Amazon shows the same one the catalog lists?

The two are never worded alike ("Large, 12pcs" in the catalog, "Color: 12pcs/Size: Medium" on Amazon), so words are
not compared one by one. The values are split into three kinds of fact and each kind is compared on its own:

  * sizes   S / M / L / XL ... (small = S, extra large = XL)
  * numbers counts and dimensions (12pcs = 12 pack = Number of Items: 12)
  * words   colours, styles, patterns (plurals and small typos count as the same word)

The variation differs when, for any kind, both sides state something and nothing agrees. If either side is blank, or a
kind appears on one side only, nothing is flagged: this only reports clear disagreement.
"""

from __future__ import annotations

import difflib
import re

from .titles import stem

LABEL = re.compile(r"[a-z][a-z' ]{0,40}:")                 # "Color:", "Number of Items:", "Size Name:"
SIZES = {"xxs": "xxs", "xs": "xs", "s": "s", "small": "s", "sm": "s", "m": "m", "med": "m", "medium": "m", "l": "l", "large": "l",
         "lg": "l", "xl": "xl", "xxl": "xxl", "xxxl": "xxxl", "xxxxl": "xxxxl"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "dozen": 12}
FILLER = set("""a an and the of with for to in on or by pack packs pcs pc piece pieces count ct set sets pk item items number
quantity size color colour style pattern inch inches in cm mm ft oz lb lbs x each per bag box roll rolls pair pairs
default standard regular other name type""".split())
SAME = {"grey": "gray", "multicolour": "multicolor", "multicoloured": "multicolor", "multicolored": "multicolor",
        "multi": "multicolor", "colourful": "multicolor", "colorful": "multicolor", "transparent": "clear"}


def parse(text: str | None) -> tuple[set[str], set[float], set[str]]:
    """(sizes, numbers, words) found in a variation text."""
    s = (text or "").lower().replace("’", "'").replace("×", "x")
    s = re.sub(r"(…|\.{3,})\s*$", "", s)                 # a cut-off value: drop the broken last word
    if "…" in (text or "") or (text or "").rstrip().endswith("..."):
        s = re.sub(r"[\s/,;|:][^\s/,;|:]*$", "", s)
    s = LABEL.sub(" ", s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", "", s)            # multi-colored -> multicolored
    s = re.sub(r"\b(x+)[\s-]*(small|large)\b", lambda m: m.group(1) + ("s" if m.group(2) == "small" else "l"), s)
    s = re.sub(r"\b(\d)\s*xl\b", lambda m: "x" * int(m.group(1)) + "l", s)
    sizes: set[str] = set()
    numbers: set[float] = set()
    words: set[str] = set()
    for tok in re.findall(r"\d+(?:\.\d+)?|[a-z]+", s):
        if tok[0].isdigit():
            numbers.add(float(tok))
        elif tok in SIZES:
            sizes.add(SIZES[tok])
        elif tok in NUMBER_WORDS:
            numbers.add(float(NUMBER_WORDS[tok]))
        elif tok not in FILLER and len(tok) > 1:
            t = stem(tok)
            words.add(SAME.get(t, t))
    return sizes, numbers, words


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if len(short) >= 4 and long.startswith(short):
        return True
    return len(short) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.84


def differs(catalog: str | None, amazon: str | None) -> bool:
    """True only when both variations say something and a kind of fact clearly disagrees."""
    if not (catalog or "").strip() or not (amazon or "").strip():
        return False
    cs, cn, cw = parse(catalog)
    asz, an, aw = parse(amazon)
    if cs and asz and not cs & asz:
        return True
    if cn and an:
        near = lambda x, ys: any(abs(x - y) <= 0.01 * max(abs(x), abs(y), 1) for y in ys)
        if not (all(near(x, an) for x in cn) or all(near(y, cn) for y in an)):
            return True
    if cw and aw and not any(_same_word(x, y) for x in cw for y in aw):
        return True
    return False
