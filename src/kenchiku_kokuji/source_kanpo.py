"""一次情報源3: 官報(インターネット版・無料公開分)。

直近90日分の官報は国立印刷局のサイトで無料公開されている。号ごとの
「目次」ページ /{YYYYMMDD}/{YYYYMMDD}.fullcontents.html から告示の
見出し(件名・発出者・番号・掲載ページ)を拾い、ドメインに合うものを
台帳に登録する。本文は号の全ページテキスト(PDFのOCR層)を
空白除去して正規化し、告示の見出し位置で切り出して取得する。

チェック済みの日付は data/kanpo_state.json に持ち、未チェック分だけを
処理する。無料窓(90日)より前には遡らない(有料検索サービスの領域)。
窓より短い間隔で回し続ける限り「今日から未来」の分は無料で蓄積できる。

目次での番号表記は「(国土交通一三一六)」のような略式が基本で、
「(同…)」は直前の発出者の略。改正告示は件名が「平成○年○○省告示
第○号の一部を改正する件」となり根拠法令名が出ないため、被改正告示が
台帳に既にある場合もドメイン内とみなす(第2ティア)。
"""
from __future__ import annotations

import datetime
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from .common import fetch, kanji_to_int, make_id, parse_notice_number

BASE = "https://www.kanpo.go.jp"
FREE_WINDOW_DAYS = 89
STATE_FILE = "kanpo_state.json"

ENTRY = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>\s*<span class="text">(.*?)</span>\s*'
    r'<span class="date">(\d+)</span>', re.S)
FULL_LINK = re.compile(r'href="([^"]*full\d+f\.html)"')
NUMBER_PART = re.compile(r"[（(]([^）)]+)[）)]\s*$")
ABBREV = {
    "国土交通": ("mlit", "国土交通省"), "厚生労働": ("mhlw", "厚生労働省"),
    "農林水産": ("maff", "農林水産省"), "法務": ("moj", "法務省"),
    "総務": ("mic", "総務省"), "財務": ("mof", "財務省"),
    "経済産業": ("meti", "経済産業省"), "環境": ("env", "環境省"),
    "文部科学": ("mext", "文部科学省"), "外務": ("mofa", "外務省"),
    "防衛": ("mod", "防衛省"), "内閣府": ("cao", "内閣府"),
    "金融": ("fsa", "金融庁"), "消費者": ("caa", "消費者庁"),
    "こども家庭": ("cfa", "こども家庭庁"), "デジタル": ("digital", "デジタル庁"),
    "復興": ("reconstruction", "復興庁"), "公安": ("npa", "国家公安委員会"),
}
TITLE_KEYWORDS = ["建築", "住宅", "宅地造成", "宅地開発", "都市計画", "市街地",
                  "区画整理", "耐震", "防火", "耐火", "避難", "昇降機", "浄化槽",
                  "構造", "地区計画", "再開発", "マンション"]
HEADING = re.compile(r"[一-龥]{2,8}(?:省|庁|委員会)告示第[〇一二三四五六七八九十百千]+号")
AMEND_REF = re.compile(
    r"(明治|大正|昭和|平成|令和)(元|\d+)年(.+?)告示第([〇一二三四五六七八九十百千万\d]+)号の一部を改正")
ERA_OFFSET = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
KAN = "〇一二三四五六七八九"


def int_to_kanji(n: int) -> str:
    out = ""
    for dig, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        d, n = divmod(n, dig)
        if d:
            out += (KAN[d] if d > 1 or not unit else "") + unit
    return out or "〇"


def _contents_url(d: datetime.date) -> str:
    return f"{BASE}/{d:%Y%m%d}/{d:%Y%m%d}.fullcontents.html"


def _parse_entry_number(inner: str, last_issuer: tuple[str, str] | None):
    """目次の (国土交通一三一六) / (同一三一七) / (法務省告示第八百四十一号)
    から (issuer_key, issuer_full, number) を返す。"""
    inner = inner.strip()
    if "告示第" in inner:
        p = parse_notice_number(inner, None)
        full = next((f for k, f in ABBREV.values() if k == p["issuer"]), inner)
        return p["issuer"], full, p["number"], last_issuer
    m = re.match(r"(.+?)([〇一二三四五六七八九]+)$", inner)
    if not m:
        return None, None, None, last_issuer
    head, digits = m.group(1), m.group(2)
    number = kanji_to_int(digits)
    if head == "同":
        if last_issuer is None:
            return None, None, None, last_issuer
        return last_issuer[0], last_issuer[1], number, last_issuer
    for ab, (key, full) in ABBREV.items():
        if head == ab or head.startswith(ab):
            return key, full, number, (key, full)
    return None, None, number, last_issuer


def _domain_match(title: str, issuer_key: str, domain: dict, known_ids: set) -> bool:
    if re.search(r"(省令|政令|府令|勅令)$", title):
        return False  # 目次には省令・政令も並ぶ。告示だけを対象とする
    if issuer_key == "mlit" or issuer_key.startswith("joint-"):
        if any(k in title for k in domain["law_names"]):
            return True
        if any(k in title for k in TITLE_KEYWORDS):
            return True
        m = AMEND_REF.search(title)
        if m:
            y = 1 if m.group(2) == "元" else int(m.group(2))
            year = ERA_OFFSET[m.group(1)] + y
            num = kanji_to_int(m.group(4))
            ref_issuer = parse_notice_number(m.group(3) + "告示", None)["issuer"]
            if make_id(ref_issuer, year, num, "") in known_ids:
                return True
        return False
    if issuer_key == "mic":
        return "消防" in title
    return False


def _issue_pdf_url(sample_page_url: str, issue_dir: str) -> str:
    """号のページViewerから総ページ数を読み、全ページPDFのURLを組み立てる。
    例: .../20261030h01819/pdf/20261030h01819full00010032.pdf"""
    try:
        html = fetch(sample_page_url, retries=1).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    m = re.search(r"/\s*(\d{1,4})\s*ページ", text) or re.search(r"/\s*(\d{1,4})", text)
    if not m:
        return ""
    total = int(m.group(1))
    day = issue_dir[:8]
    return f"{BASE}/{day}/{issue_dir}/pdf/{issue_dir}full0001{total:04d}.pdf"


def _slice_notice(pdf_bytes: bytes, issuer_full: str, number: int) -> str:
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pages = [re.sub(r"\s+", "", doc[i].get_text()) for i in range(doc.page_count)]
    target = f"{issuer_full}告示第{int_to_kanji(number)}号"
    for pi, text in enumerate(pages):
        idx = text.find(target)
        if idx < 0:
            continue
        buf = text[idx:]
        for pj in range(pi + 1, min(pi + 3, doc.page_count)):
            buf += pages[pj]
        m = HEADING.search(buf, len(target))
        return buf[: m.start()] if m else buf[:12000]
    return ""


def load_inventory(data_dir: Path, domain: dict, known_ids: set,
                   today: datetime.date | None = None) -> tuple[list[dict], dict]:
    """(レコード列, 更新後の状態) を返す。状態の保存は呼び出し側が行う。"""
    today = today or datetime.date.today()
    state_path = data_dir / STATE_FILE
    state = {"last_checked": None}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    earliest = today - datetime.timedelta(days=FREE_WINDOW_DAYS)
    if state["last_checked"]:
        start = datetime.date.fromisoformat(state["last_checked"]) + datetime.timedelta(days=1)
        start = max(start, earliest)
    else:
        start = earliest

    records: list[dict] = []
    seen: set[str] = set()
    d = start
    while d <= today:
        url = _contents_url(d)
        try:
            html = fetch(url, retries=0).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - 休刊日などは404になる
            d += datetime.timedelta(days=1)
            continue
        last_issuer: tuple[str, str] | None = None
        matched: list[dict] = []
        for href, raw_title, page in ENTRY.findall(html):
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw_title)).strip()
            nm = NUMBER_PART.search(title)
            if not nm:
                continue
            issuer_key, issuer_full, number, last_issuer = _parse_entry_number(
                nm.group(1), last_issuer)
            if issuer_key is None or number is None:
                continue
            clean_title = title[: nm.start()].strip()
            if not _domain_match(clean_title, issuer_key, domain, known_ids):
                continue
            rid = make_id(issuer_key, d.year, number, fallback=f"kanpo-{d:%Y%m%d}-{len(records)}")
            if rid in seen:
                continue
            seen.add(rid)
            issue_dir = href.split("/")[0]
            matched.append({
                "id": rid, "title": clean_title,
                "number_raw": f"{issuer_full}告示第{number}号",
                "issuer": issuer_key, "year": d.year, "number": number,
                "date": d.isoformat(),
                "kind": "amendment" if "一部を改正" in clean_title else "enactment",
                "org": "",
                "sources": [{"type": "kanpo", "page_url": url}],
                "document_url": urljoin(url, href),
                "attachments": [],
                "kanpo_issue": issue_dir, "kanpo_page": int(page),
                "issuer_full": issuer_full,
                "text_inline": "",
            })
        # 号ごとにPDFを1回だけ取得し、一致した告示の本文を切り出す
        by_issue: dict[str, list[dict]] = {}
        for rec in matched:
            by_issue.setdefault(rec["kanpo_issue"], []).append(rec)
        for issue_dir, recs in by_issue.items():
            pdf_url = _issue_pdf_url(recs[0]["document_url"], issue_dir)
            if not pdf_url:
                continue
            try:
                pdf_bytes = fetch(pdf_url, retries=1)
            except Exception:  # noqa: BLE001
                continue
            for rec in recs:
                rec["kanpo_pdf"] = pdf_url
                rec["text_inline"] = _slice_notice(pdf_bytes, rec["issuer_full"], rec["number"])
                time.sleep(0.1)
        for rec in matched:
            rec.pop("issuer_full", None)
            records.append(rec)
        d += datetime.timedelta(days=1)
        time.sleep(0.2)
    state["last_checked"] = today.isoformat()
    return records, state


def save_state(data_dir: Path, state: dict) -> None:
    (data_dir / STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
