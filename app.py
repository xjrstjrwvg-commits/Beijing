import os
import sys
import time
import re
import random
from collections import defaultdict
from flask import Flask, render_template, request, jsonify

# --- 辞書読み込み ---
try:
    from dictionary import DICTIONARY_MASTER
except ImportError:
    DICTIONARY_MASTER = {
        "country": ["ニホン", "アメリカ", "イギリス"],
        "capital": ["トウキョウ", "ワシントン", "ロンドン"],
        "custom": []
    }

sys.setrecursionlimit(10000)
app = Flask(__name__)

# --- かな関連ユーティリティ ---
KANA_LIST = (
    "アイウエオ"
    "カキクケコガギグゲゴ"
    "サシスセソザジズゼゾ"
    "タチツテトダヂヅデド"
    "ナニヌネノ"
    "ハヒフヘホバビブベボパピプペポ"
    "マミムメモ"
    "ヤユヨ"
    "ラリルレロ"
    "ワヲン"
)

SMALL_TO_LARGE = {
    "ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ",
    "ッ": "ツ", "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ"
}

DAKU_MAP = {
    "カ": "ガ", "キ": "ギ", "ク": "グ", "ケ": "ゲ", "コ": "ゴ",
    "サ": "ザ", "シ": "ジ", "ス": "ズ", "セ": "ゼ", "ソ": "ゾ",
    "タ": "ダ", "チ": "ヂ", "ツ": "ヅ", "テ": "デ", "ト": "ド",
    "ハ": "バ", "ヒ": "ビ", "フ": "ブ", "ヘ": "ベ", "ホ": "ボ"
}

HANDAKU_MAP = {
    "ハ": "パ", "ヒ": "ピ", "フ": "プ", "ヘ": "ペ", "ホ": "ポ"
}

REV_DAKU = {v: k for k, v in DAKU_MAP.items()}
REV_HANDAKU = {v: k for k, v in HANDAKU_MAP.items()}


def to_katakana(text: str) -> str:
    if not text:
        return ""
    return "".join(
        chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c
        for c in text
    )


def get_base_char(c: str, unify_small=False, unify_daku=False, unify_handaku=False) -> str:
    res = SMALL_TO_LARGE.get(c, c) if unify_small else c
    if unify_daku:
        res = REV_DAKU.get(res, res)
    if unify_handaku:
        res = REV_HANDAKU.get(res, res)
    return res


def get_clean_char(w: str, pos="head", offset=0,
                   unify_small=False, unify_daku=False, unify_handaku=False) -> str:
    text = w.replace("ー", "")
    if not text:
        return ""
    try:
        idx = offset if pos == "head" else -(1 + offset)
        return get_base_char(text[idx], unify_small, unify_daku, unify_handaku)
    except Exception:
        return ""


def shift_kana(c: str, n: int) -> str:
    if c not in KANA_LIST:
        return c
    return KANA_LIST[(KANA_LIST.index(c) + n) % len(KANA_LIST)]


def get_variants(c: str, allow_daku: bool, allow_handaku: bool, unify_small=False):
    base = SMALL_TO_LARGE.get(c, c) if unify_small else c
    s = {base}
    if allow_daku:
        for k, v in DAKU_MAP.items():
            if base == k:
                s.add(v)
            if base == v:
                s.add(k)
    if allow_handaku:
        for k, v in HANDAKU_MAP.items():
            if base == k:
                s.add(v)
            if base == v:
                s.add(k)
    return s


# --- ルート ---
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/get_dictionary")
def get_dictionary():
    return jsonify(DICTIONARY_MASTER)


# --- /search メイン ---
@app.route("/search", methods=["POST"])
def search():
    d = request.json or {}

    # --- 基本パラメータ ---
    max_len = int(d.get("max_len", 5))  # 語数
    pos_shift = int(d.get("pos_shift", 0))
    use_shift = bool(d.get("use_shift", False))
    ks_abs = int(d.get("ks_abs", 1))
    shift_mode = d.get("shift_mode", "abs")

    unify_small = bool(d.get("unify_small", False))
    allow_daku = bool(d.get("allow_daku", False))
    allow_handaku = bool(d.get("allow_handaku", False))
    unify_scope = d.get("unify_scope", "all")  # all / conn / filter

    len_mode = d.get("len_mode", "free")  # free / same / diff
    sort_mode = d.get("sort_mode", "default")

    # 合計文字数（ttl）
    target_total_len = d.get("target_total_len") or d.get("ttl")
    if target_total_len not in (None, "", 0, "0"):
        target_total_len = int(target_total_len)
    else:
        target_total_len = None

    # タイムアウト / 件数制限
    timeout_enabled = bool(d.get("timeout_enabled", False))
    timeout_sec = float(d.get("timeout_sec", 15.0))
    limit_enabled = bool(d.get("limit_enabled", False))
    limit = int(d.get("limit", 0)) if d.get("limit") not in (None, "", 0, "0") else 0

    # 共役集約
    exclude_conjugate = bool(d.get("exclude_conjugate", False))

    # 統一スコープ
    conn_s = unify_small and unify_scope in ["all", "conn"]
    conn_d = allow_daku and unify_scope in ["all", "conn"]
    conn_h = allow_handaku and unify_scope in ["all", "conn"]

    filt_s = unify_small and unify_scope in ["all", "filter"]
    filt_d = allow_daku and unify_scope in ["all", "filter"]
    filt_h = allow_handaku and unify_scope in ["all", "filter"]

    # --- 入力 ---
    start_word = to_katakana(d.get("start_word", "")).strip()

    start_char = get_clean_char(
        to_katakana(d.get("start_char", "")), "head", 0, filt_s, filt_d, filt_h
    )
    end_char = get_clean_char(
        to_katakana(d.get("end_char", "")), "head", 0, filt_s, filt_d, filt_h
    )

    asc = [
        get_clean_char(c.strip(), "head", 0, filt_s, filt_d, filt_h)
        for c in re.split("[,、]", to_katakana(d.get("all_start_char", "")))
        if c.strip()
    ]
    aec = [
        get_clean_char(c.strip(), "head", 0, filt_s, filt_d, filt_h)
        for c in re.split("[,、]", to_katakana(d.get("all_end_char", "")))
        if c.strip()
    ]

    valid_chars_raw = to_katakana(d.get("valid_chars", "")).replace("、", "").replace(",", "")
    valid_chars = set(valid_chars_raw) if valid_chars_raw else None

    exclude_chars = [
        get_base_char(c.strip(), filt_s, filt_d, filt_h)
        for c in re.split("[,、]", to_katakana(d.get("exclude_chars", "")))
        if c.strip()
    ]

    ban_start_chars = [
        get_base_char(c.strip(), filt_s, filt_d, filt_h)
        for c in re.split("[,、]", to_katakana(d.get("ban_start_chars", "")))
        if c.strip()
    ]

    # 必須文字（: / = 管理）
    # 例: "ア,イ:2,ウ=1"
    must_specs = []
    mc_raw = to_katakana(d.get("must_char", ""))
    for token in re.split("[,、]", mc_raw):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            ch, n = token.split(":", 1)
            must_specs.append((get_base_char(ch.strip(), filt_s, filt_d, filt_h), ">=", int(n)))
        elif "=" in token:
            ch, n = token.split("=", 1)
            must_specs.append((get_base_char(ch.strip(), filt_s, filt_d, filt_h), "==", int(n)))
        else:
            # 単純に「1回以上」
            must_specs.append((get_base_char(token, filt_s, filt_d, filt_h), ">=", 1))

    # --- 辞書プール ---
    raw_pool = []
    for cat in d.get("categories", ["country"]):
        raw_pool.extend(DICTIONARY_MASTER.get(cat, []))
    raw_pool = list(set(raw_pool))

    # 赤/青
    red_words = set(d.get("red_words", []))
    blue_words = set(d.get("blue_words", []))

    # --- フィルタリング ---
    temp_pool = []
    for w in raw_pool:
        if w in red_words:
            continue

        w_k = to_katakana(w)
        h = get_clean_char(w_k, "head", 0, filt_s, filt_d, filt_h)
        t = get_clean_char(w_k, "tail", 0, filt_s, filt_d, filt_h)

        if asc and h not in asc:
            continue
        if aec and t not in aec:
            continue

        if valid_chars:
            if not all(
                get_base_char(c, filt_s, filt_d, filt_h) in valid_chars
                for c in w_k.replace("ー", "")
            ):
                continue

        norm_w = "".join(get_base_char(c, filt_s, filt_d, filt_h) for c in w_k)
        if any(ex in norm_w for ex in exclude_chars):
            continue
        if any(h == bs for bs in ban_start_chars):
            continue

        temp_pool.append(w_k)

    # --- 共役集約（exclude_conjugate） ---
    if exclude_conjugate:
        pair_map = defaultdict(list)
        for w in temp_pool:
            ch = get_clean_char(w, "head", 0, conn_s, conn_d, conn_h)
            ct = get_clean_char(w, "tail", 0, conn_s, conn_d, conn_h)
            key = f"{ch}_{ct}"
            pair_map[key].append(w)
        word_pool = [v[0] for v in pair_map.values()]
    else:
        word_pool = temp_pool

    # --- 接続インデックス ---
    head_index = defaultdict(list)
    tail_index = defaultdict(list)
    for w in word_pool:
        head_index[get_clean_char(w, "head", 0, conn_s, conn_d, conn_h)].append(w)
        tail_index[get_clean_char(w, "tail", 0, conn_s, conn_d, conn_h)].append(w)

    # --- 探索 ---
    results = []
    start_time = time.time()

    def timed_out():
        if not timeout_enabled:
            return False
        return (time.time() - start_time) > timeout_sec

    def limit_reached():
        if not limit_enabled or limit <= 0:
            return False
        return len(results) >= limit

    def solve(path, total_len):
        if timed_out() or limit_reached():
            return

        if len(path) == max_len:
            # 文字数構成
            lens = {len(x) for x in path}
            if len_mode == "same" and len(lens) > 1:
                return
            if len_mode == "diff" and len(lens) != len(path):
                return

            # 青単語必須
            if not blue_words.issubset(set(path)):
                return

            # 合計文字数
            if target_total_len is not None and total_len != target_total_len:
                return

            # 終了文字
            if end_char:
                last_t = get_clean_char(path[-1], "tail", 0, conn_s, conn_d, conn_h)
                if last_t not in get_variants(end_char, allow_daku, allow_handaku, conn_s):
                    return

            # 必須文字（: / =）
            joined = "".join(path)
            norm_join = "".join(get_base_char(c, filt_s, filt_d, filt_h) for c in joined)
            for ch, op, n in must_specs:
                cnt = norm_join.count(ch)
                if op == ">=" and cnt < n:
                    return
                if op == "==" and cnt != n:
                    return

            results.append(path.copy())
            return

        last = path[-1]
        last_clean = last.replace("ー", "")
        is_odd = (len(path) % 2 != 0)

        offsets = [pos_shift]
        if d.get("auto_recovery"):
            offsets += list(range(pos_shift + 1, len(last_clean)))

        for off in offsets:
            if timed_out() or limit_reached():
                return

            pos = "tail"
            if d.get("round_trip") and is_odd:
                pos = "head"

            src = get_clean_char(last, pos, off, conn_s, conn_d, conn_h)
            if not src:
                continue

            raw_targets = {src}
            if use_shift:
                if shift_mode == "abs":
                    raw_targets = {shift_kana(src, ks_abs), shift_kana(src, -ks_abs)}
                else:
                    raw_targets = {shift_kana(src, ks_abs)}

            targets = set()
            for rt in raw_targets:
                targets |= get_variants(rt, allow_daku, allow_handaku, conn_s)

            index = tail_index if (d.get("round_trip") and is_odd) else head_index

            for tc in targets:
                cands = index.get(tc, [])
                for nxt in cands:
                    if nxt in path:
                        continue

                    if d.get("char_limit_mode"):
                        used = "".join(path)
                        used_norm = "".join(get_base_char(c, filt_s, filt_d, filt_h) for c in used)
                        nxt_norm = "".join(get_base_char(c, filt_s, filt_d, filt_h) for c in nxt)
                        if not set(used_norm).isdisjoint(set(nxt_norm)):
                            continue

                    solve(path + [nxt], total_len + len(nxt))
                    if timed_out() or limit_reached():
                        return

    # --- 開始語 ---
    if start_word and start_word in word_pool:
        starts = [start_word]
    else:
        starts = word_pool

    for w in sorted(starts):
        if start_char:
            if get_clean_char(w, "head", 0, filt_s, filt_d, filt_h) != start_char:
                continue
        solve([w], len(w))
        if timed_out() or limit_reached():
            break

    # --- ソート ---
    if sort_mode == "kana":
        results.sort()
    elif sort_mode == "len_asc":
        results.sort(key=lambda x: len("".join(x)))
    elif sort_mode == "len_desc":
        results.sort(key=lambda x: -len("".join(x)))
    elif sort_mode == "random":
        random.shuffle(results)

    return jsonify({
        "routes": results,
        "count": len(results),
        "timeout": timed_out(),
        "limited": limit_reached()
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
