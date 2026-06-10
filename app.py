# ★ 物理ずらし戻り禁止（anti_loop_physical）を完全削除した版 ★

from flask import Flask, request, jsonify, render_template
import time

try:
    from dictionary import COUNTRY, CAPITAL, CUSTOM
except:
    COUNTRY, CAPITAL, CUSTOM = [], [], []

app = Flask(__name__)

# =========================
# 正規化・かな処理
# =========================

KANA_ORDER = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"

SMALL_TO_BIG = {
    "ァ":"ア","ィ":"イ","ゥ":"ウ","ェ":"エ","ォ":"オ",
    "ッ":"ツ","ャ":"ヤ","ュ":"ユ","ョ":"ヨ",
    "ぁ":"ア","ぃ":"イ","ぅ":"ウ","ぇ":"エ","ぉ":"オ",
    "っ":"ツ","ゃ":"ヤ","ゅ":"ユ","ょ":"ヨ"
}

DAKU_MAP = {
    "ガ":"カ","ギ":"キ","グ":"ク","ゲ":"ケ","ゴ":"コ",
    "ザ":"サ","ジ":"シ","ズ":"ス","ゼ":"セ","ゾ":"ソ",
    "ダ":"タ","ヂ":"チ","ヅ":"ツ","デ":"テ","ド":"ト",
    "バ":"ハ","ビ":"ヒ","ブ":"フ","ベ":"ヘ","ボ":"ホ",
}

HANDAKU_MAP = {
    "パ":"ハ","ピ":"ヒ","プ":"フ","ペ":"ヘ","ポ":"ホ",
}

def hira_to_kata(s):
    res=[]
    for ch in s:
        code=ord(ch)
        if 0x3041<=code<=0x3096:
            res.append(chr(code+0x60))
        else:
            res.append(ch)
    return "".join(res)

def normalize_word(s, unify_small, allow_daku, allow_handaku):
    s=hira_to_kata(s)
    res=[]
    for ch in s:
        c=ch
        if unify_small and c in SMALL_TO_BIG:
            c=SMALL_TO_BIG[c]
        if allow_daku and c in DAKU_MAP:
            c=DAKU_MAP[c]
        if allow_handaku and c in HANDAKU_MAP:
            c=HANDAKU_MAP[c]
        res.append(c)
    return "".join(res)

def last_char_effective(s):
    if not s: return ""
    if s[-1]=="ー" and len(s)>=2:
        return s[-2]
    return s[-1]

def first_char(s):
    return s[0] if s else ""

def kana_index(ch):
    try:
        return KANA_ORDER.index(ch)
    except:
        return -1

def shift_kana(ch, shift):
    idx=kana_index(ch)
    if idx<0: return ch
    return KANA_ORDER[(idx+shift)%len(KANA_ORDER)]

# =========================
# 物理ずらし（戻り禁止は削除）
# =========================

def physical_shift_head(word, pos_shift):
    if not word: return ""
    return shift_kana(first_char(word), pos_shift)

# =========================
# 共役
# =========================

def conjugate_key(word, norm_conf):
    w=normalize_word(word, **norm_conf)
    return (first_char(w), last_char_effective(w))

def apply_conjugate_filters(words, exclude_conjugate, conjugate_merge, norm_conf):
    if exclude_conjugate and conjugate_merge:
        conjugate_merge=False

    if not exclude_conjugate and not conjugate_merge:
        return words

    from collections import defaultdict
    groups=defaultdict(list)
    for w in words:
        groups[conjugate_key(w, norm_conf)].append(w)

    result=[]
    for key, ws in groups.items():
        if exclude_conjugate:
            if len(ws)==1:
                result.extend(ws)
        else:
            ws_sorted=sorted(ws)
            result.append(ws_sorted[0])
    return result

# =========================
# 必須文字
# =========================

def parse_must_char_expr(expr):
    expr=hira_to_kata(expr.strip())
    if not expr: return None

    if "：" in expr:
        expr=expr.replace("：",":")

    if ":" in expr:
        ch,num=expr.split(":",1)
        return (ch[0],">=",int(num))
    elif "=" in expr:
        ch,num=expr.split("=",1)
        return (ch[0],"==",int(num))
    else:
        return (expr[0],">=",1)

def check_must_chars(route_words, must_raw, norm_conf):
    if not must_raw.strip(): return True

    joined="".join(route_words)
    joined=normalize_word(joined, **norm_conf)

    conds=[]
    for part in must_raw.split(","):
        p=parse_must_char_expr(part)
        if p: conds.append(p)

    from collections import Counter
    cnt=Counter(joined)

    for ch,op,n in conds:
        if ch=="〇":
            if op=="==":
                ok=any(c==n for c in cnt.values())
            else:
                ok=any(c>=n for c in cnt.values())
        else:
            c=cnt.get(ch,0)
            ok=(c==n) if op=="==" else (c>=n)
        if not ok: return False
    return True

# =========================
# 文字数構成
# =========================

def check_len_mode(route, mode):
    if mode=="free": return True
    if mode=="strict":
        lens=[len(w) for w in route]
        return len(set(lens))<=1
    return True

# =========================
# 青（必須単語）
# =========================

def check_blue(route, blue_words):
    if not blue_words: return True
    s=set(route)
    return all(b in s for b in blue_words)

# =========================
# 接続判定（物理ずらし戻り禁止は削除）
# =========================

def can_connect(prev, nxt, norm_conf,
                auto_recovery, anti_loop,
                round_trip, route, pos_shift):

    p=normalize_word(prev, **norm_conf)
    n=normalize_word(nxt, **norm_conf)

    # 牛耕
    if round_trip and route:
        idx=len(route)+1
        if idx%2==1:
            return last_char_effective(p)==last_char_effective(n)
        else:
            return first_char(p)==first_char(n)

    # 遡り接続
    n_head=first_char(n)
    p_chars=list(p)

    found=False
    for i in range(len(p_chars)-1,-1,-1):
        c=p_chars[i]
        if c=="ー": continue
        if c==n_head:
            found=True
            break
        if not auto_recovery:
            break
    if not found: return False

    # 1文字ループ拒否（残す）
    if anti_loop and len(route)>=2:
        a=normalize_word(route[-2], **norm_conf)
        if last_char_effective(a)==n_head:
            return False

    return True

# =========================
# DFS（max_len 削除 → exact_limit のみ）
# =========================

def search_routes(words, start_word, start_char, end_word, end_char,
                  all_start_char, all_end_char, valid_chars, exclude_chars,
                  ban_start_chars, target_total_len, len_mode, sort_mode,
                  use_shift, ks_abs, shift_mode, pos_shift,
                  unify_small, allow_daku, allow_handaku,
                  auto_recovery, round_trip, char_limit_mode,
                  exclude_conjugate, conjugate_merge,
                  must_raw, blue_words,
                  timeout, limit, exact_limit,
                  anti_loop):

    start_time=time.time()
    routes=[]

    norm_conf=dict(
        unify_small=unify_small,
        allow_daku=allow_daku,
        allow_handaku=allow_handaku
    )

    # フィルタ
    def ok(w):
        wn=normalize_word(w, **norm_conf)
        if exclude_chars:
            for ch in exclude_chars:
                if ch in wn: return False
        if valid_chars:
            for ch in wn:
                if ch not in valid_chars: return False
        if all_start_char and first_char(wn)!=all_start_char: return False
        if all_end_char and last_char_effective(wn)!=all_end_char: return False
        return True

    words=[w for w in words if ok(w)]

    # 共役
    words=apply_conjugate_filters(words, exclude_conjugate, conjugate_merge, norm_conf)

    # 重複禁止
    if char_limit_mode:
        def no_dup(w):
            wn=normalize_word(w, **norm_conf)
            return len(set(wn))==len(wn)
        words=[w for w in words if no_dup(w)]

    # 開始候補
    cands=words[:]

    if ban_start_chars:
        def bad(w):
            wn=normalize_word(w, **norm_conf)
            return first_char(wn) in ban_start_chars
        cands=[w for w in cands if not bad(w)]

    if start_word:
        starts=[start_word] if start_word in words else []
    elif start_char:
        starts=[w for w in cands if first_char(normalize_word(w, **norm_conf))==start_char]
    else:
        starts=cands

    # DFS
    def dfs(route):
        nonlocal routes
        if timeout and time.time()-start_time>timeout: return
        if limit and len(routes)>=limit: return
        if exact_limit and len(route)>exact_limit: return

        last=route[-1]
        ln=normalize_word(last, **norm_conf)

        # 終了条件
        if end_word and last==end_word:
            if check_len_mode(route,len_mode):
                if target_total_len:
                    if sum(len(w) for w in route)==target_total_len:
                        if check_must_chars(route,must_raw,norm_conf) and check_blue(route,blue_words):
                            routes.append(route[:])
                else:
                    if check_must_chars(route,must_raw,norm_conf) and check_blue(route,blue_words):
                        routes.append(route[:])

        if end_char and last_char_effective(ln)==end_char:
            if check_len_mode(route,len_mode):
                if target_total_len:
                    if sum(len(w) for w in route)==target_total_len:
                        if check_must_chars(route,must_raw,norm_conf) and check_blue(route,blue_words):
                            routes.append(route[:])
                else:
                    if check_must_chars(route,must_raw,norm_conf) and check_blue(route,blue_words):
                        routes.append(route[:])

        for w in words:
            if w in route: continue
            if not can_connect(last,w,norm_conf,
                               auto_recovery,anti_loop,
                               round_trip,route,pos_shift):
                continue
            route.append(w)
            dfs(route)
            route.pop()

    for st in starts:
        dfs([st])

    # ソート
    if sort_mode=="kana":
        routes.sort()
    elif sort_mode=="len_asc":
        routes.sort(key=lambda r: len(r))
    elif sort_mode=="len_desc":
        routes.sort(key=lambda r: -len(r))
    elif sort_mode=="random":
        import random
        random.shuffle(routes)

    if exact_limit:
        routes=routes[:exact_limit]

    return routes

# =========================
# API
# =========================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/get_dictionary")
def get_dictionary():
    return jsonify({
        "country": COUNTRY,
        "capital": CAPITAL,
        "custom": CUSTOM
    })

@app.route("/search", methods=["POST"])
def search():
    d=request.get_json(force=True)

    data=dict(
        start_word=d.get("start_word","").strip(),
        start_char=d.get("start_char","").strip(),
        must_raw=d.get("must_char",""),
        end_char=d.get("end_char","").strip(),
        end_word=d.get("end_word","").strip(),

        all_start_char=d.get("all_start_char","").strip(),
        all_end_char=d.get("all_end_char","").strip(),
        valid_chars=d.get("valid_chars","").strip(),
        exclude_chars=d.get("exclude_chars","").strip(),
        ban_start_chars=d.get("ban_start_chars","").strip(),

        target_total_len=int(d["target_total_len"]) if d.get("target_total_len") else None,
        len_mode=d.get("len_mode","free"),
        sort_mode=d.get("sort_mode","kana"),

        use_shift=bool(d.get("use_shift",False)),
        ks_abs=int(d.get("ks_abs",1)),
        shift_mode=d.get("shift_mode","abs"),
        pos_shift=int(d.get("pos_shift",0)),

        unify_small=bool(d.get("unify_small",False)),
        allow_daku=bool(d.get("allow_daku",False)),
        allow_handaku=bool(d.get("allow_handaku",False)),

        auto_recovery=bool(d.get("auto_recovery",False)),
        round_trip=bool(d.get("round_trip",False)),
        char_limit_mode=bool(d.get("char_limit_mode",False)),

        exclude_conjugate=bool(d.get("exclude_conjugate",False)),
        conjugate_merge=bool(d.get("conjugate_merge",False)),

        anti_loop=bool(d.get("anti_loop",False)),

        timeout=int(d.get("timeout",15)) if d.get("timeout_enabled",True) else 0,
        limit=int(d.get("limit",1500)) if d.get("limit_enabled",True) else 0,
        exact_limit=int(d["exact_limit"]) if d.get("exact_limit") else None,

        categories=d.get("categories",["country","capital"]),
        red_words=set(d.get("red_words",[])),
        blue_words=set(d.get("blue_words",[]))
    )

    words=[]
    if "country" in data["categories"]: words.extend(COUNTRY)
    if "capital" in data["categories"]: words.extend(CAPITAL)
    if "custom" in data["categories"]: words.extend(CUSTOM)

    words=[w for w in words if w not in data["red_words"]]

    routes=search_routes(
        words=words,
        start_word=data["start_word"],
        start_char=data["start_char"],
        end_word=data["end_word"],
        end_char=data["end_char"],
        all_start_char=data["all_start_char"],
        all_end_char=data["all_end_char"],
        valid_chars=data["valid_chars"],
        exclude_chars=data["exclude_chars"],
        ban_start_chars=data["ban_start_chars"],
        target_total_len=data["target_total_len"],
        len_mode=data["len_mode"],
        sort_mode=data["sort_mode"],
        use_shift=data["use_shift"],
        ks_abs=data["ks_abs"],
        shift_mode=data["shift_mode"],
        pos_shift=data["pos_shift"],
        unify_small=data["unify_small"],
        allow_daku=data["allow_daku"],
        allow_handaku=data["allow_handaku"],
        auto_recovery=data["auto_recovery"],
        round_trip=data["round_trip"],
        char_limit_mode=data["char_limit_mode"],
        exclude_conjugate=data["exclude_conjugate"],
        conjugate_merge=data["conjugate_merge"],
        must_raw=data["must_raw"],
        blue_words=data["blue_words"],
        timeout=data["timeout"],
        limit=data["limit"],
        exact_limit=data["exact_limit"],
        anti_loop=data["anti_loop"]
    )

    return jsonify({"routes":routes,"count":len(routes)})

if __name__=="__main__":
    app.run(debug=True)
