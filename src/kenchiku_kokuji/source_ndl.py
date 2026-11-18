"""一次情報源6: 国立国会図書館「日本法令索引」(https://hourei.ndl.go.jp/simple/)。

2004年8月時点で有効であったもの＋それ以降に制定された告示のマスター台帳
（件名・告示番号・公布日・分類・現行/廃止ステータス・改正沿革・被改正法令）
を礼儀正しいレート制限(>=1.5秒間隔)で取得し、完全マップの母集団を確定する。
日本法令索引には本文テキストが無いため、本情報源由来のレコードで無料一次ソース
に本文が無いものは text_how="metadata_only" として台帳に保持する。
"""
from __future__ import annotations

import html as html_mod
import http.cookiejar
import math
import re
import time
import urllib.parse
import urllib.request

from .common import is_excluded, make_id, parse_era_date, parse_notice_number

NDL_SIMPLE_URL = "https://hourei.ndl.go.jp/simple/"
NDL_SEARCH_URL = "https://hourei.ndl.go.jp/simple?searchLaw"
NDL_RESULT_URL = "https://hourei.ndl.go.jp/simple/result"
NDL_DETAIL_URL = "https://hourei.ndl.go.jp/simple/detail?lawId="
NDL_USER_AGENT = "kenchiku-kokuji/1.0 (+https://github.com/ssKudo/kenchiku-kokuji)"

DEFAULT_CLASSIFICATIONS = [
    "建設/住宅・建築/建築",
    "建設/住宅・建築/住宅",
    "警察・消防/消防/作用/通則",
    "警察・消防/消防/作用/危険物",
    "警察・消防/消防/作用/消防用機械器具等の規格・検定",
]

_CSRF_RE = re.compile(r'name=["\']_csrf["\'][^>]*value=["\']([^"\']+)["\']', re.I)
_CSRF_REV_RE = re.compile(r'value=["\']([^"\']+)["\'][^>]*name=["\']_csrf["\']', re.I)
_TOTAL_RE = re.compile(r"<h1>\s*検索結果[\s　]*(\d+)件\s*</h1>")
_ROW_RE = re.compile(
    r'<li>\s*<h3>\s*\d+件目：\s*<a[^>]+href="/simple/detail\?lawId=(\d+)[^"]*"[^>]*>'
    r'(.*?)</a>.*?</h3>\s*<p>(.*?)</p>\s*</li>',
    re.S,
)
_DETAIL_NUM_RE = re.compile(r"（([^）]*告示[^）]*)）")


def extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html) or _CSRF_REV_RE.search(html)
    if not m:
        raise RuntimeError("日本法令索引の _csrf トークンが見つからない")
    return m.group(1)


def build_search_params(csrf: str, classification: str) -> list[tuple[str, str]]:
    """Spring MVC チェックボックス仕様に合わせて「告示・訓令(lfd14_02)」のみを選択する。"""
    return [
        ("_csrf", csrf),
        ("_lfd_all_02", "on"),
        ("_lfd0103_02", "on"),
        ("_lfd04_02", "on"),
        ("_lfd05_02", "on"),
        ("_lfd06_02", "on"),
        ("_lfd07_02", "on"),
        ("_lfd08_02", "on"),
        ("_lfd09_02", "on"),
        ("_lfd10_02", "on"),
        ("_lfd11_02", "on"),
        ("lfd14_02", "1"),
        ("_lfd14_02", "on"),
        ("_lfd1213_02", "on"),
        ("ed1_02", "1"),
        ("ed2_02", "1"),
        ("ed3_02", "1"),
        ("cc_02", classification),
    ]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _split_date_and_number(meta_str: str) -> tuple[str | None, str]:
    """「昭和40年12月18日建設省告示第3411号」→ ("1965-12-18", "建設省告示第3411号")。"""
    date_iso = parse_era_date(meta_str)
    num_part = re.sub(
        r"^(?:明治|大正|昭和|平成|令和)(?:元|\d+)年\d+月\d+日\s*",
        "",
        meta_str.strip(),
    )
    return date_iso, num_part or meta_str.strip()


def parse_result_page(html: str, classification: str = "") -> tuple[int, list[dict]]:
    """/simple/result HTMLから (総件数, レコード列) を抽出する。"""
    tm = _TOTAL_RE.search(html)
    total = int(tm.group(1)) if tm else 0
    records: list[dict] = []
    for law_id, raw_title, raw_meta in _ROW_RE.findall(html):
        title = _clean(raw_title)
        meta = _clean(raw_meta)
        if "告示" not in meta:
            continue
        nm = _DETAIL_NUM_RE.search(meta)
        num_full = nm.group(1).strip() if nm else meta
        date_iso, number_raw = _split_date_and_number(num_full)
        parsed = parse_notice_number(num_full, date_iso)
        rid = make_id(parsed["issuer"], parsed["year"], parsed["number"],
                      fallback=f"ndl-{law_id}")
        status = "現行"
        if "廃止法令" in meta:
            status = "廃止"
        elif "改正法令" in meta:
            status = "改正"
        records.append({
            "id": rid,
            "title": title,
            "number_raw": number_raw,
            "issuer": parsed["issuer"],
            "year": parsed["year"],
            "number": parsed["number"],
            "date": date_iso,
            "kind": "amendment" if "一部を改正" in title else "enactment",
            "org": "",
            "ndl_url": f"{NDL_DETAIL_URL}{law_id}",
            "ndl_law_id": law_id,
            "ndl_status": status,
            "ndl_classification": classification,
            "sources": [{"type": "ndl", "page_url": f"{NDL_DETAIL_URL}{law_id}"}],
            "document_url": "",
            "attachments": [],
            "text_how": "metadata_only",
        })
    return total, records


def _extract_section_refs(html: str, anchor_name: str) -> list[str]:
    m = re.search(rf'name=["\']{re.escape(anchor_name)}["\']', html)
    if not m:
        return []
    tail = html[m.end():]
    end_m = re.search(r"<h2\b", tail, re.I)
    sec = tail[: end_m.start()] if end_m else tail
    refs: list[str] = []
    for h3 in re.findall(r"<h3[^>]*>(.*?)</h3>", sec, re.S | re.I):
        txt = _clean(h3)
        p = parse_notice_number(txt, None)
        if p["issuer"] != "unknown" and p["year"] and p["number"]:
            rid = make_id(p["issuer"], p["year"], p["number"], "")
            if rid and rid not in refs:
                refs.append(rid)
    return refs


def parse_detail_page(html: str, law_id: str) -> dict:
    """/simple/detail?lawId=... からメタデータ・改正履歴・被改正法令を抽出する。"""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    title = _clean(h1.group(1)) if h1 else ""
    header = re.search(r"<header[^>]*>(.*?)</header>", html, re.S | re.I)
    header_text = _clean(header.group(1)) if header else ""
    nm = _DETAIL_NUM_RE.search(header_text)
    num_full = nm.group(1).strip() if nm else header_text
    date_iso, number_raw = _split_date_and_number(num_full)

    basis_laws: list[str] = []
    bm = re.search(r"根拠法令[：:]\s*(.*?)</li>", html, re.S)
    if bm:
        for law in re.split(r"[、,]", _clean(bm.group(1))):
            law = law.strip()
            if law and law not in basis_laws:
                basis_laws.append(law)

    return {
        "ndl_url": f"{NDL_DETAIL_URL}{law_id}",
        "title": title,
        "number_raw": number_raw,
        "date": date_iso,
        "basis_laws": basis_laws,
        "amended_by": _extract_section_refs(html, "history"),
        "amends": _extract_section_refs(html, "amended"),
    }


def _make_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", NDL_USER_AGENT)]
    return opener


def load_inventory(domain: dict,
                   classifications: list[str] | None = None,
                   fetch_details: bool = False,
                   delay: float = 1.5,
                   max_pages: int | None = None) -> list[dict]:
    """日本法令索引から建築・消防系告示のマスター台帳を取得する。"""
    delay = max(delay, 1.5)
    targets = classifications or DEFAULT_CLASSIFICATIONS
    opener = _make_opener()
    by_id: dict[str, dict] = {}

    for cc in targets:
        with opener.open(NDL_SIMPLE_URL, timeout=30) as resp:
            form_html = resp.read().decode("utf-8", "ignore")
        csrf = extract_csrf(form_html)
        time.sleep(delay)

        body = urllib.parse.urlencode(build_search_params(csrf, cc)).encode("utf-8")
        req = urllib.request.Request(NDL_SEARCH_URL, data=body, method="POST")
        with opener.open(req, timeout=30) as resp:
            _ = resp.read()
        time.sleep(delay)

        with opener.open(f"{NDL_RESULT_URL}?perPageOrder&perPage=100&order=1", timeout=30) as resp:
            page1_html = resp.read().decode("utf-8", "ignore")
        total, recs = parse_result_page(page1_html, classification=cc)
        pages = max(1, math.ceil(total / 100))
        if max_pages is not None:
            pages = min(pages, max_pages)

        all_recs = list(recs)
        for p in range(2, pages + 1):
            time.sleep(delay)
            with opener.open(f"{NDL_RESULT_URL}?pager&page={p}", timeout=30) as resp:
                p_html = resp.read().decode("utf-8", "ignore")
            _, p_recs = parse_result_page(p_html, classification=cc)
            all_recs.extend(p_recs)

        for rec in all_recs:
            if is_excluded(rec["title"], domain):
                continue
            if fetch_details and rec.get("ndl_law_id"):
                time.sleep(delay)
                with opener.open(rec["ndl_url"], timeout=30) as resp:
                    d_html = resp.read().decode("utf-8", "ignore")
                detail = parse_detail_page(d_html, rec["ndl_law_id"])
                for k in ("basis_laws", "amended_by", "amends"):
                    if detail.get(k):
                        rec[k] = detail[k]
            by_id.setdefault(rec["id"], rec)
        time.sleep(delay)

    return list(by_id.values())
