"""一次情報源5: 総務省消防庁「告示一覧」。

https://www.fdma.go.jp/laws/kokuji/ から告示リンクを抽出し、
消防法・建築防火・設備関連の告示をドメイン定義(config/domain.json)に
従って収集する。消防団・消防操法・服制などの非建築系ノイズは除外する。
"""
from __future__ import annotations

import html as html_mod
import re
from urllib.parse import urljoin

from .common import fetch, is_excluded, make_id, parse_notice_number

FDMA_KOKUJI_URL = "https://www.fdma.go.jp/laws/kokuji/"

_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TITLE_NUM_RE = re.compile(
    r'^(.+?)[（(]([^）)]*告示(?:第)?[〇一二三四五六七八九十百千万\d]+(?:号)?[^）)]*)[）)]'
)


def _matches_fdma_domain(text: str, issuer_key: str, domain: dict) -> bool:
    if is_excluded(text, domain):
        return False
    if any(k in text for k in domain.get("law_names", [])):
        return True
    if any(k in text for k in domain.get("title_keywords", [])):
        return True
    kws = domain.get("issuer_keywords", {}).get(issuer_key, [])
    if kws and any(k in text for k in kws):
        return True
    # 消防庁/自治省/総務省の防火・設備関連キーワード
    for fallback_key in ("fdma", "mha"):
        if any(k in text for k in domain.get("issuer_keywords", {}).get(fallback_key, [])):
            return True
    return False


def parse_fdma_html(html: str, domain: dict,
                    page_url: str = FDMA_KOKUJI_URL) -> list[dict]:
    """消防庁「告示一覧」HTMLからドメイン内の告示レコード列を抽出する。"""
    records: list[dict] = []
    seen: set[str] = set()
    for href, raw_text in _LINK_RE.findall(html):
        if "/laws/kokuji/" not in href and not href.startswith("assets/"):
            continue
        if href.rstrip("/") == "/laws/kokuji":
            continue
        text = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", "", raw_text))).strip()
        text = re.sub(r"\s*[（(]PDF[^）)]*[）)]\s*$", "", text, flags=re.I).strip()
        m = _TITLE_NUM_RE.match(text)
        if not m:
            continue
        title = m.group(1).strip()
        number_raw = m.group(2).strip()
        parsed = parse_notice_number(number_raw, None)
        issuer = parsed["issuer"]
        if issuer == "unknown":
            issuer = "fdma"
        if not _matches_fdma_domain(text, issuer, domain):
            continue
        rid = make_id(issuer, parsed["year"], parsed["number"],
                      fallback=f"fdma-{len(records)}")
        if rid in seen:
            continue
        seen.add(rid)
        records.append({
            "id": rid,
            "title": title,
            "number_raw": number_raw,
            "issuer": issuer,
            "year": parsed["year"],
            "number": parsed["number"],
            "date": None,
            "kind": "amendment" if "一部を改正" in title else "enactment",
            "org": "消防庁",
            "sources": [{"type": "fdma", "page_url": page_url}],
            "document_url": urljoin(page_url, href),
            "attachments": [],
        })
    return records


def load_inventory(domain: dict, page_url: str = FDMA_KOKUJI_URL) -> list[dict]:
    html = fetch(page_url).decode("utf-8", "ignore")
    return parse_fdma_html(html, domain, page_url=page_url)
