"""官報アーカイブ源の見出し検出のテスト。実行: python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kenchiku_kokuji.source_kanpo_archive import (  # noqa: E402
    find_headings, issuer_key_of, parse_toc, promulgation_offset,
    segment_issue)


class TestFindHeadings(unittest.TestCase):
    def test_true_heading_after_separator(self):
        text = "前の告示の本文である。〇総務省告示第三百三十五号消防法の規定に基づき告示する。"
        heads = find_headings(text)
        self.assertEqual(len(heads), 1)
        self.assertEqual(heads[0]["issuer_key"], "mic")
        self.assertEqual(heads[0]["number"], 335)

    def test_era_prefixed_reference_rejected(self):
        # 目次・本文中の参照は「年」が発出者名の直前に付き、直後が「の」になる
        text = "〇令和三年国土交通省告示第三百二十六号の一部を改正する件（同一三一九）"
        self.assertEqual(find_headings(text), [])
        text2 = "検査方法（令和五年農林水産省告示第七百四十四号）の一部を次のように改正し"
        self.assertEqual(find_headings(text2), [])

    def test_particle_followed_reference_rejected(self):
        text = "前段の規定は〇ではないが国土交通省告示第千三百九十九号の規定により適用する"
        self.assertEqual(find_headings(text), [])

    def test_joint_issuer(self):
        text = "〇厚生労働省・国土交通省告示第二号建築基準法の規定に基づき定める。"
        heads = find_headings(text)
        self.assertEqual(len(heads), 1)
        self.assertEqual(heads[0]["issuer_key"], "joint-mhlw-mlit")
        self.assertEqual(issuer_key_of("国土交通省"), "mlit")

    def test_segmentation(self):
        pages = ["〇国土交通省告示第一号建築基準法に基づき定める。令和八年四月一日国土交通大臣",
                 "〇総務省告示第二号消防法に基づき告示する。"]
        segs = segment_issue(pages)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["page"], 1)
        self.assertEqual(segs[1]["page"], 2)
        self.assertIn("国土交通大臣", segs[0]["text"])
        self.assertNotIn("総務省告示", segs[0]["text"])

    def test_monthly_index_reference_rejected(self):
        # 月次索引の一覧項目(令和二年告示708号の改正への参照)は見出しではない
        text = ("二六四三五租税特別措置法施行令第二十六条第十八項及び第二十六条の"
                "四第十七項の規定に基づき、令和二年国土交通省告示第七百八号の一部を"
                "改正する件二六四三六砂防法第二条の土地を指定する件")
        self.assertEqual(find_headings(text), [])

    def test_boundary_only_heading(self):
        # 地方整備局の見出しは境界として拾い、分類対象にはならない
        text = ("〇国土交通省告示第千三百三十三号管理業務に関する実務についての講習"
                "を指定するための基準等（令和三年国土交通省告示第四百八十六号）第七条"
                "の規定に基づき公示する。令和八年十一月六日国土交通大臣井林辰憲"
                "〇関東地方整備局告示第二百四十号次のように道路の供用を開始するので、"
                "道路法の規定に基づき、告示する。令和八年十一月六日関東地方整備局長")
        heads = find_headings(text)
        self.assertEqual(len(heads), 2)
        self.assertTrue(heads[1].get("boundary_only"))
        segs = segment_issue([text])
        self.assertEqual(len(segs), 2)
        self.assertNotIn("関東地方整備局告示", segs[0]["text"])

    def test_parse_toc(self):
        toc_text = (
            "〇高速自動車国道に関する件（国土交通一三三〇）採"
            "〇賃貸住宅の管理業務等の適正化に関する法律施行規則第十四条第二号の"
            "指定講習にかかる指定簿に掲げる事項の変更の届出があった件（同一三三三）"
            "〇道路に関する件（関東地方整備局二三九〜二四三）")
        toc, _ = parse_toc([[toc_text]])
        self.assertEqual(toc[("mlit", 1330)], "高速自動車国道に関する件")
        self.assertIn("賃貸住宅", toc[("mlit", 1333)])
        # 範囲表記は展開される(境界用なので件名は同じ)
        self.assertEqual(toc[("mlit", 1333)], toc.get(("mlit", 1333)))

    def test_promulgation_formula(self):
        body = ("国土交通省告示第千三百十六号建築基準法施行令第十九条第二項の規定に"
                "基づき、次のように定める。令和八年十月三十日国土交通大臣井林辰憲")
        self.assertIsNotNone(promulgation_offset(body))
        self.assertEqual(promulgation_offset(body), body.index("令和八年"))
        # 発令文言が無ければ None(目次・参照の類)
        self.assertIsNone(promulgation_offset("国土交通省告示第千三百十六号の一部を改正する件"))
        # 算用数字の日付も許す
        self.assertIsNotNone(promulgation_offset("定める。令和8年10月30日国土交通大臣井林辰憲"))


if __name__ == "__main__":
    unittest.main()
