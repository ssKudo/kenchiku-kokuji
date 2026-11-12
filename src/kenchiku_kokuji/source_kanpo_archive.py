"""一次情報源4: 官報アーカイブ(kanpo-downloader)による過去分の補完。

公式の官報サイトは直近90日分しか無料公開していないため、それ以前の
期間(2025-04-01〜公式窓の開始前日)は、第三者が全号PDFを公開している
GitHub 組織 kanpo-downloader のアーカイブ(kanpo-2025 / kanpo-2026)
から補う。アーカイブには目次HTMLが無いので、検出は本文のOCRテキスト層
だけから行う:

- 号PDFの全ページのテキストを空白除去して連結する(公式源と同じ正規化)
- 真の見出しは、直前が項目区切りの「〇」で、直後が助詞等でなく、
  発出者名が既知の省庁名として分解できるものだけに限る
  (目次や本文中の「令和○年○○省告示第○号」という参照は、
  発出者名の直前が「年」になる・直後が「の」等になるため除外できる)
- 各見出しから次の見出しまでをその告示の本文として切り出す
- 分類は公式源と同じ物差し: 国土交通省は冒頭に法令名か建築キーワード、
  総務省は冒頭に「消防」、全発出者に被改正告示が台帳にある改正の第2ティア

政府調達(c)と月次索引系(m)のセクションは告示見出しを持たないことを
標本で確認したため対象外とする。本紙(h)・号外(g)・特別号外(t)のみ。
"""
from __future__ import annotations

import re
import time
from urllib.request import Request, urlopen

from .common import ISSUER_KEYS, USER_AGENT, kanji_to_int, make_id, parse_notice_number
from .source_kanpo import (ABBREV, AMEND_REF, ERA_OFFSET, TITLE_KEYWORDS,
                           _parse_entry_number)

ARCHIVE_ORG = "kanpo-downloader"
SECTIONS = ("h", "g", "t")  # 本紙・号外・特別号外(c=政府調達, m=月次索引は除く)
MAX_TEXT_CHARS = 12000  # 公式源 _slice_notice の上限に合わせる
HEAD_CHARS = 2000       # 分類に使う冒頭の長さ

# 見出しの発出者名として認める省庁名 → ID用キー。
# common.ISSUER_KEYS に無い機関は、境界検出と第2ティア判定のために持つ。
EXTRA_ISSUERS = [
    ("国家公安委員会", "npsc"), ("個人情報保護委員会", "ppc"),
    ("原子力規制委員会", "nra"), ("公正取引委員会", "jftc"),
    ("中央労働委員会", "clb"), ("運輸安全委員会", "jtsb"),
    ("内閣府", "cao"), ("宮内庁", "kunaicho"), ("警察庁", "npa"),
    ("金融庁", "fsa"), ("消費者庁", "caa"), ("こども家庭庁", "cfa"),
    ("デジタル庁", "digital"), ("復興庁", "reconstruction"),
    ("消防庁", "fdma"), ("気象庁", "jma"), ("海上保安庁", "jcg"),
    ("出入国在留管理庁", "isa"), ("国税庁", "nta"), ("林野庁", "rinya"),
    ("水産庁", "suisan"), ("特許庁", "jpo"), ("中小企業庁", "chusho"),
    ("観光庁", "kanko"), ("文化庁", "bunka"), ("スポーツ庁", "sports"),
    ("資源エネルギー庁", "enecho"), ("原子力規制庁", "nra_sec"),
]
KNOWN_ISSUERS = dict(ISSUER_KEYS + EXTRA_ISSUERS)
# 注意: _ALT は「・」の繰り返しなど他の要素と連結する際は必ず (?:_ALT) で
# 括ること。素の _ALT を (?:・_ALT)* のように書くと、・が最初の代替に
# しか掛からず共管告示のチェーンが検出できない(2026-11-11に実測で判明)。
_ALT = "|".join(re.escape(n) for n in sorted(KNOWN_ISSUERS, key=len, reverse=True))
_CHAIN = rf"(?:{_ALT})(?:・(?:{_ALT}))*"
NUM = re.compile(r"告示第([〇一二三四五六七八九十百千]+)号")
# 見出し直後がこれらの文字なら、見出しではなく本文中の参照とみなす
REF_NEXT = set("のにをはがへと、。）」』及等（")
# 発令文言: 「令和八年十月三十日国土交通大臣井林辰憲」の形。
# 官報の告示は見出し→根拠文→発令日＋発令者→件名→本文の順で、
# 実測では発令文言は見出しから201字以内に現れる(公式24件中18件で確認。
# 残り3件は公式側の切り出しが途中で切れていたもの)。
# 日付は漢数字・算用数字のどちらも許す(OCR表記が揺れるため)。
PROM = re.compile(
    r"(明治|大正|昭和|平成|令和)(元|[〇一二三四五六七八九十百千\d]+)年"
    r"([〇一二三四五六七八九十百千\d]+)月([〇一二三四五六七八九十百千\d]+)日"
    r"[一-龥]{0,12}?(大臣|長官|委員長|総裁)")
PROM_WINDOW = 2000


def issuer_key_of(chain: str) -> str:
    """「厚生労働省・国土交通省」のような発出者チェーンをID用キーにする。"""
    keys = sorted({KNOWN_ISSUERS[n] for n in chain.split("・")})
    if len(keys) >= 2:
        return "joint-" + "-".join(keys)
    return keys[0] if keys else "unknown"


def find_headings(full: str) -> list[dict]:
    """連結テキストから真の見出し位置を返す。

    「告示第N号」の出現ごとに、直前に既知の発出者名チェーンが
    ぴったり終わるものを拾う。参照(「令和○年○○省告示第○号の…」
    「(…告示第○号)」等)を落とすため、さらに次の2条件を課す:
    チェーンの直前が「〇」(官報の項目区切り)か文頭であること、
    見出し直後が助詞等でないこと。
    既知の省庁名に当てはまらない発出者(地方整備局など)の見出しは、
    境界だけの見出し(boundary_only)として拾い、セグメントの切り分けに
    使う(本文が次の告示まで延びて誤分類されるのを防ぐ)。
    """
    out = []
    for m in NUM.finditer(full):
        prefix = full[max(0, m.start() - 60): m.start()]
        cm = re.search(rf"({_CHAIN})$", prefix)
        if not cm:
            continue
        chain = cm.group(1)
        start = m.start() - len(chain)
        if start > 0 and full[start - 1] != "〇":
            continue
        if m.end() < len(full) and full[m.end()] in REF_NEXT:
            continue
        number = kanji_to_int(m.group(1))
        if number is None:
            continue
        out.append({"start": start, "end": m.end(), "chain": chain,
                    "issuer_key": issuer_key_of(chain), "number": number})
    # 境界だけの見出し(地方整備局など): 既知の見出しと重ならないものだけ追加
    GEN = re.compile(r"([一-龥]{2,12}(?:省|庁|局|委員会|府|院))告示第"
                     r"([〇一二三四五六七八九十百千]+)号")
    for m in GEN.finditer(full):
        if any(h["start"] <= m.start() < h["end"] for h in out):
            continue
        if m.start() > 0 and full[m.start() - 1] != "〇":
            continue
        if m.end() < len(full) and full[m.end()] in REF_NEXT:
            continue
        number = kanji_to_int(m.group(2))
        if number is None:
            continue
        out.append({"start": m.start(), "end": m.end(), "chain": m.group(1),
                    "issuer_key": None, "number": number, "boundary_only": True})
    out.sort(key=lambda h: h["start"])
    return out


def promulgation_offset(text: str, window: int = PROM_WINDOW) -> int | None:
    """発令文言(和暦日付＋発令者)の位置を返す。無ければ None。"""
    m = PROM.search(text[:window])
    return m.start() if m else None


def parse_toc_number(inner: str, last_issuer):
    """目次の略式番号(国土交通一三一六/同一三三三/総務・国土交通一/
    関東地方整備局二三九〜二四三/法務省告示第八百四十一号)から
    [(issuer_key, number)] を返す。範囲表記は展開する(上限20件)。
    末尾の last_issuer も返す。"""
    inner = inner.strip()
    if "〜" in inner:
        head, _, tail = inner.partition("〜")
        base = parse_toc_number(head, last_issuer)[0]
        end_num = kanji_to_int(re.sub(r"[^\d〇一二三四五六七八九十百千]", "", tail))
        if not base or end_num is None:
            return [], last_issuer
        key, start_num = base[0]
        if end_num < start_num or end_num - start_num > 19:
            return [base[0]], last_issuer
        return [(key, n) for n in range(start_num, end_num + 1)], last_issuer
    if "・" in inner and "告示第" not in inner:
        m = re.match(r"(.+?)([〇一二三四五六七八九]+)$", inner)
        if m:
            keys = sorted({ABBREV[p][0] for p in m.group(1).split("・")
                           if p in ABBREV})
            num = kanji_to_int(m.group(2))
            if keys and num is not None:
                key = keys[0] if len(keys) == 1 else "joint-" + "-".join(keys)
                return [(key, num)], (key, inner)
    key, full, num, new_last = _parse_entry_number(inner, last_issuer)
    if key is None or num is None:
        return [], last_issuer
    return [(key, num)], new_last


def parse_toc(issue_pages: list[list[str]], last_issuer=None):
    """各号の先頭ページ群から目次を読み、{(issuer_key, number): 件名} を返す。

    印刷版の目次は「〇<件名>（<略式番号>）」の並びで、公式サイトの
    fullcontents と同じ略式番号(同…/範囲表記を含む)を使う。
    件名は公式パイプラインの分類(法令名・キーワード・第2ティア)と
    同じ物差しで使うためのもので、本文冒頭だけでは法令名が出ない
    届出型告示(例: 1333号は本文が告示486号の引用のみ)を拾う。
    """
    toc: dict[tuple[str, int], str] = {}
    text = "".join("".join(p[:3]) for p in issue_pages)
    for m in re.finditer(r"[（(]([^）)]{1,40})[）)]", text):
        entries, last_issuer = parse_toc_number(m.group(1), last_issuer)
        if not entries:
            continue
        start = text.rfind("〇", 0, m.start())
        title = text[start + 1: m.start()].strip() if start >= 0 else ""
        if not (3 <= len(title) <= 200) or "。" in title:
            continue
        if PROM.search(title) or "発行内閣府" in title:
            continue
        for key, num in entries:
            toc.setdefault((key, num), title)
    return toc, last_issuer


def segment_issue(pages: list[str]) -> list[dict]:
    """空白除去済みページ文字列の列を、告示ごとのセグメントに切る。"""
    offsets = []
    pos = 0
    for p in pages:
        offsets.append(pos)
        pos += len(p)
    full = "".join(pages)
    heads = find_headings(full)
    segs = []
    for i, h in enumerate(heads):
        end = heads[i + 1]["start"] if i + 1 < len(heads) else len(full)
        page = 1
        for pi, off in enumerate(offsets):
            if off <= h["start"]:
                page = pi + 1
        segs.append({**h, "page": page,
                     "text": full[h["start"]: min(end, h["start"] + MAX_TEXT_CHARS)]})
    return segs


def _amend_target_id(head: str) -> str | None:
    """冒頭が「○○省告示第N号の一部を改正」なら被改正告示のIDを返す。"""
    m = AMEND_REF.search(head[:400])
    if not m:
        return None
    y = 1 if m.group(2) == "元" else int(m.group(2))
    year = ERA_OFFSET[m.group(1)] + y
    num = kanji_to_int(m.group(4))
    ref_issuer = parse_notice_number(m.group(3) + "告示", None)["issuer"]
    if num is None or ref_issuer == "unknown":
        return None
    return make_id(ref_issuer, year, num, "")


def classify_text(text: str, issuer_key: str, domain: dict,
                  known_ids: set) -> str | None:
    """1つのテキスト(本文冒頭または目次件名)に対する分類。"""
    if issuer_key == "mlit" or issuer_key.startswith("joint-"):
        if any(k in text for k in domain["law_names"]):
            return "tier1"
        if any(k in text for k in TITLE_KEYWORDS):
            return "tier1"
    if issuer_key == "mic" and "消防" in text:
        return "tier1"
    target = _amend_target_id(text)
    if target and target in known_ids:
        return "tier2"
    return None


def classify(seg: dict, domain: dict, known_ids: set,
             toc_title: str | None = None) -> tuple[str | None, str | None]:
    """ドメイン内なら (ティア, 根拠) を返す。根拠は "body" か "toc"。

    本文冒頭に法令名が出ない届出型告示があるため、目次の件名でも
    同じ物差しで分類する(公式パイプラインの目次ベース分類と一致させる)。
    """
    if seg.get("boundary_only"):
        return None, None
    tier = classify_text(seg["text"][:HEAD_CHARS], seg["issuer_key"], domain, known_ids)
    if tier:
        return tier, "body"
    if toc_title:
        tier = classify_text(toc_title, seg["issuer_key"], domain, known_ids)
        if tier:
            return tier, "toc"
    return None, None


def fetch_pdf(repo: str, path: str, retries: int = 2) -> bytes:
    """アーカイブのPDFを取得する。Git LFSポインタなら media 側を取り直す。"""
    raw = f"https://raw.githubusercontent.com/{ARCHIVE_ORG}/{repo}/main/{path}"
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(raw, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=120) as resp:
                data = resp.read()
            if data.startswith(b"version https://git-lfs.github.com"):
                media = (f"https://media.githubusercontent.com/media/"
                         f"{ARCHIVE_ORG}/{repo}/main/{path}")
                req = Request(media, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=180) as resp:
                    data = resp.read()
            if not data.startswith(b"%PDF"):
                raise ValueError(f"not a PDF ({len(data)} bytes)")
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise last  # type: ignore[misc]


def pages_of(pdf_bytes: bytes) -> list[str]:
    """PDFバイト列から空白除去済みのページ文字列の列を取り出す。"""
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return [re.sub(r"\s+", "", doc[i].get_text()) for i in range(doc.page_count)]


def scan_issue_pages(pages: list[str], domain: dict, known_ids: set,
                     toc: dict | None = None) -> tuple[list[dict], int, list[dict]]:
    """ページ文字列の列を走査し
    (ドメイン内セグメント列, 見出し総数, 発令未確認セグメント列) を返す。

    アーカイブでは大きな号が複数ファイルに分割されることがあるため、
    号単位にページを連結してからセグメント化する。
    構造的には見出しに見えても発令文言が確認できないものは
    hits には入れず、rejected として記録する(後で目視確認するため)。
    toc は parse_toc の返す {(issuer_key, number): 件名}。
    """
    segs = segment_issue(pages)
    hits, rejected = [], []
    for seg in segs:
        toc_title = (toc or {}).get((seg.get("issuer_key"), seg["number"]))
        tier, source = classify(seg, domain, known_ids, toc_title)
        if not tier:
            continue
        if promulgation_offset(seg["text"]) is None:
            rejected.append({k: seg[k] for k in
                             ("chain", "issuer_key", "number", "page")}
                            | {"reason": "no_promulgation_formula",
                               "head": seg["text"][:80]})
            continue
        hits.append({**seg, "tier": tier, "title_source": source,
                     "toc_title": toc_title})
    return hits, len(segs), rejected


def scan_issue(pdf_bytes: bytes, domain: dict,
               known_ids: set) -> tuple[list[dict], int, list[dict]]:
    """1号分を走査し (ドメイン内セグメント列, 見出し総数, 発令未確認列) を返す。"""
    return scan_issue_pages(pages_of(pdf_bytes), domain, known_ids)
