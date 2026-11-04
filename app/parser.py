"""Description parser (FS_database_item_extraction.md, Section 1).

Splits a product description such as ``Pepper Green Bell Diced 3/4" 4/5lb`` into
produce part, cut method, cut size/count, other descriptions and pack size, and
computes total lb from an ``a/b lb`` pack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# Ordered longest-first so multi-word methods win over their sub-words.
CUT_METHODS = [
    "Peeled & Diced", "Peeled & Sliced", "Peeled & Chunk", "Peeled & Wedged",
    "Peeled & Cubed", "Peeled & Quartered", "Whole Peeled", "Diagonal Cut", "Bias Cut",
    "Stew Cut", "Sliced Slab", "Slice Slab", "Sliced/Coins", "Half Moon", "Halfmoon",
    "Diced", "Dice", "Sliced", "Slice", "Wedged", "Wedges", "Wedge", "Matchstick",
    "Chopped", "Sticks", "Stick", "Coins", "Julienne", "Shredded", "Slivered", "Slab",
    "Chunks", "Chunk", "Quartered", "Halved", "Florets", "Floret", "Spears", "Section",
    "Cubed", "Strips", "Stalk", "Spiral", "Minced", "Rings", "Cored", "Trimmed",
]

# Canonical form used when *comparing* methods (not for display).
METHOD_CANON = {
    "dice": "diced", "slice": "sliced", "slab": "sliced", "sliced slab": "sliced",
    "slice slab": "sliced", "sliced/coins": "sliced", "coins": "sliced",
    "wedged": "wedge", "wedges": "wedge", "sticks": "stick", "chunks": "chunk",
    "florets": "floret", "halfmoon": "half moon", "peeled & wedged": "wedge",
}

OTHER_KEYWORDS = ["Skin-On", "Skin On", "Stack Pack", "Pack In Bag", "Hand Cut", "In Water", "Tray"]

# Word-order canonicalisation (yellow onion -> onion yellow, ...)
WORD_ORDER = {
    "yellow onion": "onion yellow", "red onion": "onion red", "white onion": "onion white",
    "green onion": "onion green", "sweet potato": "potato sweet", "roma tomato": "tomato roma",
    "snap pea": "pea snap", "y. onion": "onion yellow",
}

STOP_TOKENS = {"&", "-", "/", "w/", "with", "and", "the", "of", "bag", "bags"}

PACK_RE = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(lb|lbs|#)\.?\s*\)?", re.I)
PACK_ALT_RE = re.compile(r"(\d+\s*LB\s*TRAY\*?|\d+\s*ct\b|\d+\s*oz\b)", re.I)
SIZE_RE = re.compile(r'(\d+\s+\d+/\d+"|\d+/\d+"|\d+(?:\.\d+)?"|\d+\s*Cut\b|\d+\s*ct\b)', re.I)


def normalize_quotes(text: str) -> str:
    """Unify inch marks: '' ’’ ” “ and a lone ' after a digit all become "."""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("’’", '"').replace("''", '"').replace("”", '"').replace("“", '"').replace("’", "'")
    s = re.sub(r"(\d)\s*'(?!')", r'\1"', s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_size(size: str) -> str:
    return re.sub(r"\s+", "", normalize_quotes(size or "")).lower()


def canon_method(method: str) -> str:
    m = (method or "").strip().lower()
    return METHOD_CANON.get(m, m)


@dataclass
class Parsed:
    raw: str = ""
    produce: str = ""
    cut_method: str = ""
    cut_size: str = ""
    other: str = ""
    pack: str = ""
    pack_inner: float | None = None
    pack_per: float | None = None
    total_lb: float | None = None
    tokens: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _num(x: str) -> float:
    f = float(x)
    return int(f) if f.is_integer() else f


def parse_description(desc: str) -> Parsed:
    p = Parsed(raw=desc or "")
    s = normalize_quotes(desc)

    # ---- pack size (L): last a/b lb token, else ct / tray tokens
    packs = list(PACK_RE.finditer(s))
    if packs:
        m = packs[-1]
        p.pack_inner, p.pack_per = _num(m.group(1)), _num(m.group(2))
        p.pack = f"{p.pack_inner}/{p.pack_per}lb"
        p.total_lb = _num(p.pack_inner * p.pack_per)
        s = (s[: m.start()] + " " + s[m.end():]).strip()
    else:
        m = None
        for m in PACK_ALT_RE.finditer(s):
            pass
        if m:
            p.pack = m.group(1).strip()
            s = (s[: m.start()] + " " + s[m.end():]).strip()

    # ---- other descriptions (K): bracket text + keywords
    others = []
    for br in re.findall(r"\(([^)]*)\)", s):
        others.append(br.strip())
    s = re.sub(r"\([^)]*\)", " ", s)
    for kw in OTHER_KEYWORDS:
        rx = re.compile(r"\b" + re.escape(kw) + r"\b", re.I)
        if rx.search(s):
            others.append(kw)
            s = rx.sub(" ", s)
    p.other = ", ".join(o for o in others if o)

    # ---- cut method (I)
    for meth in CUT_METHODS:
        rx = re.compile(r"(?<![A-Za-z])" + re.escape(meth) + r"(?![A-Za-z])", re.I)
        if rx.search(s):
            p.cut_method = meth
            s = rx.sub(" ", s, count=1)
            break

    # ---- cut size / count (J)
    m = SIZE_RE.search(s)
    if m:
        p.cut_size = re.sub(r"\s+", " ", m.group(1)).strip()
        s = (s[: m.start()] + " " + s[m.end():]).strip()

    # ---- produce part = what is left
    produce = re.sub(r"\s+", " ", s).strip(" -,*")
    p.produce = produce
    p.tokens = produce_tokens(produce)
    return p


def produce_tokens(produce: str) -> list[str]:
    s = " " + normalize_quotes(produce).lower() + " "
    for a, b in WORD_ORDER.items():
        s = s.replace(" " + a + " ", " " + b + " ")
    s = s.replace("(", " ").replace(")", " ").replace(",", " ")
    toks = []
    for t in s.split():
        t = t.strip(".-*")
        if not t or t in STOP_TOKENS:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss") and not re.search(r"\d", t):
            t = t[:-1]  # crude singular
        toks.append(t)
    return toks


def format_cut_size_and_method(cut_size: str, cut_method: str) -> str:
    """BC 'Cut Size and Method' style: size + METHOD in caps, e.g. 3/8" DICED."""
    size = normalize_quotes(cut_size or "").upper()
    meth = (cut_method or "").strip().upper()
    return " ".join(x for x in (size, meth) if x)


def pack_to_bc(pack_inner, pack_per) -> tuple[str, str]:
    if pack_inner is None or pack_per is None:
        return "", ""
    return f"{_fmt(pack_inner)}BAG", f"{_fmt(pack_per)}LB"


def _fmt(x) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return str(int(f)) if f.is_integer() else str(f)


def parse_bc_pack(inner_cell: str, per_cell: str) -> tuple[float | None, float | None]:
    def first_num(v):
        m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
        return float(m.group(0)) if m else None
    return first_num(inner_cell), first_num(per_cell)
