#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""poster-dl: 按片名下载影视剧海报,画质可选。

只用 Python 标准库(3.8+),不交互,所有结果以 JSON 输出到 stdout。
默认走免 key 的 IMDb 搜索接口;配置了 TMDB key 后自动改用 TMDB,
并解锁分季海报、指定语言版本、多张候选。配置后的默认选择是作品原语言里评分最高的一张。

退出码: 0 成功 | 1 其他错误 | 2 参数错误 | 3 有多个候选需要选择
        4 没找到 | 5 需要 TMDB key(或 key 无效)
"""
import argparse
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IMDB_SUGGEST = "https://v3.sg.media-imdb.com/suggestion/x/"
TMDB_API = os.environ.get("POSTER_DL_TMDB_API", "https://api.themoviedb.org/3").rstrip("/")
TMDB_IMG = os.environ.get("POSTER_DL_TMDB_IMG", "https://image.tmdb.org/t/p").rstrip("/")
CONFIG_PATH = Path(
    os.environ.get("POSTER_DL_CONFIG") or Path.home() / ".config" / "poster-dl" / "config.json"
)
DEFAULT_OUT = Path.home() / "Downloads" / "posters"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# 画质档位 -> 目标宽度(像素); original 为源站原图
QUALITIES = {"low": 342, "medium": 500, "high": 780, "original": None}

IMDB_MOVIE_KINDS = {"movie", "tvMovie", "short", "video"}
IMDB_TV_KINDS = {"tvSeries", "tvMiniSeries", "tvSpecial", "tvShort"}
IMDB_MAJOR_KINDS = {"movie", "tvMovie", "tvSeries", "tvMiniSeries"}

EXIT = {"ok": 0, "error": 1, "ambiguous": 3, "not_found": 4, "need_key": 5, "bad_key": 5}


class Fail(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class NetError(Exception):
    def __init__(self, message, code=None, body=b""):
        super().__init__(message)
        self.code = code
        self.body = body


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(EXIT.get(payload.get("status"), 1))


def script_cmd():
    return 'python3 "%s"' % Path(__file__).resolve()


# ---------- 网络 ----------

def http_get(url, headers=None, timeout=25, retries=2):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            body = e.read() if e.fp else b""
            if e.code < 500 and e.code != 429:
                raise NetError("HTTP %s" % e.code, code=e.code, body=body)
            last = NetError("HTTP %s" % e.code, code=e.code, body=body)
        except Exception as e:  # 超时、DNS、TLS 等
            last = NetError("%s: %s" % (type(e).__name__, e))
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last


def get_json(url, headers=None):
    data, _ = http_get(url, headers)
    return json.loads(data.decode("utf-8"))


# ---------- 配置 ----------

def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def tmdb_key():
    """返回 (key, 来源)。v3 key 和 v4 读取令牌都接受。"""
    env = (os.environ.get("TMDB_API_KEY") or "").strip()
    if env:
        return env, "环境变量 TMDB_API_KEY"
    key = (load_config().get("tmdb_key") or "").strip()
    return (key, str(CONFIG_PATH)) if key else (None, None)


def mask(key):
    return key[:4] + "…" + key[-4:] if len(key) > 10 else "…"


def key_guide(feature):
    return {
        "need": feature,
        "steps": [
            "打开 https://www.themoviedb.org/signup 注册账号并验证邮箱(免费)",
            "打开 https://www.themoviedb.org/settings/api 申请 API,类型选 Developer,用途填个人使用即可",
            "复制页面上的「API Key」或「API Read Access Token」,两种都可以",
            "运行: %s config set-key <复制的key>" % script_cmd(),
        ],
        "note": "key 只保存在本机 %s,配置一次后所有 agent 共用。" % CONFIG_PATH,
    }


def need_key(feature, without_key=None):
    extra = {"guide": key_guide(feature)}
    if without_key:
        extra["without_key"] = without_key
    raise Fail("need_key", "%s需要 TMDB key,目前没有配置。" % feature, **extra)


# ---------- TMDB ----------

def tmdb_get(path, key, **params):
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ") or len(key) > 60:
        headers["Authorization"] = "Bearer " + key
    else:
        params["api_key"] = key
    params = {k: v for k, v in params.items() if v not in (None, "")}
    url = TMDB_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        return get_json(url, headers)
    except NetError as e:
        if e.code == 401:
            raise Fail(
                "bad_key",
                "TMDB 拒绝了当前的 key(来源: %s)。" % tmdb_key()[1],
                guide=key_guide("重新配置"),
            )
        if e.code == 404:
            raise Fail("not_found", "TMDB 上没有这个条目: %s" % path)
        raise


def has_cjk(s):
    return bool(re.search(r"[぀-ヿ㐀-鿿가-힯]", s or ""))


def tmdb_cand(item, kind=None):
    kind = kind or item.get("media_type")
    if kind not in ("movie", "tv"):
        return None
    date = item.get("release_date") or item.get("first_air_date") or ""
    title = item.get("title") or item.get("name") or ""
    orig = item.get("original_title") or item.get("original_name") or ""
    overview = (item.get("overview") or "").strip().replace("\n", " ")
    return {
        "id": "tmdb:%s:%s" % (kind, item["id"]),
        "title": title,
        "original_title": orig if orig != title else None,
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "type": kind,
        "detail": overview[:80] + ("…" if len(overview) > 80 else ""),
        "preview": TMDB_IMG + "/w342" + item["poster_path"] if item.get("poster_path") else None,
        "source": "tmdb",
        "_poster_path": item.get("poster_path"),
        "_orig_lang": item.get("original_language"),
    }


def tmdb_search(query, year, kind, key):
    lang = "zh-CN" if has_cjk(query) else "en-US"
    if kind == "movie":
        data = tmdb_get("/search/movie", key, query=query, language=lang, year=year)
    elif kind == "tv":
        data = tmdb_get("/search/tv", key, query=query, language=lang, first_air_date_year=year)
    else:
        data = tmdb_get("/search/multi", key, query=query, language=lang)
    cands = [tmdb_cand(it, kind) for it in data.get("results", [])]
    return [c for c in cands if c]


def tmdb_by_id(kind, tid, key, lang):
    return tmdb_cand(tmdb_get("/%s/%s" % (kind, tid), key, language=lang), kind)


def tmdb_from_imdb(imdb_id, key, lang):
    data = tmdb_get("/find/" + imdb_id, key, external_source="imdb_id", language=lang)
    for field, kind in (("movie_results", "movie"), ("tv_results", "tv")):
        if data.get(field):
            return tmdb_cand(data[field][0], kind)
    return None


def tmdb_posters(cand, season, key):
    _, kind, tid = cand["id"].split(":")
    path = "/%s/%s/images" % (kind, tid)
    if season is not None:
        if kind != "tv":
            raise Fail("error", "--season 只能用于剧集,「%s」是电影。" % cand["title"])
        path = "/tv/%s/season/%s/images" % (tid, season)
    posters = tmdb_get(path, key).get("posters", [])
    posters.sort(
        key=lambda p: (p.get("vote_average") or 0, p.get("vote_count") or 0, p.get("width") or 0),
        reverse=True,
    )
    return posters


def filter_lang(posters, lang):
    if not lang or lang == "all":
        return posters
    want = None if lang == "none" else lang.lower()
    return [p for p in posters if (p.get("iso_639_1") or None) == want]


# ---------- IMDb(免 key) ----------

def imdb_cand(d):
    kind = d.get("qid")
    if not str(d.get("id", "")).startswith("tt") or not kind:
        return None
    if kind not in IMDB_MOVIE_KINDS and kind not in IMDB_TV_KINDS:
        return None
    img = d.get("i") or {}
    return {
        "id": d["id"],
        "title": d.get("l") or "",
        "original_title": None,
        "year": d.get("y"),
        "type": "tv" if kind in IMDB_TV_KINDS else "movie",
        "kind": kind,
        "detail": d.get("s") or "",
        "preview": resize_imdb(img["imageUrl"], 342, img.get("width")) if img.get("imageUrl") else None,
        "source": "imdb",
        "_image": img,
    }


def imdb_search(query):
    data = get_json(IMDB_SUGGEST + urllib.parse.quote(query.strip(), safe="") + ".json")
    cands = [imdb_cand(d) for d in data.get("d", [])]
    return [c for c in cands if c]


def resize_imdb(url, width, orig_width=None):
    """IMDb 图片地址里 ._V1_ 之后是缩放指令;不放大原图。"""
    if width is None or (orig_width and orig_width <= width):
        return re.sub(r"\._V1_[^/]*\.(\w+)$", r"._V1_.\1", url)
    return re.sub(r"\._V1_[^/]*\.(\w+)$", r"._V1_UX%d_.\1" % width, url)


# ---------- 选片 ----------

def norm(s):
    return re.sub(r"[\W_]+", "", (s or "").casefold())


def public(cand):
    return {k: v for k, v in cand.items() if not k.startswith("_") and v is not None}


def narrow(cands, year, kind):
    if kind:
        cands = [c for c in cands if c["type"] == kind]
    if year:
        exact = [c for c in cands if c["year"] == year]
        cands = exact or [c for c in cands if c["year"] and abs(c["year"] - year) == 1]
    return cands


def choose(cands, query):
    """唯一命中才自动选;否则返回 None 交给调用方列候选。"""
    if len(cands) == 1:
        return cands[0]
    q = norm(query)
    exact = [c for c in cands if q in (norm(c["title"]), norm(c.get("original_title")))]
    if len(exact) == 1:
        return exact[0]
    major = [c for c in exact if c.get("kind", "movie") in IMDB_MAJOR_KINDS]
    if len(major) == 1:
        return major[0]
    return None


def pick_source(args, key, feature=None):
    """返回 'tmdb' 或 'imdb'。feature 非空表示这次请求用到了只有 TMDB 才有的功能。"""
    if args.source == "imdb":
        if feature:
            raise Fail("error", "%s只有 TMDB 源支持,请去掉 --source imdb。" % feature)
        return "imdb"
    if key:
        return "tmdb"
    if feature or args.source == "tmdb":
        need_key(feature or "使用 TMDB 源", getattr(args, "_without_key", None))
    return "imdb"


def search(query, args, key, source, warnings):
    if source == "tmdb":
        try:
            return narrow(tmdb_search(query, args.year, args.type, key), args.year, args.type), "tmdb"
        except NetError as e:
            if args.source == "tmdb" or args._tmdb_only:
                raise
            warnings.append("TMDB 连接失败(%s),这次改用 IMDb 源。" % e)
    return narrow(imdb_search(query), args.year, args.type), "imdb"


def resolve(args, key, source, warnings):
    """根据 --id 或片名确定唯一的一部作品。"""
    if args.id:
        # 片名语言跟着用户的说法走: 给了中文片名就取中文标题
        lang = "zh-CN" if has_cjk("%s %s" % (args.query or "", args.name or "")) else "en-US"
        m = re.fullmatch(r"tmdb:(movie|tv):(\d+)", args.id)
        if m:
            if not key:
                need_key("按 TMDB 编号下载")
            return tmdb_by_id(m.group(1), m.group(2), key, lang)
        if not re.fullmatch(r"tt\d+", args.id):
            raise Fail("error", "--id 的格式应为 tt1234567 或 tmdb:movie:123 / tmdb:tv:123。")
        if source == "tmdb":
            cand = tmdb_from_imdb(args.id, key, lang)
            if cand:
                return cand
            if args._tmdb_only:
                raise Fail("not_found", "TMDB 上找不到 %s 对应的条目。" % args.id)
            warnings.append("TMDB 上找不到 %s,改用 IMDb 源。" % args.id)
        cands = [c for c in imdb_search(args.id) if c["id"] == args.id]
        if not cands:
            raise Fail("not_found", "IMDb 上找不到 %s。" % args.id)
        return cands[0]

    cands, used = search(args.query, args, key, source, warnings)
    if not cands:
        raise Fail(
            "not_found",
            "没有找到「%s」。" % args.query,
            hint="可以换成原名或英文名再试,或者去掉 --year / --type。",
        )
    # 没有海报的条目下载不了,有别的选择时不让它们参与
    cands = [c for c in cands if c.get("preview")] or cands
    if args.first:
        return cands[0]
    cand = choose(cands, args.query)
    if cand is None:
        raise Fail(
            "ambiguous",
            "「%s」有 %d 个候选,请选定后用 --id 再下载。" % (args.query, min(len(cands), args.limit)),
            candidates=[public(c) for c in cands[: args.limit]],
            source=used,
        )
    return cand


# ---------- 图片 ----------

def image_info(data):
    """返回 (扩展名, 宽, 高);认不出尺寸时宽高为 None。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return "jpg", w, h
            i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
        return "jpg", None, None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", None, None
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return "gif", w, h
    return None, None, None


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", s)
    return re.sub(r"\s+", " ", s).strip(" .") or "poster"


def out_dir(args):
    d = args.out or os.environ.get("POSTER_DL_DIR") or load_config().get("out_dir") or DEFAULT_OUT
    return Path(d).expanduser()


def save_image(url, base, args):
    try:
        data, ctype = http_get(url, timeout=60)
    except NetError as e:
        raise Fail("error", "图片下载失败: %s" % e, url=url)
    ext, w, h = image_info(data)
    if not ext:
        raise Fail("error", "下载到的不是图片(Content-Type: %s)。" % ctype, url=url)
    folder = out_dir(args)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("%s.%s" % (base, ext))
    n = 2
    while path.exists() and not args.force:
        path = folder / ("%s (%d).%s" % (base, n, ext))
        n += 1
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path, w, h, len(data)


# ---------- 子命令 ----------

def cmd_search(args):
    key, _ = tmdb_key()
    warnings = []
    args._tmdb_only = False
    cands, used = search(args.query, args, key, pick_source(args, key), warnings)
    if not cands:
        raise Fail("not_found", "没有找到「%s」。" % args.query, hint="可以换成原名或英文名再试。")
    out = {"status": "ok", "source": used, "candidates": [public(c) for c in cands[: args.limit]]}
    if warnings:
        out["warnings"] = warnings
    emit(out)


def tmdb_feature(args):
    if args.season is not None:
        return "下载分季海报"
    if getattr(args, "pick", None):
        return "从多张候选里挑选"
    if args.lang:
        return "指定海报语言版本"
    return None


def poster_row(i, p):
    return {
        "n": i,
        "lang": p.get("iso_639_1") or "none",
        "width": p.get("width"),
        "height": p.get("height"),
        "votes": "%s (%s 票)" % (p.get("vote_average"), p.get("vote_count")),
        "preview": TMDB_IMG + "/w342" + p["file_path"],
    }


def cmd_list(args):
    key, _ = tmdb_key()
    args._tmdb_only = True
    args._without_key = "不配 key 时每部作品只有 IMDb 的一张主海报,直接 download 即可。"
    warnings = []
    cand = resolve(args, key, pick_source(args, key, "列出全部海报版本"), warnings)
    posters = filter_lang(tmdb_posters(cand, args.season, key), args.lang)
    if not posters:
        raise Fail("not_found", "「%s」没有符合条件的海报。" % cand["title"], hint="去掉 --lang 看全部版本。")
    emit({
        "status": "ok",
        "title": cand["title"],
        "year": cand["year"],
        "id": cand["id"],
        "total": len(posters),
        "posters": [poster_row(i, p) for i, p in enumerate(posters[: args.limit], 1)],
        "hint": "下载某一张: download --id %s --pick <n>%s%s" % (
            cand["id"],
            " --season %s" % args.season if args.season is not None else "",
            " --lang %s" % args.lang if args.lang else "",
        ),
    })


def cmd_download(args):
    key, _ = tmdb_key()
    warnings = []
    feature = tmdb_feature(args)
    args._tmdb_only = bool(feature)
    if args.lang and args.season is None and not args.pick:
        args._without_key = "去掉 --lang 可以直接下载默认版本(IMDb 主海报,通常是原版)。"
    source = pick_source(args, key, feature)
    cand = resolve(args, key, source, warnings)
    target = QUALITIES[args.quality]
    out = {"status": "ok"}

    if cand["source"] == "tmdb":
        posters = filter_lang(tmdb_posters(cand, args.season, key), args.lang)
        if args.pick:
            if not 1 <= args.pick <= len(posters):
                raise Fail("error", "--pick 超出范围,这个条件下共有 %d 张。" % len(posters))
            chosen = posters[args.pick - 1]
        else:
            if args.lang and not posters:
                raise Fail(
                    "not_found",
                    "「%s」没有 %s 版本的海报。" % (cand["title"], args.lang),
                    hint="去掉 --lang 下载默认版本,或用 list 看有哪些语言。",
                )
            # 没指定语言时只在作品原语言的版本里选,没有原语言版本再看全部
            pool = posters if args.lang else filter_lang(posters, cand.get("_orig_lang")) or posters
            chosen = pool[0] if pool else None
            if pool and args.largest:
                # pool 已按评分排序,max 在同宽度里取到的是评分最高的那张
                chosen = max(pool, key=lambda p: p.get("width") or 0)
            elif pool:
                cw = chosen.get("width") or 0
                bigger = [p for p in pool if (p.get("width") or 0) >= cw * 1.2]
                if bigger and (target is None or cw < target):
                    out["hint"] = (
                        "这是评分最高的版本,宽 %dpx;同条件下还有 %d 张更大的(最宽 %dpx)。"
                        "加 --largest 直接取最大的一张,或用 list 查看后 --pick 挑选。"
                        % (cw, len(bigger), max(p.get("width") or 0 for p in bigger))
                    )
            if chosen is None:
                # 图片列表为空时退回条目自带的主海报
                main = cand.get("_poster_path")
                if args.season is not None:
                    tid = cand["id"].split(":")[2]
                    main = tmdb_get("/tv/%s/season/%s" % (tid, args.season), key).get("poster_path")
                chosen = {"file_path": main} if main else None
        if not chosen:
            what = cand["title"] + (" 第 %d 季" % args.season if args.season is not None else "")
            raise Fail("not_found", "TMDB 上「%s」还没有海报。" % what)
        ow = chosen.get("width")
        size = "original" if target is None or (ow and ow <= target) else "w%d" % target
        url = "%s/%s%s" % (TMDB_IMG, size, chosen["file_path"])
        out["lang"] = chosen.get("iso_639_1") or "none"
    else:
        img = cand.get("_image") or {}
        if not img.get("imageUrl"):
            raise Fail("not_found", "IMDb 上「%s」没有海报。" % cand["title"], guide=key_guide("换用 TMDB 源"))
        ow = img.get("width")
        url = resize_imdb(img["imageUrl"], target, ow)
        if key is None and ow and ow < 1000 and args.quality in ("original", "high"):
            out["hint"] = "免 key 源只有这一张,原图宽 %dpx。配置 TMDB key 后通常能拿到更大的版本,运行 config guide 查看步骤。" % ow

    if target and ow and ow < target:
        warnings.append("原图宽度只有 %dpx,低于 %s 档的 %dpx,已下载原图,没有放大。" % (ow, args.quality, target))

    title = args.name or cand["title"]
    base = safe_name(title) + (" (%s)" % cand["year"] if cand["year"] else "")
    if args.season is not None:
        base += " S%02d" % args.season
    base += " [%s]" % args.quality
    path, w, h, size_bytes = save_image(url, base, args)

    out.update({
        "file": str(path),
        "title": cand["title"],
        "year": cand["year"],
        "type": cand["type"],
        "id": cand["id"],
        "source": cand["source"],
        "quality": args.quality,
        "width": w,
        "height": h,
        "bytes": size_bytes,
        "url": url,
    })
    if args.season is not None:
        out["season"] = args.season
    if warnings:
        out["warnings"] = warnings
    emit(out)


def cmd_config(args):
    cfg = load_config()
    if args.action == "guide":
        emit({"status": "ok", "guide": key_guide("解锁分季海报、语言版本和多张候选")})
    if args.action == "set-key":
        if not args.value:
            raise Fail("error", "用法: config set-key <key>")
        key = args.value.strip()
        out = {"status": "ok", "saved_to": str(CONFIG_PATH), "tmdb_key": mask(key)}
        try:
            tmdb_get("/configuration", key)
            out["verified"] = True
        except Fail:
            raise Fail("bad_key", "TMDB 不认这个 key,没有保存。请检查是否复制完整。", guide=key_guide("重新配置"))
        except NetError as e:
            out["verified"] = False
            out["warnings"] = ["已保存,但没能联网验证(%s)。" % e]
        cfg["tmdb_key"] = key
        save_config(cfg)
        emit(out)
    if args.action == "clear-key":
        cfg.pop("tmdb_key", None)
        save_config(cfg)
        emit({"status": "ok", "tmdb_key": None})
    if args.action == "set-dir":
        if not args.value:
            raise Fail("error", "用法: config set-dir <目录>")
        cfg["out_dir"] = str(Path(args.value).expanduser())
        save_config(cfg)
        emit({"status": "ok", "out_dir": cfg["out_dir"]})
    key, where = tmdb_key()
    args.out = None
    emit({
        "status": "ok",
        "tmdb_key": mask(key) if key else None,
        "tmdb_key_from": where,
        "default_source": "tmdb" if key else "imdb",
        "out_dir": str(out_dir(args)),
        "config_path": str(CONFIG_PATH),
    })


def build_parser():
    p = argparse.ArgumentParser(prog="poster_dl.py", description="下载影视剧海报,画质可选。输出为 JSON。")
    sub = p.add_subparsers(dest="cmd", required=True)

    def target(sp, need_query=True):
        sp.add_argument("query", nargs="?" if not need_query else None, help="片名(中文、英文、原名均可)")
        sp.add_argument("--year", type=int, help="上映/首播年份,用来区分同名作品")
        sp.add_argument("--type", choices=["movie", "tv"], help="只要电影或只要剧集")
        sp.add_argument("--source", choices=["auto", "imdb", "tmdb"], default="auto",
                        help="auto: 配了 TMDB key 用 TMDB,否则用 IMDb(默认)")
        sp.add_argument("--limit", type=int, default=8, help="最多返回多少个候选(默认 8)")

    s = sub.add_parser("search", help="搜索作品,列出候选")
    target(s)
    s.set_defaults(func=cmd_search)

    for name, func, helptext in (
        ("download", cmd_download, "下载海报"),
        ("list", cmd_list, "列出一部作品的全部海报版本(需要 TMDB key)"),
    ):
        d = sub.add_parser(name, help=helptext)
        target(d, need_query=False)
        d.add_argument("--id", help="直接指定作品: tt1234567 或 tmdb:movie:123 / tmdb:tv:123")
        d.add_argument("--first", action="store_true", help="有多个候选时不询问,直接取第一个")
        d.add_argument("--season", type=int, help="剧集的第几季(0 为特别篇;需要 TMDB key)")
        d.add_argument("--lang", help="海报语言: zh / en / ja / ko … 或 none(无字版);需要 TMDB key")
        d.add_argument("--name", help="保存用的片名(默认用数据源返回的片名)")
        if name == "download":
            d.add_argument("--quality", "-q", choices=list(QUALITIES), default="original",
                           help="low=342px medium=500px high=780px original=原图(默认)")
            d.add_argument("--pick", type=int, help="下载 list 结果里的第 n 张(需要 TMDB key)")
            d.add_argument("--largest", action="store_true",
                           help="有多个版本时取分辨率最大的一张,而不是评分最高的(免 key 时只有一张,此项无效果)")
            d.add_argument("--out", "-o", help="保存目录(默认 ~/Downloads/posters)")
            d.add_argument("--force", action="store_true", help="同名文件直接覆盖(默认自动加序号)")
        d.set_defaults(func=func)

    c = sub.add_parser("config", help="查看或修改配置")
    c.add_argument("action", nargs="?", default="show",
                   choices=["show", "guide", "set-key", "clear-key", "set-dir"])
    c.add_argument("value", nargs="?")
    c.set_defaults(func=cmd_config)
    return p


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_parser().parse_args()
    if args.cmd in ("download", "list") and not args.query and not args.id:
        emit({"status": "error", "message": "需要片名或 --id。"})
    if not hasattr(args, "name"):
        args.name = None
    try:
        args.func(args)
    except Fail as e:
        out = {"status": e.status, "message": e.message}
        out.update(e.extra)
        emit(out)
    except NetError as e:
        emit({"status": "error", "message": "网络请求失败: %s" % e,
              "hint": "检查网络或代理后重试。"})
    except Exception as e:
        emit({"status": "error", "message": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
