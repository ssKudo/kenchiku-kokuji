"""パイプライン実行エントリ。

使い方:
  python -m kenchiku_kokuji.run_all [--data data] [--force]

処理: 情報源3つを取得 → IDで統合 → 本文を取得・テキスト化 →
data/notices.jsonl(台帳) + data/texts/*.txt + data/stats.md を出力する。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from . import build as build_mod
from . import (source_excel, source_fdma, source_kanpo,
               source_kanpo_archive, source_ndl, source_web)
from .common import load_domain

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--force", action="store_true", help="本文を再取得する")
    ap.add_argument("--include-kanpo-archive", action="store_true",
                    help="kanpo-downloader アーカイブ(2025〜2026)の遡り取得を含める")
    ap.add_argument("--archive-since", default=None, help="アーカイブ取得の開始日(YYYY-MM-DD)")
    ap.add_argument("--archive-until", default=None, help="アーカイブ取得の終了日(YYYY-MM-DD)")
    ap.add_argument("--include-fdma", action="store_true", help="消防庁告示一覧を含める")
    ap.add_argument("--include-ndl", action="store_true", help="日本法令索引マスター台帳を含める")
    ap.add_argument("--ndl-details", action="store_true", help="日本法令索引の詳細ページも取得する")
    ap.add_argument("--skip-build", action="store_true",
                    help="ネットワーク取得を行わず既存台帳の整理・統計再生成のみ行う")
    args = ap.parse_args()
    data_dir = Path(args.data)

    domain = load_domain(ROOT / "config" / "domain.json")
    ledger_recs: list[dict] = []
    ledger_path = data_dir / "notices.jsonl"
    if ledger_path.exists():
        ledger_recs = [json.loads(line) for line in
                       ledger_path.read_text(encoding="utf-8").splitlines()
                       if line.strip()]

    if args.skip_build:
        records = build_mod.repair_and_prune(ledger_recs, domain)
        records = build_mod.synthesize_missing_base_stubs(records, data_dir)
        for r in records:
            r.setdefault("text_file", "")
            r.setdefault("text_chars", 0)
            r.setdefault("text_sha256", "")
            r["text_status"] = build_mod.compute_text_status(r)
        with ledger_path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        removed = build_mod.cleanup_orphan_texts(records, data_dir)
        print(f"skip-build: {len(records)} notices, removed {removed} orphan texts")
    else:
        print("source: excel ...", flush=True)
        inv_excel = source_excel.load_inventory(domain)
        print(f"  {len(inv_excel)} records")
        print("source: web ...", flush=True)
        inv_web = source_web.load_inventory()
        print(f"  {len(inv_web)} records")
        print("source: kanpo ...", flush=True)
        known_ids = {r["id"] for r in inv_excel} | {r["id"] for r in inv_web}
        known_ids |= {r["id"] for r in ledger_recs}
        inv_kanpo, kanpo_state = source_kanpo.load_inventory(data_dir, domain, known_ids)
        print(f"  {len(inv_kanpo)} records")
        inv_archive: list[dict] = []
        if args.include_kanpo_archive:
            print("source: kanpo_archive ...", flush=True)
            known_ids |= {r["id"] for r in inv_kanpo}
            inv_archive = source_kanpo_archive.load_inventory(
                domain, known_ids,
                since_date=args.archive_since, until_date=args.archive_until)
            print(f"  {len(inv_archive)} records")
        inv_fdma: list[dict] = []
        if args.include_fdma:
            print("source: fdma ...", flush=True)
            inv_fdma = source_fdma.load_inventory(domain)
            print(f"  {len(inv_fdma)} records")
        inv_ndl: list[dict] = []
        if args.include_ndl:
            print("source: ndl ...", flush=True)
            inv_ndl = source_ndl.load_inventory(domain, fetch_details=args.ndl_details)
            print(f"  {len(inv_ndl)} records")

        records = build_mod.merge(
            ledger_recs, inv_excel, inv_web, inv_kanpo, inv_archive, inv_fdma, inv_ndl)
        records = build_mod.repair_and_prune(records, domain)
        records = build_mod.synthesize_missing_base_stubs(records, data_dir)
        print(f"merged: {len(records)} unique notices")
        records = build_mod.build(records, data_dir, force=args.force)
        build_mod.cleanup_orphan_texts(records, data_dir)
        source_kanpo.save_state(data_dir, kanpo_state)

    with_text = [r for r in records if r.get("text_file")]
    statuses = Counter(r.get("text_status", "unknown") for r in records)
    years = Counter((r.get("date") or str(r.get("year") or "????"))[:4] for r in records)
    stats = [
        "# 収集統計",
        "",
        f"- 告示レコード総数: {len(records)}",
        f"- 本文テキスト取得済み: {len(with_text)} ({len(with_text) / max(len(records),1):.0%})",
        f"- 本文状態の内訳: {dict(statuses)}",
        f"- 本文の総文字数: {sum(r['text_chars'] for r in records):,}",
        f"- 情報源の内訳: {dict(Counter(s['type'] for r in records for s in r['sources']))}",
        f"- 種別: {dict(Counter(r['kind'] for r in records))}",
        "",
        "## 年別件数",
        "",
        *[f"- {y}: {c}" for y, c in sorted(years.items())],
        "",
        "## 本文が取れなかったレコード",
        "",
        *[f"- {r['id']} {r['title'][:40]} ({r['text_how']})"
          for r in records if not r["text_file"]],
    ]
    (data_dir / "stats.md").write_text("\n".join(stats) + "\n", encoding="utf-8")
    (data_dir / "missing_texts.md").write_text(
        build_mod.format_missing_texts_report(records), encoding="utf-8")
    print("\n".join(stats[:7]))


if __name__ == "__main__":
    main()
