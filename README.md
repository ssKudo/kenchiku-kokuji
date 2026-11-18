# kenchiku-kokuji

建築・防火関係の**告示**を、国土交通省・官報・総務省消防庁・国立国会図書館「日本法令索引」などの一次情報源から収集し、機械可読な台帳と本文テキストのコーパスとして整えるパイプライン。

告示には法令のような統一データベースが無く（デジタル庁も公式に認めている）、現行文は商業出版・有料DBの編集成果の中にしか存在しない。本プロジェクトはその代替ではなく、**一次情報源から辿れる材料を集める**ことを目的とする。溶け込み（改正を適用した現行文の生成）や現行性の判定は行わない。各レコードに出典URLと文書番号を保持し、原本で検証できる形にする。

## 収集源（いずれも一次情報・無料公開分）

| 情報源 | 内容 | 備考 |
|---|---|---|
| 告示・通達一覧（Excel） | https://www.mlit.go.jp/notice/ で配布 | 全省庁分を含む。住宅局分は2019年で更新停止（実測） |
| 住宅局「建築基準法等に基づく告示の制定・改正について」 | 制定・改正の告知ページ | 2020年以降分は実質ここにしか出ない |
| 住宅局「建築物省エネ法 最新の法令」 | `house/29.html`（`#cont4` 告示セクション） | 省エネ法関連の現行告示PDFを補完 |
| 官報（インターネット版） | 国立印刷局 https://www.kanpo.go.jp/ の目次＋本文PDF | 直近90日分が無料公開。`data/kanpo_state.json` で差分管理 |
| 官報アーカイブ（kanpo-downloader） | https://github.com/kanpo-downloader/kanpo-2025・kanpo-2026 | 2025-04以降の官報PDF全号アーカイブ（`--include-kanpo-archive`） |
| 総務省消防庁「告示一覧」 | https://www.fdma.go.jp/laws/kokuji/ | 消防法・防火・設備関連告示を収集（`--include-fdma`） |
| 国立国会図書館「日本法令索引」 | https://hourei.ndl.go.jp/simple/ | 2004年8月以降有効・制定分のマスター台帳（`--include-ndl`）。本文が無いため無料一次ソースに無いものは `metadata_only` として完全マップに組み込む |

対象ドメインの定義（法令名・件名キーワード・発出者別キーワード・非建築ノイズ除外ルール）は `config/domain.json` に一本化している。

## 出力

- `data/index.tsv` — AIエージェント・高速検索向けの1告示1行TSVインデックス（`id`, `date`, `year`, `issuer`, `kind`, `text_status`, `text_chars`, `number_raw`, `title`, `text_file`）
- `data/notices.jsonl` — 告示1件＝1行の台帳。IDは `発出者-西暦年-番号`（例: `mlit-2026-1316`、`kensetsu-2000-1399`、`fdma-1964-7`）。改正関係（`amends` / `amended_by`）や `text_status`（`ok` / `short` / `truncated` / `metadata_only` / `image_pdf` / `no_text_found`）を保持
- `data/texts/<id>.txt` — 本文テキスト（改行・段落構造を保持してPDF/HTMLから抽出）
- `data/stats.md` — 収集統計と本文未取得レコード一覧
- `data/missing_texts.md` — オフライン・有料検索での補完対象一覧（`metadata_only`, `image_pdf`, `no_text_found`, `truncated`）
- `AGENTS.md` / `llms.txt` — AIエージェント向けの検索・参照ガイド

## 使い方

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m kenchiku_kokuji.run_all --include-fdma
python -m unittest discover tests -v
```

主なオプション:
- `--force`: 取得済み本文も含めて全件再取得
- `--include-fdma`: 消防庁告示一覧を含める（週次CIで有効）
- `--include-ndl` / `--ndl-details`: 日本法令索引マスター台帳を含める（1.5秒以上の礼儀正しい間隔で取得）
- `--include-kanpo-archive`: `kanpo-downloader` の過去号PDFアーカイブを含める
- `--skip-build`: ネットワーク取得を行わず既存台帳の整理・統計/`index.tsv` 再生成のみ行う

再実行すると、取得済みの本文は再取得しない（台帳が再開ポイントになる）。

## 設計判断と限界

詳しくは `docs/DESIGN.md`。要点:

- **完全マップ方針**: 無料一次ソースに本文が存在しない古い告示・被改正告示も `metadata_only` として台帳に残し、母集団と欠損箇所を `data/missing_texts.md` で可視化している
- 改正告示は独立したレコードとして収集し、`amends` / `amended_by` で参照関係を結線する（溶け込み本文の生成は行わない）
- 図表中心の告示や非テキストPDF（`image_pdf`）はテキスト抽出できない。文字数や `text_status` を確認のうえ原本PDFで検証すること

## ライセンスと出典

- コード: MIT License（`LICENSE`）
- 告示の本文: 法令・告示の原文は著作権法第13条により著作権の対象外。
  本コーパスは一次情報源から自ら収集・再構成したもので、
  商業DB等の編集成果物は一切含まない
- 出典:
  - 国土交通省「告示・通達一覧」 https://www.mlit.go.jp/notice/
  - 国土交通省住宅局「建築基準法等に基づく告示の制定・改正について」
    https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000096.html
  - 国土交通省住宅局「建築物省エネ法 最新の法令」
    https://www.mlit.go.jp/jutakukentiku/house/29.html
  - 総務省消防庁「告示一覧」 https://www.fdma.go.jp/laws/kokuji/
  - 国立国会図書館「日本法令索引」 https://hourei.ndl.go.jp/
  - 官報発行サイト（内閣府） https://www.kanpo.go.jp/
    （官報のコンテンツは公共データ利用規約（第1.0版）に基づき出典を記載して利用）
  - 各レコードには出典URL（`sources` / `document_url` / `ndl_url`）を保持しており、
    原本で検証できる
- 取得は各サイトの負荷に配慮し、間隔を空けて行う（`build.py` の `DELAY_SEC`、`source_ndl.py` の `>= 1.5s` 待機）

## 収集について

本プロジェクトの収集パイプラインの構築とデータの整備は、AIエージェントの支援を受けて行われた。収集は週次で自動実行され、新規に公布された建築・防火関係告示が台帳へ追加されていく。

現在の収録状況（2026-11-18時点）: **896件**（うち本文テキストあり **731件**〔82%〕・約482万字、`metadata_only` 160件）。詳細は `data/stats.md` および `data/missing_texts.md` を参照。
