import os, time, sys, re, random
from flask import Flask, render_template, request, jsonify
from collections import defaultdict

# --- 辞書読み込み ---
try:
    from dictionary import DICTIONARY_MASTER
except ImportError:
    DICTIONARY_MASTER = {"country": ["ニホン"], "capital": ["トウキョウ"], "custom": []}

sys.setrecursionlimit(10000)
app = Flask(__name__)

# --- かな処理 ---
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
    "ァ":"ア","ィ":"イ","ゥ":"ウ","ェ":"エ","ォ":"オ",
    "ッ":"ツ","ャ":"ヤ","ュ":"ユ","ョ":"ヨ","ヮ":"ワ"
}

DAKU_MAP = {
    "カ":"ガ","キ":"ギ","ク":"グ","ケ":"ゲ","コ":"ゴ",
    "サ":"ザ","シ":"ジ","ス":"ズ","セ":"ゼ","ソ":"ゾ",
    "タ":"ダ","チ":"ヂ","ツ":"ヅ","テ":"デ","ト":"ド",
    "ハ":"バ","ヒ":"ビ","フ":"ブ","ヘ":"ベ","ホ":"ボ"
}

HANDAKU_MAP = {
    "ハ":"パ","ヒ":"ピ","フ":"プ","ヘ":"ペ","ホ":"ポ"
}

REV_DAKU = {v: k for k, v in DAKU_MAP.items()}
REV_HANDAKU = {v: k for k, v in HANDAKU_MAP.items()}

def to_katakana(text):
    if not text: return ""
    return "".join([chr(ord(c)+96) if 0x3041 <= ord(c) <= 0x3096 else c for c in text])

def get_base_char(c, unify_small=False, unify_daku=False, unify_handaku=False):
    res = SMALL_TO_LARGE.get(c, c) if unify_small else c
    if unify_daku: res = REV_DAKU.get(res, res)
    if unify_handaku: res = REV_HANDAKU.get(res, res)
    return res

def get_clean_char(w, pos="head", offset=0, us=False, ud=False, uh=False):
    text = w.replace("ー","")
    if not text: return ""
    try:
        idx = offset if pos=="head" else -(1+offset)
        return get_base_char(text[idx], us, ud, uh)
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
            if base==k: s.add(v)
            if base==v: s.add(k)
    if allow_handaku:
        for k,v in HANDAKU_MAP.items():
            if base==k: s.add(v)
            if base==v: s.add(k)
    return s

# --- ルート ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/get_dictionary")
def get_dictionary():
    return jsonify(DICTIONARY_MASTER)

# --- メイン探索 ---
@app.route("/search", methods=["POST"])
def search():
    d = request.json

    # --- 基本パラメータ ---
    max_len = int(d.get("max_len", 5))
    pos_shift = int(d.get("pos_shift", 0))
    use_shift = d.get("use_shift", False)
    ks_abs = int(d.get("ks_abs", 1))
    shift_mode = d.get("shift_mode", "abs")

    unify_small = d.get("unify_small", False)
    allow_daku = d.get("allow_daku", False)
    allow_handaku = d.get("allow_handaku", False)
    unify_scope = d.get("unify_scope", "all")

    conn_s = unify_small and unify_scope in ["all","conn"]
    conn_d = allow_daku and unify_scope in ["all","conn"]
    conn_h = allow_handaku and unify_scope in ["all","conn"]

    filt_s = unify_small and unify_scope in ["all","filter"]
    filt_d = allow_daku and unify_scope in ["all","filter"]
    filt_h = allow_handaku and unify_scope in ["all","filter"]

    len_mode = d.get("len_mode", "free")
    sort_mode = d.get("sort_mode", "default")

    target_total_len = d.get("target_total_len")
    if target_total_len:
        target_total_len = int(target_total_len)

    # --- 入力 ---
    start_word = to_katakana(d.get("start_word",""))
    start_char = get_clean_char(to_katakana(d.get("start_char","")), "head", 0, filt_s, filt_d, filt_h)
    end_char = get_clean_char(to_katakana(d.get("end_char","")), "head", 0, filt_s, filt_d, filt_h)

    asc = [get_clean_char(c.strip(),"head",0,filt_s,filt_d,filt_h)
           for c in re.split("[,、]", to_katakana(d.get("all_start_char",""))) if c.strip()]

    aec = [get_clean_char(c.strip(),"head",0,filt_s,filt_d,filt_h)
           for c in re.split("[,、]", to_katakana(d.get("all_end_char",""))) if c.strip()]

    valid_chars = set(to_katakana(d.get("valid_chars","")).replace(",","").replace("、","")) or None

    exclude_chars = [get_base_char(c.strip(),filt_s,filt_d,filt_h)
                     for c in re.split("[,、]", to_katakana(d.get("exclude_chars",""))) if c.strip()]

    ban_start_chars = [get_base_char(c.strip(),filt_s,filt_d,filt_h)
                       for c in re.split("[,、]", to_katakana(d.get("ban_start_chars",""))) if c.strip()]

    must_chars = [get_base_char(c.strip(),filt_s,filt_d,filt_h)
                  for c in re.split("[,、]", to_katakana(d.get("must_char",""))) if c.strip()]

    # --- 辞書 ---
    raw_pool = []
    for cat in d.get("categories",["country"]):
        raw_pool.extend(DICTIONARY_MASTER.get(cat,[]))
    raw_pool = list(set(raw_pool))

    # --- 赤/青 ---
    red_words = set(d.get("red_words",[]))
    blue_words = set(d.get("blue_words",[]))

    # --- フィルタ ---
    temp_pool = []
    for w in raw_pool:
        if w in red_words: continue

        w_k = to_katakana(w)
        h = get_clean_char(w_k,"head",0,filt_s,filt_d,filt_h)
        t = get_clean_char(w_k,"tail",0,filt_s,filt_d,filt_h)

        if asc and h not in asc: continue
        if aec and t not in aec: continue

        if valid_chars:
            if not all(get_base_char(c,filt_s,filt_d,filt_h) in valid_chars for c in w_k.replace("ー","")):
                continue

        norm_w = "".join(get_base_char(c,filt_s,filt_d,filt_h) for c in w_k)
        if any(ex in norm_w for ex in exclude_chars): continue
        if any(h == bs for bs in ban_start_chars): continue

        temp_pool.append(w_k)

    # --- 共役排除 ---
    if d.get("exclude_conjugate"):
        pair_map = defaultdict(list)
        for w in temp_pool:
            ch = get_clean_char(w,"head",0,conn_s,conn_d,conn_h)
            ct = get_clean_char(w,"tail",0,conn_s,conn_d,conn_h)
            pair_map[f"{ch}_{ct}"].append(w)
        word_pool = [v[0] for v in pair_map.values()]
    else:
        word_pool = temp_pool

    # --- インデックス ---
    head_index = defaultdict(list)
    tail_index = defaultdict(list)
    for w in word_pool:
        head_index[get_clean_char(w,"head",0,conn_s,conn_d,conn_h)].append(w)
        tail_index[get_clean_char(w,"tail",0,conn_s,conn_d,conn_h)].append(w)

    # --- 探索 ---
    results = []
    start_time = time.time()
    timeout = 15

    def solve(path, total_len):
        if time.time()-start_time > timeout: return
        if len(path) == max_len:
            # 文字数構成
            if len_mode=="same" and len({len(x) for x in path})>1: return
            if len_mode=="diff" and len({len(x) for x in path})!=len(path): return

            # 青単語
            if not blue_words.issubset(set(path)): return

            # 合計文字数
            if target_total_len and total_len != target_total_len: return

            # 終了文字
            if end_char:
                last_t = get_clean_char(path[-1],"tail",0,conn_s,conn_d,conn_h)
                if last_t not in get_variants(end_char,allow_daku,allow_handaku,conn_s):
                    return

            # 必須文字
            joined = "".join(path)
            norm_join = "".join(get_base_char(c,filt_s,filt_d,filt_h) for c in joined)
            for mc in must_chars:
                if norm_join.count(mc) < 1: return

            results.append(path.copy())
            return

        last = path[-1]
        last_clean = last.replace("ー","")
        is_odd = (len(path)%2 != 0)

        offsets = [pos_shift]
        if d.get("auto_recovery"):
            offsets += list(range(pos_shift+1, len(last_clean)))

        for off in offsets:
            src = get_clean_char(last, ("tail" if not d.get("round_trip") or is_odd else "head"), off, conn_s,conn_d,conn_h)
            if not src: continue

            raw_targets = {src}
            if use_shift:
                if shift_mode=="abs":
                    raw_targets = {shift_kana(src, ks_abs), shift_kana(src, -ks_abs)}
                else:
                    raw_targets = {shift_kana(src, ks_abs)}

            targets = set()
            for rt in raw_targets:
                targets |= get_variants(rt, allow_daku, allow_handaku, conn_s)

            for tc in targets:
                cands = (tail_index if (d.get("round_trip") and is_odd) else head_index).get(tc, [])
                for nxt in cands:
                    if nxt in path: continue

                    # 文字重複禁止
                    if d.get("char_limit_mode"):
                        used = "".join(path)
                        used = "".join(get_base_char(c,filt_s,filt_d,filt_h) for c in used)
                        nxt_norm = "".join(get_base_char(c,filt_s,filt_d,filt_h) for c in nxt)
                        if not set(used).isdisjoint(set(nxt_norm)):
                            continue

                    solve(path+[nxt], total_len+len(nxt))

    # --- 開始 ---
    starts = [start_word] if start_word in word_pool else word_pool
    for w in sorted(starts):
        if start_char:
            if get_clean_char(w,"head",0,filt_s,filt_d,filt_h) != start_char:
                continue
        solve([w], len(w))

    # --- ソート ---
    if sort_mode=="kana":
        results.sort()
    elif sort_mode=="len_asc":
        results.sort(key=lambda x: len("".join(x)))
    elif sort_mode=="len_desc":
        results.sort(key=lambda x: -len("".join(x)))
    elif sort_mode=="random":
        random.shuffle(results)

    return jsonify({"routes": results, "count": len(results)})

# --- Render 用 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
