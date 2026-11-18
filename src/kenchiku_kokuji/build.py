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

from .common import fetch, is_excluded, make_id, parse_notice_number
from .source_kanpo import extract_amend_targets

DELAY_SEC = 0.3


def _merge_list_field(cur: dict, rec: dict, key: str) -> None:
    vals = rec.get(key)
    if not vals:
        return
    existing = cur.setdefault(key, [])
    for v in vals:
        if v not in existing:
            existing.append(v)


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
            if not cur.get("document_url") and rec.get("document_url"):
                cur["document_url"] = rec["document_url"]
            if not cur.get("org") and rec.get("org"):
                cur["org"] = rec["org"]
            if not cur.get("date") and rec.get("date"):
                cur["date"] = rec["date"]
            if not cur.get("number_raw") and rec.get("number_raw"):
                cur["number_raw"] = rec["number_raw"]
            if not cur.get("ndl_url") and rec.get("ndl_url"):
                cur["ndl_url"] = rec["ndl_url"]
            if len(rec.get("title", "")) > len(cur.get("title", "")) and not rec.get("title", "").startswith("（未取得"):
                cur["title"] = rec["title"]
            elif cur.get("title", "").startswith("（未取得") and rec.get("title"):
                cur["title"] = rec["title"]
            if not cur.get("text_file") and rec.get("text_file"):
                for tk in ("text_file", "text_chars", "text_sha256", "text_how", "text_status"):
                    if tk in rec:
                        cur[tk] = rec[tk]
            cur["attachments"].extend(
                a for a in rec.get("attachments", []) if a not in cur["attachments"])
            for list_key in ("basis_laws", "amends", "amended_by"):
                _merge_list_field(cur, rec, list_key)
    return sorted(by_id.values(), key=lambda r: (r["date"] or "", r["id"]))


def repair_and_prune(records: list[dict], domain: dict) -> list[dict]:
    """欠損year/IDを修復し、非建築ノイズを除外して重複IDを統合する。"""
    repaired: list[dict] = []
    for rec in records:
        if is_excluded(rec.get("title", ""), domain):
            continue
        r = dict(rec)
        if (r.get("year") is None or r.get("issuer") == "unknown") and r.get("number_raw"):
            p = parse_notice_number(r["number_raw"], r.get("date"))
            if p["year"] is not None and p["number"] is not None and p["issuer"] != "unknown":
                r["issuer"] = p["issuer"]
                r["year"] = p["year"]
                r["number"] = p["number"]
                r["id"] = make_id(p["issuer"], p["year"], p["number"], r["id"])
        repaired.append(r)
    merged = merge(repaired)
    valid_ids = {r["id"] for r in merged}
    for r in merged:
        for key in ("amends", "amended_by"):
            if key in r:
                r[key] = [tid for tid in r[key] if tid in valid_ids]
    return merged


def cleanup_orphan_texts(records: list[dict], data_dir: Path) -> int:
    """台帳に存在しない孤立テキストファイル(除外・統合済み)を削除する。"""
    texts_dir = data_dir / "texts"
    if not texts_dir.exists():
        return 0
    keep = {Path(r["text_file"]).name for r in records if r.get("text_file")}
    removed = 0
    for p in texts_dir.glob("*.txt"):
        if p.name not in keep:
            p.unlink()
            removed += 1
    return removed


def compute_text_status(rec: dict) -> str:
    """レコードの本文取得状態を分類する。"""
    if rec.get("text_file"):
        how = rec.get("text_how", "")
        chars = rec.get("text_chars", 0)
        if chars == 12000 and how in ("kanpo_archive", "kanpo_article"):
            return "truncated"
        if how == "page_text_short" or chars < 800:
            return "short"
        return "ok"
    how = rec.get("text_how", "")
    if how in ("pdf", "page_pdf"):
        return "image_pdf"
    if how == "metadata_only":
        return "metadata_only"
    if not rec.get("document_url") and any(
        s.get("type") in ("ndl", "amend_ref") for s in rec.get("sources", [])
    ):
        return "metadata_only"
    return "no_text_found"


def _parse_stub_id(rid: str) -> tuple[str, int | None, int | None]:
    parts = rid.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return parts[0], int(parts[1]), int(parts[2])
    return "unknown", None, None


def synthesize_missing_base_stubs(records: list[dict], data_dir: Path | None = None) -> list[dict]:
    """改正告示のタイトル・本文冒頭から被改正告示IDを抽出し、
    amends / amended_by を結線するとともに、台帳に無い元告示を metadata_only スタブとして補う。"""
    by_id = {r["id"]: r for r in records}
    new_stubs: dict[str, dict] = {}
    for rec in records:
        texts = [rec.get("title", "")]
        if data_dir and rec.get("text_file"):
            tf = data_dir / rec["text_file"]
            if tf.exists():
                texts.append(tf.read_text(encoding="utf-8", errors="ignore")[:600])
        elif rec.get("text_inline"):
            texts.append(rec["text_inline"][:600])
        targets: list[str] = []
        for t in texts:
            for tid in extract_amend_targets(t):
                if tid != rec["id"] and tid not in targets:
                    targets.append(tid)
        if not targets:
            continue
        amends = rec.setdefault("amends", [])
        for tid in targets:
            if tid not in amends:
                amends.append(tid)
            target_rec = by_id.get(tid) or new_stubs.get(tid)
            if target_rec is None:
                issuer, year, number = _parse_stub_id(tid)
                target_rec = {
                    "id": tid,
                    "title": f"（未取得・被改正告示）{tid}",
                    "number_raw": "",
                    "issuer": issuer,
                    "year": year,
                    "number": number,
                    "date": None,
                    "kind": "enactment",
                    "org": "",
                    "sources": [{"type": "amend_ref", "referenced_by": rec["id"]}],
                    "document_url": "",
                    "attachments": [],
                    "text_how": "metadata_only",
                    "text_status": "metadata_only",
                    "amended_by": [],
                }
                new_stubs[tid] = target_rec
            ab = target_rec.setdefault("amended_by", [])
            if rec["id"] not in ab:
                ab.append(rec["id"])
    if not new_stubs:
        return records
    return sorted(
        list(records) + list(new_stubs.values()),
        key=lambda r: (r["date"] or "", r["id"]),
    )


def format_missing_texts_report(records: list[dict]) -> str:
    """本文未取得・不完全レコード（オフライン補完対象）の一覧Markdownを生成する。"""
    groups: dict[str, list[dict]] = {
        "metadata_only": [],
        "image_pdf": [],
        "no_text_found": [],
        "truncated": [],
    }
    for r in records:
        st = r.get("text_status") or compute_text_status(r)
        if st in groups:
            groups[st].append(r)

    labels = {
        "metadata_only": "メタデータのみ（無料一次ソースに本文なし・オフライン/有料補完対象）",
        "image_pdf": "画像PDF（OCR未実施のため0文字）",
        "no_text_found": "取得失敗・本文未検出",
        "truncated": "12,000字で打ち切られたレコード（再取得推奨）",
    }
    lines = ["# 本文未取得・補完対象一覧", ""]
    for key, heading in labels.items():
        items = groups[key]
        lines.append(f"## {heading} ({len(items)}件)")
        lines.append("")
        if not items:
            lines.append("- なし")
            lines.append("")
            continue
        for r in items:
            extra = []
            if r.get("ndl_url"):
                extra.append(f"NDL: {r['ndl_url']}")
            if r.get("amended_by"):
                extra.append(f"被改正参照: {', '.join(r['amended_by'][:5])}")
            suffix = f" [{' / '.join(extra)}]" if extra else ""
            lines.append(f"- `{r['id']}` {r.get('title', '')[:60]}{suffix}")
        lines.append("")
    return "\n".join(lines)


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text() for page in doc), doc.page_count


def _strip_html(html: str) -> str:
    """タグを除去し、段落等の改行を保持したページ本文を返す。"""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(
        r"<(?:br\s*/?|/(?:p|div|li|h[1-6]|tr|dt|dd|blockquote|pre)|hr\s*/?)>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
            merged_rec = {
                **rec,
                "text_file": old["text_file"],
                "text_chars": old["text_chars"],
                "text_sha256": old["text_sha256"],
                "text_how": old["text_how"],
            }
            merged_rec["text_status"] = compute_text_status(merged_rec)
            out.append(merged_rec)
            continue
        inline = rec.pop("text_inline", "")
        if inline and inline.strip():
            text, how = inline, "kanpo_article"
        elif not rec.get("document_url") and (
            rec.get("text_how") == "metadata_only"
            or any(s.get("type") in ("ndl", "amend_ref") for s in rec.get("sources", []))
        ):
            text, how = "", "metadata_only"
        else:
            text, how = obtain_text(rec)
        rec = {**rec, "text_file": "", "text_chars": 0, "text_sha256": "", "text_how": how}
        if text.strip():
            rel = f"texts/{rec['id']}.txt"
            (data_dir / rel).write_text(text, encoding="utf-8")
            rec["text_file"] = rel
            rec["text_chars"] = len(text)
            rec["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec["text_status"] = compute_text_status(rec)
        out.append(rec)
        if how != "metadata_only":
            time.sleep(DELAY_SEC)
        if i % 25 == 0 or i == len(records):
            ok = sum(1 for r in out if r["text_file"])
            print(f"[{i}/{len(records)}] text-ok: {ok}", flush=True)
    with ledger_path.open("w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out
