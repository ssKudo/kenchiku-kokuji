"""一次情報源1: 国土交通省「告示・通達一覧」Excel。

https://www.mlit.go.jp/notice/ で配布される一覧を取得し、ドメイン定義
(config/domain.json)に合う告示だけを抽出する。通達・通知は対象外。
注意: この一覧の住宅局分は2019年で更新が止まっている(実測)。2020年以降分は
source_web が担う。
"""
from __future__ import annotations

import datetime
import io
import re

import openpyxl

from .common import fetch, make_id, parse_notice_number

PAGE_URL = "https://www.mlit.go.jp/notice/"


def find_xlsx_url() -> str:
    html = fetch(PAGE_URL).decode("utf-8", "ignore")
    m = re.search(r'href="([^"]+\.xlsx)"', html)
    if not m:
        raise RuntimeError("告示・通達一覧の xlsx リンクが見つからない")
    url = m.group(1)
    return url if url.startswith("http") else "https://www.mlit.go.jp" + url


def load_inventory(domain: dict) -> list[dict]:
    xlsx_url = find_xlsx_url()
    wb = openpyxl.load_workbook(io.BytesIO(fetch(xlsx_url)), read_only=True)
    ws = wb.active
    law_names = domain["law_names"]
    org_keywords = domain["org_keywords"]
    records = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        name, num, date, org, link, url = (list(row) + [None] * 6)[:6]
        if not name or not num or "告示" not in str(num):
            continue
        name, org = str(name).strip(), str(org or "").strip()
        if not (any(k in name for k in law_names) or any(k in org for k in org_keywords)):
            continue
        m = re.search(r'HYPERLINK\("([^"]+)"', str(link)) if link else None
        doc_url = m.group(1) if m else (str(url).strip() if isinstance(url, str) else "")
        if isinstance(date, (datetime.datetime, datetime.date)):
            date_iso = date.strftime("%Y-%m-%d")
        elif date:
            date_iso = str(date)[:10]
        else:
            date_iso = None
        parsed = parse_notice_number(str(num), date_iso)
        rid = make_id(parsed["issuer"], parsed["year"], parsed["number"],
                      fallback=f"excel-{len(records)}")
        records.append({
            "id": rid,
            "title": name,
            "number_raw": str(num).strip(),
            "issuer": parsed["issuer"],
            "year": parsed["year"],
            "number": parsed["number"],
            "date": date_iso,
            "kind": "amendment" if "一部を改正" in name else "enactment",
            "org": org,
            "sources": [{"type": "excel", "page_url": PAGE_URL}],
            "document_url": doc_url,
            "attachments": [],
        })
    return records
