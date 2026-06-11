from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple, Optional
from flask import Flask, request, jsonify
import unicodedata
import time
import random

# =========================
# 辞書（dictionary.py 方式）
# =========================

try:
    from dictionary import COUNTRY, CAPITAL, CUSTOM
except ImportError:
    COUNTRY, CAPITAL, CUSTOM = [], [], []

app = Flask(__name__)

# =========================
# 正規化
# =========================

SMALL_MAP = {
    "ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ",
    "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ッ": "ツ",
    "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お",
    "ゃ": "や", "ゅ": "ゆ", "ょ": "よ", "っ": "つ",
}

def normalize_kana(s: str,
                   unify_small: bool,
                   allow_daku: bool,
                   allow_handaku: bool) -> str:
    s = unicodedata.normalize("NFKC", s)
    res = []
    for ch in s:
        base = ch
        if unify_small and ch in SMALL_MAP:
            base = SMALL_MAP[ch]

        if not allow_daku or not allow_handaku:
            decomp = unicodedata.normalize("NFD", base)
            base_char = ""
            for c in decomp:
                if not unicodedata.combining(c):
                    base_char = c
            base = base_char

        res.append(base)
    return "".join(res)

# =========================
# 50音ずらし / 物理ずらし
# =========================

GOJUON_ROWS = [
    "アイウエオ",
    "カキクケコ",
    "サシスセソ",
    "タチツテト",
    "ナニヌネノ",
    "ハヒフヘホ",
    "マミムメモ",
    "ヤユヨ",
    "ラリルレロ",
    "ワヲン",
]

PHYS_MAP = {ch: (r, c) for r, row in enumerate(GOJUON_ROWS) for c, ch in enumerate(row)}

def shift_kana(ch: str, ks_abs: int, mode: str) -> List[str]:
    for row in GOJUON_ROWS:
        if ch in row:
            idx = row.index(ch)
            res = []
            if mode == "abs":
                for d in (-ks_abs, ks_abs):
                    ni = idx + d
                    if 0 <= ni < len(row):
                        res.append(row[ni])
            else:
                ni = idx + ks_abs
                if 0 <= ni < len(row):
                    res.append(row[ni])
            return list(dict.fromkeys(res))
    return []

def physical_shift(ch: str, offset: int) -> List[str]:
    if offset == 0 or ch not in PHYS_MAP:
        return [ch]
    r, c = PHYS_MAP[ch]
    row = GOJUON_ROWS[r]
    nc = c + offset
    if 0 <= nc < len(row):
        return [row[nc]]
    return [ch]

# =========================
# 接続判定
# =========================

def first_char(w: str) -> str:
    return w[0] if w else ""

def last_char(w: str) -> str:
    return w[-1] if w else ""

def can_connect(prev: str,
                nxt: str,
                use_shift: bool,
                ks_abs: int,
                shift_mode: str,
                pos_shift: int,
                round_trip: bool,
                auto_recovery: bool) -> bool:

    if not prev:
        return True

    lc = last_char(prev)
    fc = first_char(nxt)

    if round_trip and lc == fc:
        return True

    if use_shift:
        if fc in shift_kana(lc, ks_abs, shift_mode):
            return True

    if pos_shift != 0:
        if fc in physical_shift(lc, pos_shift):
            return True

    if auto_recovery and len(prev) >= 2:
        if prev[-2] == fc:
            return True

    return lc == fc

# =========================
# 必須文字
# =========================

@dataclass
class MustCharRule:
    char: str
    count: int

def parse_must_chars(s: str) -> List[MustCharRule]:
    if not s:
        return []
    res = []
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            ch, n = token.split(":", 1)
        elif "=" in token:
            ch, n = token.split("=", 1)
        else:
            ch, n = token, "1"
        res.append(MustCharRule(ch.strip(), int(n)))
    return res

def check_must_chars(route: List[str], rules: List[MustCharRule]) -> bool:
    joined = "".join(route)
    for r in rules:
        if joined.count(r.char) < r.count:
            return False
    return True

# =========================
# 文字制約
# =========================

def check_valid_chars(route: List[str],
                      valid_chars: str,
                      exclude_chars: str,
                      char_limit_mode: bool) -> bool:

    joined = "".join(route)

    if valid_chars:
        for ch in joined:
            if ch not in valid_chars:
                return False

    if exclude_chars:
        for ch in joined:
            if ch in exclude_chars:
                return False

    if char_limit_mode:
        seen = set()
        for ch in joined:
            if ch in seen:
                return False
            seen.add(ch)

    return True

# =========================
# 共役排除
# =========================

def normalize_for_conjugate(w: str) -> str:
    for suf in ["共和国", "連邦", "王国", "国"]:
        if w.endswith(suf):
            return w[:-len(suf)]
    return w

# =========================
# DFS
# =========================

@dataclass
class SearchConfig:
    start_word: str
    start_char: str
    end_char: str
    end_word: str
    all_start_char: str
    all_end_char: str
    must_rules: List[MustCharRule]
    target_total_len: Optional[int]
    len_mode: str
    use_shift: bool
    ks_abs: int
    shift_mode: str
    pos_shift: int
    unify_small: bool
    allow_daku: bool
    allow_handaku: bool
    auto_recovery: bool
    round_trip: bool
    char_limit_mode: bool
    exclude_conjugate: bool
    anti_loop: bool
    timeout: int
    timeout_enabled: bool
    limit: int
    limit_enabled: bool
    display_mode: str
    realtime_counter: bool
    exact_limit: Optional[int]
    valid_chars: str
    exclude_chars: str
    ban_start_chars: str
    red_words: Set[str]
    blue_words: Set[str]

def filter_words(words: List[str], cfg: SearchConfig) -> List[str]:
    res = []
    for w in words:
        if cfg.ban_start_chars and first_char(w) in cfg.ban_start_chars:
            continue
        if cfg.all_start_char and first_char(w) != cfg.all_start_char:
            continue
        if cfg.all_end_char and last_char(w) != cfg.all_end_char:
            continue
        res.append(w)
    return res

def dfs_search(words: List[str], cfg: SearchConfig) -> List[List[str]]:
    start_time = time.time()
    routes: List[List[str]] = []

    by_first: Dict[str, List[str]] = {}
    for w in words:
        by_first.setdefault(first_char(w), []).append(w)

    def time_over() -> bool:
        return cfg.timeout_enabled and (time.time() - start_time) >= cfg.timeout

    def dfs(route: List[str], used: Set[str], conj_used: Set[str]):
        if cfg.limit_enabled and len(routes) >= cfg.limit:
            return
        if cfg.exact_limit is not None and len(routes) >= cfg.exact_limit:
            return
        if time_over():
            return

        if route:
            ok = True
            if cfg.end_char and last_char(route[-1]) != cfg.end_char:
                ok = False
            if cfg.end_word and route[-1] != cfg.end_word:
                ok = False
            if cfg.must_rules and not check_must_chars(route, cfg.must_rules):
                ok = False
            if not check_valid_chars(route, cfg.valid_chars, cfg.exclude_chars, cfg.char_limit_mode):
                ok = False
            if ok:
                routes.append(route.copy())
                if cfg.exact_limit is not None and len(routes) >= cfg.exact_limit:
                    return

        last_w = route[-1] if route else cfg.start_word
        lc = last_char(last_w) if last_w else cfg.start_char

        if cfg.anti_loop and len(route) >= 2:
            if len(route[-1]) == 1 and len(route[-2]) == 1 and route[-1] == route[-2]:
                return

        cand: List[str] = []
        if lc in by_first:
            cand.extend(by_first[lc])

        if cfg.use_shift:
            for c in shift_kana(lc, cfg.ks_abs, cfg.shift_mode):
                cand.extend(by_first.get(c, []))

        if cfg.pos_shift != 0:
            for c in physical_shift(lc, cfg.pos_shift):
                cand.extend(by_first.get(c, []))

        cand = list(dict.fromkeys(cand))

        for w in cand:
            if w in used:
                continue
            if w in cfg.red_words:
                continue

            key = normalize_for_conjugate(w)
            if cfg.exclude_conjugate and key in conj_used:
                continue

            used.add(w)
            added = None
            if cfg.exclude_conjugate:
                conj_used.add(key)
                added = key

            route.append(w)
            dfs(route, used, conj_used)
            route.pop()
            used.remove(w)
            if added:
                conj_used.remove(added)

    if cfg.start_word:
        if cfg.start_word in words:
            dfs([cfg.start_word], {cfg.start_word}, set())
        else:
            dfs([cfg.start_word], set(), set())
        return routes

    starts = [w for w in words if first_char(w) == cfg.start_char] if cfg.start_char else words

    for w in starts:
        used = {w}
        conj_used: Set[str] = set()
        if cfg.exclude_conjugate:
            conj_used.add(normalize_for_conjugate(w))
        dfs([w], used, conj_used)

    return routes

# =========================
# /search
# =========================

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json(force=True)

    start_word = data.get("start_word", "")
    start_char = data.get("start_char", "")
    must_char = data.get("must_char", "")
    end_char = data.get("end_char", "")
    end_word = data.get("end_word", "")
    all_start_char = data.get("all_start_char", "")
    all_end_char = data.get("all_end_char", "")
    valid_chars = data.get("valid_chars", "")
    exclude_chars = data.get("exclude_chars", "")
    ban_start_chars = data.get("ban_start_chars", "")
    target_total_len = data.get("target_total_len", None)
    len_mode = data.get("len_mode", "free")
    sort_mode = data.get("sort_mode", "kana")
    use_shift = data.get("use_shift", False)
    ks_abs = int(data.get("ks_abs", 1))
    shift_mode = data.get("shift_mode", "abs")
    pos_shift = int(data.get("pos_shift", 0))
    unify_small = data.get("unify_small", False)
    allow_daku = data.get("allow_daku", False)
    allow_handaku = data.get("allow_handaku", False)
    auto_recovery = data.get("auto_recovery", False)
    round_trip = data.get("round_trip", False)
    char_limit_mode = data.get("char_limit_mode", False)
    exclude_conjugate = data.get("exclude_conjugate", False)
    anti_loop = data.get("anti_loop", False)
    timeout = int(data.get("timeout", 15))
    timeout_enabled = data.get("timeout_enabled", True)
    limit = int(data.get("limit", 1500))
    limit_enabled = data.get("limit_enabled", True)
    display_mode = data.get("display_mode", "normal")
    realtime_counter = data.get("realtime_counter", False)

    exact_limit_raw = data.get("exact_limit", None)
    exact_limit = None
    if exact_limit_raw:
        try:
            v = int(exact_limit_raw)
            if v > 0:
                exact_limit = v
        except:
            pass

    categories = data.get("categories", ["country", "capital"])
    red_words = set(data.get("red_words", []))
    blue_words = set(data.get("blue_words", []))

    words: List[str] = []
    if "country" in categories:
        words.extend(COUNTRY)
    if "capital" in categories:
        words.extend(CAPITAL)
    if "custom" in categories:
        words.extend(CUSTOM)

    def norm(s: str) -> str:
        return normalize_kana(s, unify_small, allow_daku, allow_handaku) if s else ""

    words = [norm(w) for w in words]
    start_word = norm(start_word)
    end_word = norm(end_word)
    start_char = norm(start_char)
    end_char = norm(end_char)
    all_start_char = norm(all_start_char)
    all_end_char = norm(all_end_char)
    valid_chars = norm(valid_chars)
    exclude_chars = norm(exclude_chars)
    ban_start_chars = norm(ban_start_chars)
    must_rules = parse_must_chars(norm(must_char))

    cfg = SearchConfig(
        start_word=start_word,
        start_char=start_char,
        end_char=end_char,
        end_word=end_word,
        all_start_char=all_start_char,
        all_end_char=all_end_char,
        must_rules=must_rules,
        target_total_len=target_total_len,
        len_mode=len_mode,
        use_shift=use_shift,
        ks_abs=ks_abs,
        shift_mode=shift_mode,
        pos_shift=pos_shift,
        unify_small=unify_small,
        allow_daku=allow_daku,
        allow_handaku=allow_handaku,
        auto_recovery=auto_recovery,
        round_trip=round_trip,
        char_limit_mode=char_limit_mode,
        exclude_conjugate=exclude_conjugate,
        anti_loop=anti_loop,
        timeout=timeout,
        timeout_enabled=timeout_enabled,
        limit=limit,
        limit_enabled=limit_enabled,
        display_mode=display_mode,
        realtime_counter=realtime_counter,
        exact_limit=exact_limit,
        valid_chars=valid_chars,
        exclude_chars=exclude_chars,
        ban_start_chars=ban_start_chars,
        red_words=red_words,
        blue_words=blue_words,
    )

    words = filter_words(words, cfg)
    routes = dfs_search(words, cfg)

    if sort_mode == "len_asc":
        routes.sort(key=lambda r: len("".join(r)))
    elif sort_mode == "len_desc":
        routes.sort(key=lambda r: -len("".join(r)))
    elif sort_mode == "random":
        random.shuffle(routes)
    else:
        routes.sort(key=lambda r: r[0] if r else "")

    if limit_enabled and len(routes) > limit:
        routes = routes[:limit]
    if exact_limit is not None and len(routes) > exact_limit:
        routes = routes[:exact_limit]

    return jsonify({"routes": routes, "count": len(routes)})

# =========================
# /get_dictionary
# =========================

@app.route("/get_dictionary")
def get_dictionary():
    return jsonify({
        "country": COUNTRY,
        "capital": CAPITAL,
        "custom": CUSTOM
    })

# =========================
# root
# =========================

@app.route("/")
def index():
    return "ULTRA ENGINE Pro backend is running."

if __name__ == "__main__":
    app.run(debug=True)
