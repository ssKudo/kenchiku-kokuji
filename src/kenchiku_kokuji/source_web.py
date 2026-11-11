"""一次情報源2: 住宅局「建築基準法等に基づく告示の制定・改正について」。

制定・改正の告知ページ。Excel一覧が止まる2020年以降分はここにしか出ない。
ページは公布日の見出しの下に「○告示名(令和X年○○省告示第N号)」と
資料PDFへのリンクが並ぶ構造。これをエントリ単位に分解する。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from .common import fetch, make_id, parse_era_date, parse_notice_number

PAGE_URL = "https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000096.html"

HEADING = re.compile("【([^】]*公布[^】]*)】")
ENTRY = re.compile("○\\s*([^（\n]+?)\\s*[（(]([^）)]*告示第[^）)]*号)[）)]")
LINK = re.compile('<a[^>]+href="([^"]+\\.pdf)"[^>]*>([^<]*)</a>', re.I)


def load_inventory() -> list[dict]:
    html = fetch(PAGE_URL).decode("utf-8", "ignore")
    heads = [(m.start(), m.group(1)) for m in HEADING.finditer(html)]
    records: list[dict] = []
    for i, (pos, heading) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(html)
        seg = html[pos:end]
        date_iso = parse_era_date(heading)
        entries = list(ENTRY.finditer(seg))
        links = [(m.start(), urljoin(PAGE_URL, m.group(1)), m.group(2).strip())
                 for m in LINK.finditer(seg)]
        for j, e in enumerate(entries):
            title = re.sub("<[^>]+>", "", e.group(1)).strip()
            number_raw = re.sub("<[^>]+>", "", e.group(2)).strip()
            span_end = entries[j + 1].start() if j + 1 < len(entries) else len(seg)
            mine = [(u, a) for (p, u, a) in links if e.start() <= p < span_end]
            doc_url, attachments = "", []
            for u, a in mine:
                if not doc_url and ("本文" in a or "告示" in a):
                    doc_url = u
                else:
                    attachments.append({"label": a or "資料", "url": u})
            if not doc_url and mine:
                doc_url = mine[0][0]
                attachments = [a for a in attachments if a["url"] != doc_url]
            parsed = parse_notice_number(number_raw, date_iso)
            date_note = ""
            if (parsed["year"] and date_iso
                    and int(date_iso[:4]) != parsed["year"]):
                # 見出しの公布年と告示番号の年が食い違う場合は、番号の年を
                # 告示の同一性として優先し、日付は未確定として記録する
                date_note = "heading year != notice year (re-listed?)"
                date_iso = None
            rid = make_id(parsed["issuer"], parsed["year"], parsed["number"],
                          fallback=f"web-{len(records)}")
            if any(r["id"] == rid for r in records):
                continue
            records.append({
                "id": rid,
                "title": title,
                "number_raw": number_raw,
                "issuer": parsed["issuer"],
                "year": parsed["year"],
                "number": parsed["number"],
                "date": date_iso,
                "date_note": date_note,
                "kind": "amendment" if "一部を改正" in title else "enactment",
                "org": "",
                "sources": [{"type": "web", "page_url": PAGE_URL}],
                "document_url": doc_url,
                "attachments": attachments,
            })
    return records
