from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple, Optional
from flask import Flask, request, jsonify
import itertools
import time
import unicodedata
import json
import random
import os

app = Flask(__name__)

# =========================
# 辞書ロード
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DICT_PATH = os.path.join(BASE_DIR, "dictionary.json")

with open(DICT_PATH, "r", encoding="utf-8") as f:
    RAW_DICT = json.load(f)

COUNTRY_WORDS: List[str] = RAW_DICT.get("country", [])
CAPITAL_WORDS: List[str] = RAW_DICT.get("capital", [])
CUSTOM_WORDS: List[str] = RAW_DICT.get("custom", [])

# =========================
# 正規化系
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
    # NFKC
    s = unicodedata.normalize("NFKC", s)

    res = []
    for ch in s:
        base = ch
        # 小文字→大文字
        if unify_small and ch in SMALL_MAP:
            base = SMALL_MAP[ch]

        # 濁点・半濁点の扱い
        if not allow_daku or not allow_handaku:
            decomp = unicodedata.normalize("NFD", base)
            base_char = ""
            marks = []
            for c in decomp:
                if unicodedata.combining(c):
                    marks.append(c)
                else:
                    base_char = c
            # 濁点・半濁点を落とす
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

def build_physical_map() -> Dict[str, Tuple[int, int]]:
    mp = {}
    for r, row in enumerate(GOJUON_ROWS):
        for c, ch in enumerate(row):
            mp[ch] = (r, c)
    return mp

PHYS_MAP = build_physical_map()

def shift_kana(ch: str, ks_abs: int, mode: str) -> List[str]:
    # mode: "abs" or "rel"
    # abs: ±ks_abs の2通り
    # rel: +ks_abs のみ
    res = []
    for row in GOJUON_ROWS:
        if ch in row:
            idx = row.index(ch)
            if mode == "abs":
                for d in (-ks_abs, ks_abs):
                    ni = idx + d
                    if 0 <= ni < len(row):
                        res.append(row[ni])
            else:
                ni = idx + ks_abs
                if 0 <= ni < len(row):
                    res.append(row[ni])
            break
    return list(dict.fromkeys(res))

def physical_shift(ch: str, offset: int) -> List[str]:
    if offset == 0:
        return [ch]
    if ch not in PHYS_MAP:
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

def last_char(word: str) -> str:
    return word[-1] if word else ""

def first_char(word: str) -> str:
    return word[0] if word else ""

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

    # 牛耕（往復）: lc == fc も許可
    if round_trip and lc == fc:
        return True

    # 50音ずらし
    if use_shift:
        cand = shift_kana(lc, ks_abs, shift_mode)
        if fc in cand:
            return True

    # 物理ずらし
    if pos_shift != 0:
        cand = physical_shift(lc, pos_shift)
        if fc in cand:
            return True

    # 遡り接続（auto_recovery）
    if auto_recovery:
        # 1文字戻って接続できるかを簡易実装
        if len(prev) >= 2:
            lc2 = prev[-2]
            if lc2 == fc:
                return True

    # 通常しりとり
    return lc == fc

# =========================
# 必須文字 / 文字制約
# =========================

@dataclass
class MustCharRule:
    char: str
    count: int

def parse_must_chars(s: str) -> List[MustCharRule]:
    """
    例:
      ア,イ:2,ウ=1,〇=7
    """
    if not s:
        return []
    res: List[MustCharRule] = []
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
        ch = ch.strip()
        try:
            cnt = int(n)
        except ValueError:
            cnt = 1
        res.append(MustCharRule(ch, cnt))
    return res

def check_must_chars(route: List[str], rules: List[MustCharRule]) -> bool:
    if not rules:
        return True
    joined = "".join(route)
    for r in rules:
        if joined.count(r.char) < r.count:
            return False
    return True

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
        # 重複禁止: 同じ文字を2回以上使わない
        seen: Set[str] = set()
        for ch in joined:
            if ch in seen:
                return False
            seen.add(ch)
    return True

# =========================
# 共役排除（超簡易版）
# =========================

def normalize_for_conjugate(w: str) -> str:
    # ここでは末尾の「国」「共和国」「連邦」などをざっくり落とす程度
    for suf in ["共和国", "連邦", "王国", "国"]:
        if w.endswith(suf):
            return w[:-len(suf)]
    return w

def build_conjugate_groups(words: List[str]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for w in words:
        key = normalize_for_conjugate(w)
        groups.setdefault(key, []).append(w)
    return groups

# =========================
# DFS 探索
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

def filter_words(words: List[str],
                 cfg: SearchConfig) -> List[str]:
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

    # 共役排除用
    conj_groups = build_conjugate_groups(words) if cfg.exclude_conjugate else {}

    # index
    by_first: Dict[str, List[str]] = {}
    for w in words:
        fc = first_char(w)
        by_first.setdefault(fc, []).append(w)

    def time_over() -> bool:
        if not cfg.timeout_enabled:
            return False
        return (time.time() - start_time) >= cfg.timeout

    def can_use(word: str, used: Set[str]) -> bool:
        if word in used:
            return False
        if cfg.red_words and word in cfg.red_words:
            return False
        return True

    def dfs(route: List[str],
            used: Set[str],
            conj_used: Set[str]):
        if cfg.limit_enabled and len(routes) >= cfg.limit:
            return
        if cfg.exact_limit is not None and len(routes) >= cfg.exact_limit:
            return
        if time_over():
            return

        # 途中判定
        if cfg.target_total_len is not None:
            total_len = len("".join(route))
            if cfg.len_mode == "strict":
                if total_len > cfg.target_total_len:
                    return
            # free の場合は超えてもOK

        # 終了条件
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
                # exact_limit がある場合はここで止める可能性
                if cfg.exact_limit is not None and len(routes) >= cfg.exact_limit:
                    return

        last_w = route[-1] if route else cfg.start_word
        lc = last_char(last_w) if last_w else cfg.start_char

        # 1文字ループ拒否
        if cfg.anti_loop and len(route) >= 2:
            if len(route[-1]) == 1 and len(route[-2]) == 1:
                if route[-1] == route[-2]:
                    return

        # 次候補
        cand_words: List[str] = []
        if lc in by_first:
            cand_words.extend(by_first[lc])

        # ずらし系
        if cfg.use_shift:
            for c in shift_kana(lc, cfg.ks_abs, cfg.shift_mode):
                cand_words.extend(by_first.get(c, []))
        if cfg.pos_shift != 0:
            for c in physical_shift(lc, cfg.pos_shift):
                cand_words.extend(by_first.get(c, []))

        # 重複削除
        cand_words = list(dict.fromkeys(cand_words))

        for w in cand_words:
            if not can_use(w, used):
                continue
            # 共役排除
            if cfg.exclude_conjugate:
                key = normalize_for_conjugate(w)
                if key in conj_used:
                    continue

            used.add(w)
            conj_added = None
            if cfg.exclude_conjugate:
                key = normalize_for_conjugate(w)
                conj_added = key
                conj_used.add(key)

            route.append(w)
            dfs(route, used, conj_used)

            route.pop()
            used.remove(w)
            if cfg.exclude_conjugate and conj_added is not None:
                conj_used.remove(conj_added)

    # 開始
    initial_words = words
    if cfg.start_word:
        if cfg.start_word not in words:
            # 開始単語が辞書にない場合は単独で route に入れて開始
            dfs([cfg.start_word], set(), set())
            return routes
        else:
            dfs([cfg.start_word], {cfg.start_word}, set())
            return routes

    # start_word が空の場合、start_char から始まる全単語を起点にする
    if cfg.start_char:
        starts = [w for w in initial_words if first_char(w) == cfg.start_char]
    else:
        starts = initial_words

    for w in starts:
        used = {w}
        conj_used: Set[str] = set()
        if cfg.exclude_conjugate:
            conj_used.add(normalize_for_conjugate(w))
        dfs([w], used, conj_used)

    return routes

# =========================
# /search API
# =========================

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json(force=True)

    start_word = data.get("start_word", "").strip()
    start_char = data.get("start_char", "").strip()
    must_char = data.get("must_char", "").strip()
    end_char = data.get("end_char", "").strip()
    end_word = data.get("end_word", "").strip()
    all_start_char = data.get("all_start_char", "").strip()
    all_end_char = data.get("all_end_char", "").strip()
    valid_chars = data.get("valid_chars", "").strip()
    exclude_chars = data.get("exclude_chars", "").strip()
    ban_start_chars = data.get("ban_start_chars", "").strip()
    target_total_len = data.get("target_total_len", None)
    len_mode = data.get("len_mode", "free")
    sort_mode = data.get("sort_mode", "kana")
    use_shift = bool(data.get("use_shift", False))
    ks_abs = int(data.get("ks_abs", 1) or 1)
    shift_mode = data.get("shift_mode", "abs")
    pos_shift = int(data.get("pos_shift", 0) or 0)
    unify_small = bool(data.get("unify_small", False))
    allow_daku = bool(data.get("allow_daku", False))
    allow_handaku = bool(data.get("allow_handaku", False))
    auto_recovery = bool(data.get("auto_recovery", False))
    round_trip = bool(data.get("round_trip", False))
    char_limit_mode = bool(data.get("char_limit_mode", False))
    exclude_conjugate = bool(data.get("exclude_conjugate", False))
    anti_loop = bool(data.get("anti_loop", False))

    timeout = int(data.get("timeout", 15) or 15)
    timeout_enabled = bool(data.get("timeout_enabled", True))
    limit = int(data.get("limit", 1500) or 1500)
    limit_enabled = bool(data.get("limit_enabled", True))
    display_mode = data.get("display_mode", "normal")
    realtime_counter = bool(data.get("realtime_counter", False))

    exact_limit_raw = data.get("exact_limit", None)
    exact_limit = None
    if exact_limit_raw is not None:
        try:
            v = int(exact_limit_raw)
            if v > 0:
                exact_limit = v
        except ValueError:
            exact_limit = None

    categories = data.get("categories", ["country", "capital"])
    red_words = set(data.get("red_words", []))
    blue_words = set(data.get("blue_words", []))

    # 辞書選択
    words: List[str] = []
    if "country" in categories:
        words.extend(COUNTRY_WORDS)
    if "capital" in categories:
        words.extend(CAPITAL_WORDS)
    if "custom" in categories:
        words.extend(CUSTOM_WORDS)

    # 正規化
    def norm(w: str) -> str:
        return normalize_kana(w, unify_small, allow_daku, allow_handaku)

    words = [norm(w) for w in words]
    start_word = norm(start_word) if start_word else ""
    end_word = norm(end_word) if end_word else ""
    start_char = norm(start_char) if start_char else ""
    end_char = norm(end_char) if end_char else ""
    all_start_char = norm(all_start_char) if all_start_char else ""
    all_end_char = norm(all_end_char) if all_end_char else ""
    valid_chars = norm(valid_chars) if valid_chars else ""
    exclude_chars = norm(exclude_chars) if exclude_chars else ""
    ban_start_chars = norm(ban_start_chars) if ban_start_chars else ""

    must_rules = parse_must_chars(norm(must_char) if must_char else "")

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

    # ソート
    if sort_mode == "len_asc":
        routes.sort(key=lambda r: len("".join(r)))
    elif sort_mode == "len_desc":
        routes.sort(key=lambda r: -len("".join(r)))
    elif sort_mode == "random":
        random.shuffle(routes)
    else:
        # kana: 先頭語の五十音順
        routes.sort(key=lambda r: r[0] if r else "")

    # limit / exact_limit は DFS 内で制御済みだが、念のため
    if limit_enabled and len(routes) > limit:
        routes = routes[:limit]
    if exact_limit is not None and len(routes) > exact_limit:
        routes = routes[:exact_limit]

    return jsonify({
        "routes": routes,
        "count": len(routes),
    })

# =========================
# /get_dictionary
# =========================

@app.route("/get_dictionary", methods=["GET"])
def get_dictionary():
    return jsonify({
        "country": COUNTRY_WORDS,
        "capital": CAPITAL_WORDS,
        "custom": CUSTOM_WORDS,
    })

# =========================
# root
# =========================

@app.route("/")
def index():
    return "ULTRA ENGINE Pro backend is running."

if __name__ == "__main__":
    app.run(debug=True)
