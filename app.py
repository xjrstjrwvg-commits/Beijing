import os, time, sys, re, random
from flask import Flask, render_template, request, jsonify
from collections import defaultdict

try:
    from dictionary import DICTIONARY_MASTER
except ImportError:
    DICTIONARY_MASTER = {"country": ["ニホン"], "capital": ["トウキョウ"]}

sys.setrecursionlimit(10000)
app = Flask(__name__)

# --- 定数 ---
KANA_LIST = (
    "アイウエオ" "カキクケコ" "ガギグゲゴ" "サシスセソ" "ザジズゼゾ"
    "タチツテト" "ダヂヅデド" "ナニヌネノ" "ハヒフヘホ" "バビブベボ"
    "パピプペポ" "マミムメモ" "ヤユヨ" "ラリルレロ" "ワン"
)
SMALL_TO_LARGE = {"ァ":"ア","ィ":"イ","ゥ":"ウ","ェ":"エ","ォ":"オ",
                  "ッ":"ツ","ャ":"ヤ","ュ":"ユ","ョ":"ヨ","ヮ":"ワ"}
DAKU_MAP = {"カ":"ガ","キ":"ギ","ク":"グ","ケ":"ゲ","コ":"ゴ",
            "サ":"ザ","シ":"ジ","ス":"ズ","セ":"ゼ","ソ":"ゾ",
            "タ":"ダ","チ":"ヂ","ツ":"ヅ","テ":"デ","ト":"ド",
            "ハ":"バ","ヒ":"ビ","フ":"ブ","ヘ":"ベ","ホ":"ボ"}
HANDAKU_MAP = {"ハ":"パ","ヒ":"ピ","フ":"プ","ヘ":"ペ","ホ":"ポ"}

REV_DAKU = {v:k for k,v in DAKU_MAP.items()}
REV_HANDAKU = {v:k for k,v in HANDAKU_MAP.items()}

# --- ユーティリティ ---
def to_katakana(text):
    if not text: return ""
    return "".join(chr(ord(c)+96) if 0x3041 <= ord(c) <= 0x3096 else c for c in text)

def get_base_char(c, unify_small=False, unify_d=False, unify_h=False):
    res = SMALL_TO_LARGE.get(c, c) if unify_small else c
    if unify_d: res = REV_DAKU.get(res, res)
    if unify_h: res = REV_HANDAKU.get(res, res)
    return res

def get_clean_char(w, pos="head", offset=0, unify_s=False, unify_d=False, unify_h=False):
    t = w.replace("ー", "")
    if not t: return ""
    try:
        idx = offset if pos == "head" else -(1+offset)
        return get_base_char(t[idx], unify_s, unify_d, unify_h)
    except:
        return ""

def shift_kana(c, n):
    if c not in KANA_LIST: return c
    return KANA_LIST[(KANA_LIST.index(c)+n) % len(KANA_LIST)]

def get_variants(c, allow_daku, allow_handaku, unify=False):
    base = SMALL_TO_LARGE.get(c, c) if unify else c
    s = {base}
    if allow_daku:
        for k,v in DAKU_MAP.items():
            if base == k: s.add(v)
            if base == v: s.add(k)
    if allow_handaku:
        for k,v in HANDAKU_MAP.items():
            if base == k: s.add(v)
            if base == v: s.add(k)
    return s

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_dictionary')
def get_dictionary():
    return jsonify(DICTIONARY_MASTER)

@app.route('/search', methods=['POST'])
def search():
    d = request.json

    # --- 削除後の最小限パラメータ ---
    max_len = int(d.get("max_len", 5))

    use_shift = d.get("use_shift", False)
    ks_val = int(d.get("ks_abs", 1))
    s_mode = d.get("shift_mode", "abs")

    allow_daku = d.get("allow_daku", False)
    allow_handaku = d.get("allow_handaku", False)

    raw_valid = to_katakana(d.get("valid_chars", ""))
    valid_chars = set(raw_valid.replace("、","").replace(",","")) if raw_valid else None

    red_words = set(d.get("red_words", []))
    blue_words = set(d.get("blue_words", []))

    asc = [get_clean_char(c.strip(), "head", 0, False, allow_daku, allow_handaku)
           for c in re.split("[、,]", to_katakana(d.get("all_start_char",""))) if c.strip()]

    aec = [get_clean_char(c.strip(), "head", 0, False, allow_daku, allow_handaku)
           for c in re.split("[、,]", to_katakana(d.get("all_end_char",""))) if c.strip()]

    ex_list = [get_base_char(c.strip(), False, allow_daku, allow_handaku)
               for c in re.split("[、,]", to_katakana(d.get("exclude_chars",""))) if c.strip()]

    bs_list = [get_base_char(c.strip(), False, allow_daku, allow_handaku)
               for c in re.split("[、,]", to_katakana(d.get("ban_start_chars",""))) if c.strip()]

    must_chars = [get_base_char(c, False, allow_daku, allow_handaku)
                  for c in re.split("[、,]", to_katakana(d.get("must_char",""))) if c]

    start_word = to_katakana(d.get("start_word",""))
    start_char = get_clean_char(to_katakana(d.get("start_char","")),
                                "head", 0, False, allow_daku, allow_handaku)
    end_char = get_clean_char(to_katakana(d.get("end_char","")),
                              "head", 0, False, allow_daku, allow_handaku)

    # --- 辞書 ---
    raw_pool = []
    for cat in d.get("categories", ["country"]):
        raw_pool.extend(DICTIONARY_MASTER.get(cat, []))
    raw_pool = list(set(raw_pool))

    # --- 一次フィルタ ---
    temp_pool = []
    for w in raw_pool:
        if w in red_words: continue

        if valid_chars and not all(get_base_char(c, False, allow_daku, allow_handaku) in valid_chars
                                   for c in w.replace("ー","")):
            continue

        h = get_clean_char(w, "head", 0, False, allow_daku, allow_handaku)
        t = get_clean_char(w, "tail", 0, False, allow_daku, allow_handaku)

        if asc and h not in asc: continue
        if aec and t not in aec: continue

        norm_w = "".join(get_base_char(c, False, allow_daku, allow_handaku) for c in w)
        if any(ex in norm_w for ex in ex_list): continue

        if h in bs_list: continue

        temp_pool.append(w)

    # --- 共役排除 ---
    word_pool = []
    if d.get("exclude_conjugate", False):
        mp = defaultdict(list)
        for w in temp_pool:
            h = get_clean_char(w, "head", 0, False, allow_daku, allow_handaku)
            t = get_clean_char(w, "tail", 0, False, allow_daku, allow_handaku)
            mp[f"{h}_{t}"].append(w)
        for k,v in mp.items():
            if len(v) == 1:
                word_pool.append(v[0])
    else:
        word_pool = temp_pool

    # --- 接続インデックス ---
    head_index = defaultdict(list)
    tail_index = defaultdict(list)

    for w in word_pool:
        h = get_clean_char(w, "head", 0, False, allow_daku, allow_handaku)
        t = get_clean_char(w, "tail", 0, False, allow_daku, allow_handaku)
        head_index[h].append(w)
        tail_index[t].append(w)

    # --- 探索 ---
    results = []

    def solve(path):
        if len(path) == max_len:

            for b in blue_words:
                if b not in path:
                    return

            joined = "".join(path)
            norm = "".join(get_base_char(c, False, allow_daku, allow_handaku) for c in joined)

            for mc in must_chars:
                if mc not in norm:
                    return

            if end_char:
                last_tail = get_clean_char(path[-1], "tail", 0, False, allow_daku, allow_handaku)
                if last_tail not in get_variants(end_char, allow_daku, allow_handaku, False):
                    return

            results.append(list(path))
            return

        last = path[-1]
        src = get_clean_char(last, "tail", 0, False, allow_daku, allow_handaku)
        if not src:
            return

        raw_targets = {src}
        if use_shift:
            if s_mode == "abs":
                raw_targets = {
                    shift_kana(src, ks_val),
                    shift_kana(src, -ks_val)
                }
            else:
                raw_targets = {shift_kana(src, ks_val)}

        targets = set()
        for rt in raw_targets:
            targets.update(get_variants(rt, allow_daku, allow_handaku, False))

        for tc in targets:
            cands = head_index.get(tc, [])
            for nxt in cands:
                if nxt in path:
                    continue
                solve(path + [nxt])

    starts = [start_word] if start_word in word_pool else word_pool

    for w in sorted(starts):
        if start_char:
            if get_clean_char(w, "head", 0, False, allow_daku, allow_handaku) != start_char:
                continue
        solve([w])

    return jsonify({"routes": results, "count": len(results)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
