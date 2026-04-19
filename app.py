import os, time, sys, re, random
from flask import Flask, render_template, request, jsonify
from collections import Counter, defaultdict

# 外部辞書データのインポート
try:
    from dictionary import DICTIONARY_MASTER
except ImportError:
    DICTIONARY_MASTER = {"country": ["ニホン"], "capital": ["トウキョウ"]}

sys.setrecursionlimit(10000)
app = Flask(__name__)

# --- 定数・マッピング ---
KANA_LIST = (
    "アイウエオ" "カキクケコ" "ガギグゲゴ" "サシスセソ" "ザジズゼゾ"
    "タチツテト" "ダヂヅデド" "ナニヌネノ" "ハヒフヘホ" "バビブベボ"
    "パピプペポ" "マミムメモ" "ヤユヨ" "ラリルレロ" "ワン"
)
SMALL_TO_LARGE = {"ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ", "ッ": "ツ", "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ"}
DAKU_MAP = {"カ":"ガ", "キ":"ギ", "ク":"グ", "ケ":"ゲ", "コ":"ゴ", "サ":"ザ", "シ":"ジ", "ス":"ズ", "セ":"ゼ", "ソ":"ゾ", "タ":"ダ", "チ":"ヂ", "ツ":"ヅ", "テ":"デ", "ト":"ド", "ハ":"バ", "ヒ":"ビ", "フ":"ブ", "ヘ":"ベ", "ホ":"ボ"}
HANDAKU_MAP = {"ハ":"パ", "ヒ":"ピ", "フ":"プ", "ヘ":"ベ", "ホ":"ポ"}
REV_DAKU = {v: k for k, v in DAKU_MAP.items()}
REV_HANDAKU = {v: k for k, v in HANDAKU_MAP.items()}

# --- ユーティリティ ---
def to_katakana(text):
    if not text: return ""
    return "".join([chr(ord(c) + 96) if 0x3041 <= ord(c) <= 0x3096 else c for c in text])

def get_base_char(c):
    return SMALL_TO_LARGE.get(c, c)

def get_clean_char(w, pos="head", offset=0):
    text = w.replace("ー", "")
    if not text: return ""
    try:
        idx = offset if pos == "head" else -(1 + offset)
        return get_base_char(text[idx])
    except IndexError: return ""

def shift_kana(char, n):
    if char not in KANA_LIST: return char
    return KANA_LIST[(KANA_LIST.index(char) + n) % len(KANA_LIST)]

def get_variants(char):
    v = {char}
    if char in DAKU_MAP: v.add(DAKU_MAP[char])
    if char in REV_DAKU: v.add(REV_DAKU[char])
    if char in HANDAKU_MAP: v.add(HANDAKU_MAP[char])
    if char in REV_HANDAKU: v.add(REV_HANDAKU[char])
    return v

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_dictionary')
def get_dictionary(): return jsonify(DICTIONARY_MASTER)

@app.route('/search', methods=['POST'])
def search():
    d = request.json
    summary_mode = d.get('summary_mode', False)
    max_len = int(d.get('max_len', 5))
    p_shift = int(d.get('pos_shift', 0))
    use_shift = d.get('use_shift', False)
    ks_val = int(d.get('ks_abs', 1))
    s_mode = d.get('shift_mode', 'abs')
    
    # フィルタリング
    blocked = set(d.get('blocked_words', []))
    forced = set(d.get('force_words', []))
    raw_pool = []
    for cat in d.get('categories', ["country"]):
        raw_pool.extend(DICTIONARY_MASTER.get(cat, []))
    
    temp_pool = [w for w in set(raw_pool) if w not in blocked]
    
    if d.get('exclude_conjugates'):
        pair_map = defaultdict(list)
        for w in temp_pool:
            ch, ct = get_clean_char(w, "head", 0), get_clean_char(w, "tail", 0)
            pair_map[f"{ch}_{ct}"].append(w)
        word_pool = [v[0] for v in pair_map.values() if len(v) == 1]
    else:
        word_pool = temp_pool

    # インデックス作成
    h_idx = defaultdict(list)
    t_idx = defaultdict(list)
    for w in word_pool:
        h_idx[get_clean_char(w, "head", 0)].append(w)
        t_idx[get_clean_char(w, "tail", 0)].append(w)

    results = []
    summary_counts = Counter()
    start_time = time.time()
    limit = 1500

    def solve(path, current_len):
        if time.time() - start_time > 15: return
        if not summary_mode and len(results) >= limit: return

        if len(path) == max_len:
            if not forced.issubset(set(path)): return
            # 終了字指定(通常モード時のみ厳密チェック)
            if not summary_mode and d.get('end_char'):
                if get_clean_char(path[-1], "tail", 0) != to_katakana(d.get('end_char')): return
            
            if summary_mode:
                s_char = get_clean_char(path[0], "head", 0)
                e_char = get_clean_char(path[-1], "tail", 0)
                summary_counts[f"{s_char}→{e_char}"] += 1
            else:
                results.append(list(path))
            return

        last_w = path[-1]
        is_even = len(path) % 2 == 0
        # 接続元文字取得 (牛耕/遡り対応)
        src_pos = "head" if (d.get('round_trip') and is_even) else "tail"
        
        # 遡り接続 (auto_recovery)
        offsets = [p_shift]
        if d.get('auto_recovery'):
            clean_last = last_w.replace("ー","")
            offsets = range(p_shift, len(clean_last))

        for off in offsets:
            src = get_clean_char(last_w, src_pos, off)
            if not src: continue
            
            # 50音ずらし適用
            targets = set()
            if use_shift:
                targets.add(shift_kana(src, ks_val))
                if s_mode == 'abs': targets.add(shift_kana(src, -ks_val))
            else:
                targets.add(src)
            
            # 濁音・半濁音バリエーション展開
            final_targets = set()
            for t in targets: final_targets.update(get_variants(t))

            found_any = False
            for target in final_targets:
                # 次の単語の検索方向 (牛耕対応)
                next_pos_idx = t_idx if (d.get('round_trip') and not is_even) else h_idx
                for nxt in next_pos_idx.get(target, []):
                    if nxt in path: continue
                    found_any = True
                    solve(path + [nxt], current_len + len(nxt))
            if found_any: break # 最初の有効なオフセットで探索

    # 探索開始
    start_word = to_katakana(d.get('start_word', ""))
    starts = [start_word] if start_word in word_pool else word_pool
    
    for w in sorted(starts):
        if not summary_mode and d.get('start_char'):
            if get_clean_char(w, "head", 0) != to_katakana(d.get('start_char')): continue
        solve([w], len(w))

    if summary_mode:
        # 通り数が多い順にソートして返却
        res_list = [f"{k} {v}通り" for k, v in summary_counts.most_common()]
        return jsonify({"summary": res_list, "count": sum(summary_counts.values())})
    
    return jsonify({"routes": results, "count": len(results)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
