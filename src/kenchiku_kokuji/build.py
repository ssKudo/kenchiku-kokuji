"""統合と本文取得。

2つの情報源のレコードを (発出者, 年, 番号) のIDで統合し、本文PDFを取得して
テキスト化する。既に取得済みの本文は再取得しない(再開可能)。
取得に失敗したレコードも台帳に残し、失敗理由を記録する(欠落を隠さない)。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from .common import fetch

DELAY_SEC = 0.3


def merge(*inventories: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for inv in inventories:
        for rec in inv:
            cur = by_id.get(rec["id"])
            if cur is None:
                by_id[rec["id"]] = rec
                continue
            # 既存レコードに情報源を足し、欠けている項目を補う
            types = {s["type"] for s in cur["sources"]}
            for s in rec["sources"]:
                if s["type"] not in types:
                    cur["sources"].append(s)
            if not cur["document_url"] and rec["document_url"]:
                cur["document_url"] = rec["document_url"]
            if not cur["org"] and rec["org"]:
                cur["org"] = rec["org"]
            if len(rec["title"]) > len(cur["title"]):
                cur["title"] = rec["title"]
            cur["attachments"].extend(
                a for a in rec["attachments"] if a not in cur["attachments"])
    return sorted(by_id.values(), key=lambda r: (r["date"] or "", r["id"]))


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text() for page in doc), doc.page_count


def _strip_html(html: str) -> str:
    """タグを除去して空白を畳んだページ本文。"""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def accept_page_text(html: str, title: str) -> tuple[str, str]:
    """ランディングページのHTMLから本文を受け入れるか判定する。

    800字超は従来通り page_text。短い告示（SGML原文ページに全文が
    載っているが400〜700字しかないもの）は、タイトルがページ内に
    含まれていることを条件に page_text_short として受け入れる。
    タイトルが無ければ一覧・エラー画面の類とみなして捨てる。
    """
    plain = _strip_html(html)
    if len(plain) > 800:
        return plain, "page_text"
    if len(plain) > 200:
        key = re.sub(r"\s+", "", title)[:20]
        if key and key in re.sub(r"\s+", "", plain):
            return plain, "page_text_short"
    return "", "no_text_found"


def decode_html(data: bytes, title: str = "") -> str:
    """HTMLバイト列を文字列に直す。

    国交省のSGML原文ページは Shift_JIS 宣言のものが多く、UTF-8決め打ちで
    読むと文字化けしてタイトル照合も効かなくなる（2026-11-11に34件の
    no_text_foundの原因と判明）。metaタグのcharsetを優先し、無ければ
    utf-8 → shift_jis → euc-jp の順に試し、タイトルが見つかるデコードを
    採用する。どれにもタイトルが無ければ文字化け(U+FFFD)の無いものを返す。
    """
    m = re.search(rb'charset=["\']?\s*([A-Za-z0-9_\-]+)', data[:4096], re.I)
    candidates: list[str] = []
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    for enc in ("utf-8", "shift_jis", "euc-jp"):
        if enc not in candidates:
            candidates.append(enc)
    key = re.sub(r"\s+", "", title)
    best: str | None = None
    for enc in candidates:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if best is None:
            best = text
        if key and key[:20] in re.sub(r"\s+", "", text):
            return text
        if "\ufffd" not in text:
            best = text
    return best if best is not None else data.decode("utf-8", "ignore")


def obtain_text(rec: dict) -> tuple[str, str]:
    """本文テキストと取得方法を返す。失敗時は ("", 理由)。"""
    url = rec["document_url"]
    if not url:
        return "", "no_url"
    try:
        data = fetch(url)
    except Exception as e:  # noqa: BLE001
        return "", f"fetch_error:{type(e).__name__}"
    if data[:4] == b"%PDF":
        try:
            text, _pages = extract_pdf_text(data)
            return text, "pdf"
        except Exception as e:  # noqa: BLE001
            return "", f"pdf_error:{type(e).__name__}"
    # ランディングページ: PDFリンクを探し、無ければページ自体をテキスト化
    html = decode_html(data, rec.get("title", ""))
    for href in re.findall(r'href="([^"]+\.pdf)"', html, re.I)[:4]:
        try:
            d2 = fetch(urljoin(url, href))
            if d2[:4] == b"%PDF":
                text, _pages = extract_pdf_text(d2)
                if text.strip():
                    return text, "page_pdf"
        except Exception:  # noqa: BLE001
            continue
    return accept_page_text(html, rec.get("title", ""))


def build(records: list[dict], data_dir: Path, force: bool = False) -> list[dict]:
    texts_dir = data_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = data_dir / "notices.jsonl"
    done: dict[str, dict] = {}
    if ledger_path.exists() and not force:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                old = json.loads(line)
                if old.get("text_file") and (data_dir / old["text_file"]).exists():
                    done[old["id"]] = old
    out = []
    for i, rec in enumerate(records, 1):
        if rec["id"] in done:
            # メタデータは最新の情報源で更新し、本文だけをキャッシュから再利用する
            old = done[rec["id"]]
            out.append({**rec,
                        "text_file": old["text_file"],
                        "text_chars": old["text_chars"],
                        "text_sha256": old["text_sha256"],
                        "text_how": old["text_how"]})
            continue
        inline = rec.pop("text_inline", "")
        if inline and inline.strip():
            text, how = inline, "kanpo_article"
        else:
            text, how = obtain_text(rec)
        rec = {**rec, "text_file": "", "text_chars": 0, "text_sha256": "", "text_how": how}
        if text.strip():
            rel = f"texts/{rec['id']}.txt"
            (data_dir / rel).write_text(text, encoding="utf-8")
            rec["text_file"] = rel
            rec["text_chars"] = len(text)
            rec["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        out.append(rec)
        if i % 25 == 0 or i == len(records):
            ok = sum(1 for r in out if r["text_file"])
            print(f"[{i}/{len(records)}] text-ok: {ok}", flush=True)
        time.sleep(DELAY_SEC)
    with ledger_path.open("w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out
