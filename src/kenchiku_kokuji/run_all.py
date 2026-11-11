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
from . import source_excel, source_kanpo, source_web

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--force", action="store_true", help="本文を再取得する")
    args = ap.parse_args()
    data_dir = Path(args.data)

    domain = json.loads((ROOT / "config" / "domain.json").read_text(encoding="utf-8"))
    print("source: excel ...", flush=True)
    inv_excel = source_excel.load_inventory(domain)
    print(f"  {len(inv_excel)} records")
    print("source: web ...", flush=True)
    inv_web = source_web.load_inventory()
    print(f"  {len(inv_web)} records")
    print("source: kanpo ...", flush=True)
    known_ids = {r["id"] for r in inv_excel} | {r["id"] for r in inv_web}
    ledger = data_dir / "notices.jsonl"
    if ledger.exists():
        known_ids |= {json.loads(l)["id"]
                      for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()}
    inv_kanpo, kanpo_state = source_kanpo.load_inventory(data_dir, domain, known_ids)
    print(f"  {len(inv_kanpo)} records")

    # 台帳も統合の種にする。官報は state に基づく増分取得のため、
    # 新規が0件の実行で merge だけに頼ると過去の官報分が台帳から
    # 消えてしまう（2026-11-11に23件が欠落した）。台帳を先頭に置くことで
    # 既存レコードは維持され、新しい情報源は出典・項目を上書き/追加する。
    ledger_recs: list[dict] = []
    ledger_path = data_dir / "notices.jsonl"
    if ledger_path.exists():
        ledger_recs = [json.loads(line) for line in
                       ledger_path.read_text(encoding="utf-8").splitlines()
                       if line.strip()]
    records = build_mod.merge(ledger_recs, inv_excel, inv_web, inv_kanpo)
    print(f"merged: {len(records)} unique notices")
    records = build_mod.build(records, data_dir, force=args.force)
    source_kanpo.save_state(data_dir, kanpo_state)

    with_text = [r for r in records if r["text_file"]]
    years = Counter((r["date"] or "????")[:4] for r in records)
    stats = [
        "# 収集統計",
        "",
        f"- 告示レコード総数: {len(records)}",
        f"- 本文テキスト取得済み: {len(with_text)} ({len(with_text) / max(len(records),1):.0%})",
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
    print("\n".join(stats[:6]))


if __name__ == "__main__":
    main()
