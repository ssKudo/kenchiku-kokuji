# AGENTS.md — kenchiku-kokuji

建築・防火関係告示コーパスをAIエージェントが参照・検索する際の公式ガイドです。

## 推奨3ステップ検索（コンテキスト節約のため必ずこの順で行うこと）

1. **`data/index.tsv` を `rg` / `grep` で検索して対象 `id` を絞る**
   - 1告示＝1行の軽量タブ区切りファイルです（`data/notices.jsonl` を全件読み込まないでください）。
   - 列: `id`, `date`, `year`, `issuer`, `kind`, `text_status`, `text_chars`, `number_raw`, `title`, `text_file`
   - 例: `rg "耐火構造" data/index.tsv` / `rg "mlit-2023-970" data/index.tsv`
2. **必要な場合のみ `data/notices.jsonl` から該当 `id` の1行を取得する**
   - 出典URL（`sources`, `document_url`, `ndl_url`）、改正関係（`amends`, `amended_by`）、添付資料（`attachments`）を確認できます。
   - 例: `rg '"id": "kensetsu-1999-1287"' data/notices.jsonl`
3. **本文が必要な場合は `data/texts/<id>.txt` を読む**
   - `text_status` が `ok` または `short` のレコードは `text_file`（例: `data/texts/kensetsu-1964-91.txt`）を参照できます。
   - `text_status` が `truncated` のレコードは12,000字で切れているため、必要に応じて出典URL（官報PDF等）で確認してください。

## 本文が無いレコード（`metadata_only` / `image_pdf` / `no_text_found`）の扱い

- `text_status` が `metadata_only`（160件）、`image_pdf`（2件）、`no_text_found`（3件）のレコードには無料一次ソース由来の本文テキストが存在しません。
- **存在しない本文を推測・補完（ハルシネーション）しないでください。**
- 該当する場合は「台帳上は `metadata_only`（本文未収録）である」旨と、参照可能なメタデータ（`id`, `number_raw`, `title`, `ndl_url`, `amended_by`）をそのまま回答し、詳細は `data/missing_texts.md` を案内してください。

## 注意事項

- 本リポジトリは「一次情報源から辿れる材料」の台帳であり、溶け込み（改正を反映した現行文の自動生成）や現行性の法的判定は行いません。
- 関連法令の検索・ルーティングについては姉妹リポジトリ `kenchiku-law-routing` も参照してください。
