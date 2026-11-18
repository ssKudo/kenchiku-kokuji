"""完全マップ(metadata_only/スタブ補完)・省エネ法・消防庁・NDL日本法令索引のオフラインテスト。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kenchiku_kokuji.build import (  # noqa: E402
    build, cleanup_orphan_texts, compute_text_status, format_index_tsv,
    format_missing_texts_report, merge, repair_and_prune,
    synthesize_missing_base_stubs)
from kenchiku_kokuji.common import load_domain, parse_notice_number  # noqa: E402
from kenchiku_kokuji.source_fdma import parse_fdma_html  # noqa: E402
from kenchiku_kokuji.source_ndl import (  # noqa: E402
    build_search_params, extract_csrf, parse_detail_page, parse_result_page)
from kenchiku_kokuji.source_web import parse_shoene_html  # noqa: E402


class TestCompleteMapAndStubs(unittest.TestCase):
    def test_compute_text_status_variants(self):
        self.assertEqual(compute_text_status({"text_file": "a.txt", "text_chars": 2000, "text_how": "pdf"}), "ok")
        self.assertEqual(compute_text_status({"text_file": "a.txt", "text_chars": 400, "text_how": "page_text_short"}), "short")
        self.assertEqual(compute_text_status({"text_file": "a.txt", "text_chars": 12000, "text_how": "kanpo_archive"}), "truncated")
        self.assertEqual(compute_text_status({"text_file": "", "text_how": "pdf"}), "image_pdf")
        self.assertEqual(compute_text_status({"text_file": "", "text_how": "metadata_only"}), "metadata_only")
        self.assertEqual(compute_text_status({"text_file": "", "text_how": "no_text_found"}), "no_text_found")

    def test_synthesize_missing_base_stubs_and_build_metadata_only(self):
        recs = [{
            "id": "mlit-2026-464",
            "title": "平成二十六年国土交通省告示第四百十七号の一部を改正する告示",
            "number_raw": "国土交通省告示第464号",
            "issuer": "mlit",
            "year": 2026,
            "number": 464,
            "date": "2026-04-01",
            "kind": "amendment",
            "org": "",
            "sources": [{"type": "kanpo", "page_url": "https://example.com"}],
            "document_url": "",
            "attachments": [],
            "text_inline": "本文あり\n段落。",
        }]
        with_stubs = synthesize_missing_base_stubs(recs)
        by_id = {r["id"]: r for r in with_stubs}
        self.assertIn("mlit-2014-417", by_id)
        stub = by_id["mlit-2014-417"]
        self.assertEqual(stub["text_status"], "metadata_only")
        self.assertEqual(stub["amended_by"], ["mlit-2026-464"])
        self.assertEqual(by_id["mlit-2026-464"]["amends"], ["mlit-2014-417"])

        with tempfile.TemporaryDirectory() as td:
            built = build(with_stubs, Path(td))
            b_by_id = {r["id"]: r for r in built}
            self.assertEqual(b_by_id["mlit-2014-417"]["text_status"], "metadata_only")
            self.assertEqual(b_by_id["mlit-2014-417"]["text_how"], "metadata_only")
            self.assertEqual(b_by_id["mlit-2026-464"]["text_status"], "short")
            report = format_missing_texts_report(built)
            self.assertIn("mlit-2014-417", report)

    def test_merge_preserves_complete_map_fields(self):
        stub = {
            "id": "kensetsu-1999-1287",
            "title": "（未取得・被改正告示）kensetsu-1999-1287",
            "date": None,
            "org": "",
            "document_url": "",
            "sources": [{"type": "amend_ref", "referenced_by": "mlit-2026-1109"}],
            "attachments": [],
            "amended_by": ["mlit-2026-1109"],
        }
        ndl = {
            "id": "kensetsu-1999-1287",
            "title": "耐火構造の構造方法を定める件",
            "number_raw": "建設省告示第1287号",
            "date": "1999-05-24",
            "org": "",
            "document_url": "",
            "ndl_url": "https://hourei.ndl.go.jp/simple/detail?lawId=12345",
            "basis_laws": ["建築基準法"],
            "sources": [{"type": "ndl", "page_url": "https://hourei.ndl.go.jp/simple/detail?lawId=12345"}],
            "attachments": [],
        }
        merged = merge([stub], [ndl])
        self.assertEqual(len(merged), 1)
        rec = merged[0]
        self.assertEqual(rec["title"], "耐火構造の構造方法を定める件")
        self.assertEqual(rec["date"], "1999-05-24")
        self.assertEqual(rec["basis_laws"], ["建築基準法"])
        self.assertEqual(rec["amended_by"], ["mlit-2026-1109"])
        self.assertIn("hourei.ndl.go.jp", rec["ndl_url"])

    def test_repair_and_prune_and_cleanup_orphans(self):
        p = parse_notice_number("昭和50.年消防庁告示7", None)
        self.assertEqual((p["issuer"], p["year"], p["number"]), ("fdma", 1975, 7))

        domain = load_domain()
        recs = [
            {
                "id": "fdma-54",
                "title": "キュービクル式非常電源専用受電設備の基準",
                "number_raw": "昭和50.年消防庁告示7",
                "issuer": "fdma",
                "year": None,
                "number": 7,
                "date": None,
                "org": "消防庁",
                "document_url": "https://example.com/1",
                "sources": [{"type": "fdma", "page_url": "https://example.com"}],
                "attachments": [],
                "text_file": "texts/fdma-54.txt",
                "text_chars": 1000,
                "text_sha256": "abc",
                "text_how": "pdf",
            },
            {
                "id": "fdma-1975-7",
                "title": "キュービクル式非常電源専用受電設備の基準",
                "number_raw": "消防庁告示第7号",
                "issuer": "fdma",
                "year": 1975,
                "number": 7,
                "date": "1975-05-01",
                "org": "",
                "document_url": "",
                "sources": [{"type": "ndl", "page_url": "https://hourei.ndl.go.jp/1"}],
                "attachments": [],
            },
            {
                "id": "kensetsu-1987-60",
                "title": "ダム工事総括管理業務に関する知識及び技術の審査・証明事業認定規程",
                "number_raw": "建設省告示第60号",
                "issuer": "kensetsu",
                "year": 1987,
                "number": 60,
                "date": "1987-01-01",
                "org": "",
                "document_url": "",
                "sources": [{"type": "ndl", "page_url": "https://hourei.ndl.go.jp/2"}],
                "attachments": [],
            },
            {
                "id": "fdma-2008-27",
                "title": "特定施設水道連結型スプリンクラー設備の基準",
                "number_raw": "消防庁告示第27号",
                "issuer": "fdma",
                "year": 2008,
                "number": 27,
                "date": "2008-01-01",
                "org": "消防庁",
                "document_url": "",
                "sources": [{"type": "fdma", "page_url": "https://example.com"}],
                "attachments": [],
            },
        ]
        cleaned = repair_and_prune(recs, domain)
        ids = [r["id"] for r in cleaned]
        self.assertEqual(ids, ["fdma-1975-7", "fdma-2008-27"])
        self.assertEqual(cleaned[0]["text_file"], "texts/fdma-54.txt")

        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td) / "texts"
            tdir.mkdir()
            (tdir / "fdma-54.txt").write_text("keep", encoding="utf-8")
            (tdir / "kensetsu-1987-60.txt").write_text("orphan", encoding="utf-8")
            removed = cleanup_orphan_texts(cleaned, Path(td))
            self.assertEqual(removed, 1)
            self.assertTrue((tdir / "fdma-54.txt").exists())
            self.assertFalse((tdir / "kensetsu-1987-60.txt").exists())

    def test_format_index_tsv_sanitizes_tabs_and_newlines(self):
        recs = [{
            "id": "mlit-2026-1",
            "date": "2026-01-05",
            "year": 2026,
            "issuer": "mlit",
            "kind": "enactment",
            "text_status": "ok",
            "text_chars": 1500,
            "number_raw": "国土交通省告示\t第1号",
            "title": "建築基準法\nに基づく\r\n告示",
            "text_file": "texts/mlit-2026-1.txt",
        }]
        tsv = format_index_tsv(recs)
        lines = tsv.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[0],
            "id\tdate\tyear\tissuer\tkind\ttext_status\ttext_chars\tnumber_raw\ttitle\ttext_file",
        )
        cols = lines[1].split("\t")
        self.assertEqual(len(cols), 10)
        self.assertEqual(cols[0], "mlit-2026-1")
        self.assertEqual(cols[7], "国土交通省告示 第1号")
        self.assertEqual(cols[8], "建築基準法 に基づく 告示")


class TestShoeneWebParser(unittest.TestCase):
    def test_parse_shoene_cont4(self):
        html = """
        <div id="cont4"><h3 class="title">告示</h3>
          <div class="topicsListJutaku"><ul class="list01">
            <li><div class="text"><a href="/common/001880626.pdf">建築物エネルギー消費性能基準等を定める省令における算出方法等に係る事項（令和５年国土交通省告示第970号） (PDF形式:247KB)</a></div></li>
            <li><div class="text"><a href="https://www.jsbc.or.jp/other.pdf">外部資料</a></div></li>
          </ul></div>
        </div>
        <div id="cont5"><h3>通知</h3><a href="/common/999.pdf">通知（令和5年国土交通省告示第1号）</a></div>
        """
        recs = parse_shoene_html(html)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["id"], "mlit-2023-970")
        self.assertEqual(recs[0]["document_url"], "https://www.mlit.go.jp/common/001880626.pdf")


class TestFdmaParser(unittest.TestCase):
    def test_parse_fdma_omitted_dai_gou_and_noise_filter(self):
        p = parse_notice_number("昭和39年消防庁告示7", None)
        self.assertEqual((p["issuer"], p["year"], p["number"]), ("fdma", 1964, 7))

        domain = load_domain()
        html = """
        <ul>
          <li><a href="/laws/kokuji/post27/">消防水利の基準（昭和39年消防庁告示7）</a></li>
          <li><a href="/laws/kokuji/assets/h14_kokuji1.pdf">対象火気設備等の位置、構造及び管理並びに対象火気器具等の取扱いに関する条例の制定に関する基準を定める件（平成14年消防庁告示1）(PDF)</a></li>
          <li><a href="/laws/kokuji/assets/h15_kokuji3.pdf">消防法施行規則の規定に基づく件（平成14年総務省告示568）</a></li>
          <li><a href="/laws/kokuji/post99/">消防団員の服制に関する基準（昭和40年消防庁告示1）</a></li>
        </ul>
        """
        recs = parse_fdma_html(html, domain)
        ids = [r["id"] for r in recs]
        self.assertIn("fdma-2002-1", ids)
        self.assertIn("mic-2002-568", ids)
        # 消防団員の服制はノイズ除外される
        self.assertNotIn("fdma-1965-1", ids)


class TestNdlParser(unittest.TestCase):
    def test_csrf_and_search_params(self):
        html = '<form><input type="hidden" name="_csrf" value="abc-123-xyz"/></form>'
        csrf = extract_csrf(html)
        self.assertEqual(csrf, "abc-123-xyz")
        params = dict(build_search_params(csrf, "建設/住宅・建築/建築"))
        self.assertEqual(params["lfd14_02"], "1")
        self.assertEqual(params["_lfd0103_02"], "on")
        self.assertNotIn("lfd0103_02", params)
        self.assertEqual(params["cc_02"], "建設/住宅・建築/建築")

    def test_parse_result_and_detail_page(self):
        result_html = """
        <h1>検索結果　351件</h1>
        <ul>
          <li>
            <h3>1件目：<a href="/simple/detail?lawId=0000123456&amp;current=1">地階を除く階数が十一以上である建築物の屋上に設ける冷却塔設備の防火上支障のない構造方法等</a></h3>
            <p>現行法令\u2003告示･訓令\u2003（昭和40年12月18日建設省告示第3411号）</p>
          </li>
        </ul>
        """
        total, recs = parse_result_page(result_html, classification="建設/住宅・建築/建築")
        self.assertEqual(total, 351)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["id"], "kensetsu-1965-3411")
        self.assertEqual(recs[0]["date"], "1965-12-18")
        self.assertEqual(recs[0]["ndl_status"], "現行")
        self.assertEqual(recs[0]["text_how"], "metadata_only")

        detail_html = """
        <header><h1>冷却塔設備の防火上支障のない構造方法</h1><p>（昭和40年12月18日建設省告示第3411号）</p></header>
        <section id="infomation"><ul><li><span>根拠法令：建築基準法、建築基準法施行令</span></li></ul></section>
        <h2><a name="history">2. 法令沿革</a></h2>
        <ul><li><h3>改正：<a href="/simple/detail?lawId=999">平成12年建設省告示第1400号</a></h3></li></ul>
        <h2><a name="amended">3. 被改正法令</a></h2>
        <ul><li><h3>被改正：<span>昭和35年建設省告示第2207号</span></h3></li></ul>
        """
        detail = parse_detail_page(detail_html, "0000123456")
        self.assertEqual(detail["basis_laws"], ["建築基準法", "建築基準法施行令"])
        self.assertEqual(detail["amended_by"], ["kensetsu-2000-1400"])
        self.assertEqual(detail["amends"], ["kensetsu-1960-2207"])


if __name__ == "__main__":
    unittest.main()
