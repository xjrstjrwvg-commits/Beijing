import os, time, sys, re, random
from flask import Flask, render_template, request, jsonify
from collections import Counter, defaultdict

# 辞書データのインポート
try:
    from dictionary import DICTIONARY_MASTER
except ImportError:
    DICTIONARY_MASTER = {"country": ["ニホン"], "capital": ["トウキョウ"]}

sys.setrecursionlimit(10000)
# iPhoneでの階層トラブルを防ぐためtemplate_folderを明示
app = Flask(__name__, template_folder='templates')

# --- 定数・マッピング ---
KANA_LIST = (
    "アイウエオ" "カキクケコ" "ガギグゲゴ" "サシスセソ" "ザジズゼゾ"
    "タチツテト" "ダヂヅデド" "ナニヌネノ" "ハヒフヘホ" "バビブベボ"
    "パピプペポ" "マミムメモ" "ヤユヨ" "ラリルレロ" "ワン"
)
SMALL_TO_LARGE = {"ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ", "ッ": "ツ", "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ"}
DAKU_MAP = {"カ":"ガ", "キ":"ギ", "ク":"グ", "ケ":"ゲ", "コ":"ゴ", "サ":"ザ", "シ":"ジ", "ス":"ズ", "セ":"ゼ", "ソ":"ゾ", "タ":"ダ", "チ":"ヂ", "ツ":"ヅ", "テ":"デ", "ト":"ド", "ハ":"バ", "ヒ":"ビ", "フ":"ブ", "ヘ":"ベ", "ホ":"ボ"}
HANDAKU_MAP = {"ハ":"パ", "ヒ":"ピ", "フ":"プ", "ヘ":"ペ", "ホ":"ポ"}

REV_DAKU = {v: k for k, v in DAKU_MAP.items()}
REV_HANDAKU = {v: k for k, v in HANDAKU_MAP.items()}

# --- ユーティリティ ---
def to_katakana(text):
    if not text: return ""
    return "".join([chr(ord(c) + 96) if 0x3041 <= ord(c) <= 0x3096 else c for c in text])

def get_base_char(c, unify_small=False, unify_daku=False, unify_handaku=False):
    res = SMALL_TO_LARGE.get(c, c) if unify_small else c
    if unify_daku: res = REV_DAKU.get(res, res)
    if unify_handaku: res = REV_HANDAKU.get(res, res)
    return res

def get_clean_char(w, pos="head", offset=0, unify_s=False, unify_d=False, unify_h=False):
    text = w.replace("ー", "")
    if not text: return ""
    try:
        idx = offset if pos == "head" else -(1 + offset)
        char = text[idx]
        return get_base_char(char, unify_s, unify_d, unify_h)
    except IndexError: return ""

def shift_kana(char, n):
    if char not in KANA_LIST: return char
    return KANA_LIST[(KANA_LIST.index(char) + n) % len(KANA_LIST)]

def get_variants(char, allow_daku, allow_handaku, unify=False):
    base = SMALL_TO_LARGE.get(char, char) if unify else char
    variants = {base}
    if allow_daku:
        for k, v in DAKU_MAP.items():
            if base == k: variants.add(v)
            if base == v: variants.add(k)
    if allow_handaku:
        for k, v in HANDAKU_MAP.items():
            if base == k: variants.add(v)
            if base == v: variants.add(k)
    return variants

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_dictionary')
def get_dictionary():
    return jsonify(DICTIONARY_MASTER)

@app.route('/search', methods=['POST'])
def search():
    d = request.json
    timeout = int(d.get('timeout', 15))
    limit = int(d.get('limit', 1500))
    limit_en = d.get('limit_enabled', True)
    max_len = int(d.get('max_len', 5))
    p_shift = int(d.get('pos_shift', 0))
    use_shift = d.get('use_shift', False)
    ks_val = int(d.get('ks_abs', 1))
    s_mode = d.get('shift_mode', 'abs')
    
    u_small, u_daku, u_handaku = d.get('unify_small', False), d.get('allow_daku', False), d.get('allow_handaku', False)
    scope = d.get('unify_scope', 'all')
    
    conn_s, conn_d, conn_h = (u_small and scope in ['all', 'conn']), (u_daku and scope in ['all', 'conn']), (u_handaku and scope in ['all', 'conn'])
    filt_s, filt_d, filt_h = (u_small and scope in ['all', 'filter']), (u_daku and scope in ['all', 'filter']), (u_handaku and scope in ['all', 'filter'])

    len_mode = d.get('len_mode', 'free')
    raw_valid = to_katakana(d.get('valid_chars', ""))
    valid_chars = set(raw_valid.replace("、", "").replace(",", "")) if raw_valid else None

    red_words, blue_words = set(d.get('red_words', [])), set(d.get('blue_words', []))
    
    asc = [get_clean_char(c.strip(), "head", 0, filt_s, filt_d, filt_h) for c in re.split('[、,]', to_katakana(d.get('all_start_char', ""))) if c.strip()]
    aec = [get_clean_char(c.strip(), "head", 0, filt_s, filt_d, filt_h) for c in re.split('[、,]', to_katakana(d.get('all_end_char', ""))) if c.strip()]
    ex_list = [get_base_char(c.strip(), filt_s, filt_d, filt_h) for c in re.split('[、,]', to_katakana(d.get('exclude_chars', ""))) if c.strip()]
    bs_list = [get_base_char(c.strip(), filt_s, filt_d, filt_h) for c in re.split('[、,]', to_katakana(d.get('ban_start_chars', ""))) if c.strip()]
    must_chars = [get_base_char(c, filt_s, filt_d, filt_h) for c in re.split('[、,]', to_katakana(d.get('must_char', ""))) if c]
    
    start_word = to_katakana(d.get('start_word', ""))
    start_char = get_clean_char(to_katakana(d.get('start_char', "")), "head", 0, filt_s, filt_d, filt_h)
    end_char = get_clean_char(to_katakana(d.get('end_char', "")), "head", 0, filt_s, filt_d, filt_h)

    raw_pool = []
    for cat in d.get('categories', ["country"]):
        raw_pool.extend(DICTIONARY_MASTER.get(cat, []))
    raw_pool = list(set(raw_pool))
    
    # スコア用：元の単語数
    total_raw_count = len(raw_pool)

    temp_pool = []
    for w in raw_pool:
        if w in red_words: continue
        if valid_chars and not all(get_base_char(c, filt_s, filt_d, filt_h) in valid_chars for c in w.replace("ー", "")): continue
        h_char = get_clean_char(w, "head", 0, filt_s, filt_d, filt_h)
        t_char = get_clean_char(w, "tail", 0, filt_s, filt_d, filt_h)
        if asc and h_char not in asc: continue
        if aec and t_char not in aec: continue
        norm_w = "".join([get_base_char(c, filt_s, filt_d, filt_h) for c in w])
        if any(ex in norm_w for ex in ex_list): continue
        if any(h_char == bs for bs in bs_list): continue
        temp_pool.append(w)

    if d.get('exclude_conjugate'):
        pair_map = defaultdict(list)
        for w in temp_pool:
            ch = get_clean_char(w, "head", 0, conn_s, conn_d, conn_h)
            ct = get_clean_char(w, "tail", 0, conn_s, conn_d, conn_h)
            pair_map[f"{ch}_{ct}"].append(w)
        word_pool = []
        for words in pair_map.values():
            if len(words) == 1: word_pool.append(words[0])
    else:
        word_pool = temp_pool

    # スコア用：現在の有効単語数
    available_word_count = len(word_pool)

    head_index, tail_index = defaultdict(list), defaultdict(list)
    for w in word_pool:
        head_index[get_clean_char(w, "head", 0, conn_s, conn_d, conn_h)].append(w)
        tail_index[get_clean_char(w, "tail", 0, conn_s, conn_d, conn_h)].append(w)

    results, start_time = [], time.time()

    def solve(path, current_total_len):
        if time.time() - start_time > timeout or (limit_en and len(results) >= limit): return
        if len_mode == 'diff' and len(path) > 1:
            lens = [len(x) for x in path]
            if len(lens) != len(set(lens)): return

        if len(path) == max_len:
            if len_mode == 'same' and len(set(len(x) for x in path)) > 1: return
            path_set = set(path)
            if not blue_words.issubset(path_set): return
            norm_t = "".join([get_base_char(c, filt_s, filt_d, filt_h) for c in "".join(path)])

            def check_list(lst):
                for group in lst:
                    target_cnt, g_shift, items = 1, 0, []
                    for itm in group:
                        if ':' in itm:
                            ps = itm.split(':')
                            if ps.upper() == 'S': g_shift = int(ps)
                            else: target_cnt = int(ps if ps.isdigit() else ps)
                        else: items.append(itm)
                    total = sum(norm_t.count("".join([get_base_char(shift_kana(c, g_shift), filt_s, filt_d, filt_h) for c in it])) for it in items)
                    if (d.get('exclusive_choice') and total != target_cnt) or (not d.get('exclusive_choice') and total < target_cnt): return False
                return True

            if not (check_list(d.get('group_constraints', [])) and check_list(d.get('choice_constraints', []))): return
            if must_chars and not all(norm_t.count(mc) >= 1 and (norm_t.count(mc) == 1 if d.get('once_constraint') else True) for mc in must_chars): return
            if d.get('target_total_len') and current_total_len != int(d['target_total_len']): return
            if end_char and get_clean_char(path[-1], "tail", 0, conn_s, conn_d, conn_h) not in get_variants(end_char, u_daku, u_handaku, conn_s): return
            results.append(list(path))
            return
        
        is_odd = (len(path) % 2 != 0)
        last_word_clean = path[-1].replace("ー","")
        base_offsets = [p_shift] + ([i for i in range(p_shift+1, len(last_word_clean))] if d.get('auto_recovery') else [])
        
        for off in base_offsets:
            src = get_clean_char(path[-1], ("tail" if not d.get('round_trip') or is_odd else "head"), off, conn_s, conn_d, conn_h)
            if not src: continue
            raw_ts = {shift_kana(src, ks_val if s_mode!='abs' else abs(ks_val)), shift_kana(src, -abs(ks_val))} if use_shift and s_mode == 'abs' else {shift_kana(src, ks_val)} if use_shift else {src}
            targets = set()
            for rt in raw_ts: targets.update(get_variants(rt, u_daku, u_handaku, conn_s))
            found = False
            for tc in targets:
                cands = (tail_index if (d.get('round_trip') and is_odd) else head_index).get(tc, [])
                for nxt in cands:
                    if nxt in path: continue
                    if d.get('char_limit_mode'):
                        p_txt = "".join([get_base_char(c, filt_s, filt_d, filt_h) for c in "".join(path)])
                        n_txt = "".join([get_base_char(c, filt_s, filt_d, filt_h) for c in nxt])
                        if not set(p_txt).isdisjoint(set(n_txt)): continue
                    found = True
                    solve(path + [nxt], current_total_len + len(nxt))
            if found: break

    starts = [start_word] if start_word in word_pool else word_pool
    for w in sorted(starts):
        if not start_word and start_char and get_clean_char(w, "head", 0, filt_s, filt_d, filt_h) != start_char: continue
        solve([w], len(w))
    
    return jsonify({
        "routes": results, 
        "count": len(results),
        "total_raw_count": total_raw_count,
        "available_word_count": available_word_count
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
