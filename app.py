from flask import Flask, request, jsonify, render_template
import time

app = Flask(__name__)

# ============================
# 辞書読み込み（DICTIONARY_MASTER 方式）
# ============================
from dictionary import DICTIONARY_MASTER

COUNTRY_LIST = DICTIONARY_MASTER["country"]
CAPITAL_LIST = DICTIONARY_MASTER["capital"]
CUSTOM_LIST = DICTIONARY_MASTER["custom"]

def load_words(categories):
    words = []
    if "country" in categories:
        words += COUNTRY_LIST
    if "capital" in categories:
        words += CAPITAL_LIST
    if "custom" in categories:
        words += CUSTOM_LIST
    return words


# ============================
# 正規化
# ============================
SMALL_MAP = {
    "ァ":"ア","ィ":"イ","ゥ":"ウ","ェ":"エ","ォ":"オ",
    "ッ":"ツ","ャ":"ヤ","ュ":"ユ","ョ":"ヨ",
    "ぁ":"あ","ぃ":"い","ぅ":"う","ぇ":"え","ぉ":"お",
    "っ":"つ","ゃ":"や","ゅ":"ゆ","ょ":"よ"
}

DAKU_MAP = {
    "ガ":"カ","ギ":"キ","グ":"ク","ゲ":"ケ","ゴ":"コ",
    "ザ":"サ","ジ":"シ","ズ":"ス","ゼ":"セ","ゾ":"ソ",
    "ダ":"タ","ヂ":"チ","ヅ":"ツ","デ":"テ","ド":"ト",
    "バ":"ハ","ビ":"ヒ","ブ":"フ","ベ":"ヘ","ボ":"ホ",
    "が":"か","ぎ":"き","ぐ":"く","げ":"け","ご":"こ",
    "ざ":"さ","じ":"し","ず":"す","ぜ":"せ","ぞ":"そ",
    "だ":"た","ぢ":"ち","づ":"つ","で":"て","ど":"と",
    "ば":"は","び":"ひ","ぶ":"ふ","べ":"へ","ぼ":"ほ"
}

HANDAKU_MAP = {
    "パ":"ハ","ピ":"ヒ","プ":"フ","ペ":"ヘ","ポ":"ホ",
    "ぱ":"は","ぴ":"ひ","ぷ":"ふ","ぺ":"へ","ぽ":"ほ"
}

def normalize_char(c, unify_small, allow_daku, allow_handaku):
    if unify_small and c in SMALL_MAP:
        c = SMALL_MAP[c]
    if allow_daku and c in DAKU_MAP:
        c = DAKU_MAP[c]
    if allow_handaku and c in HANDAKU_MAP:
        c = HANDAKU_MAP[c]
    return c

def normalize_word(w, unify_small, allow_daku, allow_handaku):
    return "".join(normalize_char(c, unify_small, allow_daku, allow_handaku) for c in w)


# ============================
# ずらし
# ============================
KANA = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"

def apply_shift_char(c, ks_abs, shift_mode):
    if c not in KANA:
        return c
    idx = KANA.index(c)
    return KANA[(idx + ks_abs) % len(KANA)]

def apply_physical_shift(c, pos_shift):
    if c not in KANA:
        return c
    idx = KANA.index(c)
    return KANA[(idx + pos_shift) % len(KANA)]


# ============================
# 接続判定（仕様 1〜6 全部入り）
# ============================
def can_connect(prev, nxt, *, unify_small, allow_daku, allow_handaku,
                ks_abs, shift_mode, pos_shift,
                auto_recovery, anti_loop, anti_loop_physical):

    prev_norm = normalize_word("".join(apply_shift_char(c, ks_abs, shift_mode) for c in prev),
                               unify_small, allow_daku, allow_handaku)
    nxt_norm  = normalize_word("".join(apply_shift_char(c, ks_abs, shift_mode) for c in nxt),
                               unify_small, allow_daku, allow_handaku)

    prev_last = prev_norm[-1]
    nxt_head  = nxt_norm[0]

    # ⑤ 1文字ループ拒否
    if anti_loop:
        if prev_last == nxt_head:
            return False

    # ⑥ 物理ずらし戻り拒否
    if anti_loop_physical:
        if apply_physical_shift(prev[-1], pos_shift) == apply_physical_shift(nxt[0], pos_shift):
            return False

    # 通常接続
    if prev_last == nxt_head:
        return True

    # ① 遡り接続
    if auto_recovery:
        for i in range(2, len(prev_norm)+1):
            if prev_norm[-i] == nxt_head:
                return True

    return False


# ============================
# 単語フィルタ（重複禁止 / 共役排除 / 共役集約）
# ============================
def filter_words(words, *, unify_small, allow_daku, allow_handaku,
                 char_limit_mode, exclude_conjugate, conjugate_merge):

    norm_words = [normalize_word(w, unify_small, allow_daku, allow_handaku) for w in words]

    pair_count = {}
    for nw in norm_words:
        pair = (nw[0], nw[-1])
        pair_count[pair] = pair_count.get(pair, 0) + 1

    pair_seen = {}
    result = []

    for w, nw in zip(words, norm_words):
        pair = (nw[0], nw[-1])

        # ② 重複禁止
        if char_limit_mode:
            if len(set(nw)) != len(nw):
                continue

        # ③ 共役排除
        if exclude_conjugate:
            if pair_count[pair] >= 2:
                continue

        # ④ 共役集約（UI 未実装なので False）
        if conjugate_merge:
            if pair_seen.get(pair, False):
                continue
            pair_seen[pair] = True

        result.append(w)

    return result


# ============================
# 探索
# ============================
def search_routes(words, start_word, start_char, end_char, end_word,
                  max_len, timeout, timeout_enabled, limit, limit_enabled,
                  unify_small, allow_daku, allow_handaku,
                  ks_abs, shift_mode, pos_shift,
                  auto_recovery, anti_loop, anti_loop_physical,
                  red_words, blue_words,
                  early_cut, realtime_counter):

    start_time = time.time()
    results = []
    checked = 0
    hit = 0

    # 赤は除外、青は優先
    words = [w for w in words if w not in red_words]
    blue_set = set(blue_words)
    words = sorted(words, key=lambda w: (w not in blue_set))

    # 開始条件
    def ok_start(w):
        if start_word:
            return w == start_word
        if start_char:
            return normalize_char(w[0], unify_small, allow_daku, allow_handaku) == \
                   normalize_char(start_char, unify_small, allow_daku, allow_handaku)
        return True

    # 終了条件
    def ok_end(w):
        if end_word:
            return w == end_word
        if end_char:
            return normalize_char(w[-1], unify_small, allow_daku, allow_handaku) == \
                   normalize_char(end_char, unify_small, allow_daku, allow_handaku)
        return True

    # DFS
    def dfs(path):
        nonlocal checked, hit, results

        if timeout_enabled and time.time() - start_time > timeout:
            return

        if limit_enabled and len(results) >= limit:
            return

        last = path[-1]

        if ok_end(last):
            results.append(path[:])
            hit += 1
            if early_cut:
                return

        if len(path) >= max_len:
            return

        for w in words:
            checked += 1
            if w in path:
                continue
            if can_connect(last, w,
                           unify_small=unify_small,
                           allow_daku=allow_daku,
                           allow_handaku=allow_handaku,
                           ks_abs=ks_abs,
                           shift_mode=shift_mode,
                           pos_shift=pos_shift,
                           auto_recovery=auto_recovery,
                           anti_loop=anti_loop,
                           anti_loop_physical=anti_loop_physical):
                dfs(path + [w])

    for w in words:
        if ok_start(w):
            dfs([w])

    return results, checked, hit


# ============================
# API
# ============================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/get_dictionary")
def get_dictionary():
    return jsonify({
        "country": COUNTRY_LIST,
        "capital": CAPITAL_LIST,
        "custom": CUSTOM_LIST
    })


@app.route("/search", methods=["POST"])
def search():
    d = request.json

    words = load_words(d["categories"])

    # 単語フィルタ
    words = filter_words(
        words,
        unify_small=d["unify_small"],
        allow_daku=d["allow_daku"],
        allow_handaku=d["allow_handaku"],
        char_limit_mode=d["char_limit_mode"],
        exclude_conjugate=d["exclude_conjugate"],
        conjugate_merge=False
    )

    routes, checked, hit = search_routes(
        words,
        d["start_word"], d["start_char"], d["end_char"], d["end_word"],
        d["max_len"], d["timeout"], d["timeout_enabled"],
        d["limit"], d["limit_enabled"],
        d["unify_small"], d["allow_daku"], d["allow_handaku"],
        d["ks_abs"], d["shift_mode"], d["pos_shift"],
        d["auto_recovery"], d["anti_loop"], d["anti_loop_physical"],
        d["red_words"], d["blue_words"],
        d["display_mode"] == "early",
        d["realtime_counter"]
    )

    return jsonify({
        "routes": routes,
        "checked": checked,
        "hit": hit
    })


if __name__ == "__main__":
    app.run(debug=True)
