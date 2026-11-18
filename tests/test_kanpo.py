"""domain.json・AMEND_REF・官報本文切り出しのテスト。実行: python -m unittest discover tests"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kenchiku_kokuji.common import is_excluded, load_domain  # noqa: E402
from kenchiku_kokuji.source_kanpo import (  # noqa: E402
    _domain_match, _slice_notice_from_pages, extract_amend_targets)


class TestDomainConfig(unittest.TestCase):
    def test_load_domain_has_single_source_keys(self):
        domain = load_domain()
        for key in ("law_names", "org_keywords", "title_keywords",
                    "issuer_keywords", "exclude_keywords"):
            self.assertIn(key, domain)
            self.assertTrue(domain[key])
        self.assertIn("建築", domain["title_keywords"])
        self.assertIn("消防", domain["issuer_keywords"]["mic"])

    def test_noise_filtering_precision(self):
        domain = load_domain()
        # 港湾・航空・船舶・鉄道・車両などは除外される
        for title in (
            "港湾法の規定に基づく告示",
            "航空法施行規則の規定により指定する件",
            "船舶安全法の規定に基づく技術基準",
            "道路運送車両法に基づく型式指定",
            "鉄道事業法に基づく安全基準",
        ):
            self.assertTrue(is_excluded(title, domain), title)
            self.assertFalse(_domain_match(title, "mlit", domain, set()), title)

        # 「自動車の転回広場」のような正当な建築告示は除外されない
        legit = "建築基準法施行令第百四十四条の四の規定により定める自動車の転回広場に関する基準"
        self.assertFalse(is_excluded(legit, domain))
        self.assertTrue(_domain_match(legit, "mlit", domain, set()))

    def test_existing_notices_jsonl_loads_cleanly(self):
        ledger = Path(__file__).resolve().parents[1] / "data" / "notices.jsonl"
        if not ledger.exists():
            self.skipTest("data/notices.jsonl not present")
        lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreater(len(lines), 0)
        sample = json.loads(lines[0])
        self.assertIn("id", sample)
        self.assertIn("title", sample)


class TestAmendRef(unittest.TestCase):
    def test_arabic_era_year(self):
        targets = extract_amend_targets("平成30年国土交通省告示第1086号の一部を改正する件")
        self.assertEqual(targets, ["mlit-2018-1086"])

    def test_kanji_era_year_and_gannen(self):
        self.assertEqual(
            extract_amend_targets("昭和五十六年建設省告示第千百号の一部を改正する件"),
            ["kensetsu-1981-1100"],
        )
        self.assertEqual(
            extract_amend_targets("令和元年国土交通省告示第百号の一部を改正する件"),
            ["mlit-2019-100"],
        )

    def test_multi_target_amendment(self):
        title = (
            "昭和五十六年建設省告示第千百号及び"
            "平成十二年建設省告示第千四百六十一号の一部を改正する件"
        )
        self.assertEqual(
            extract_amend_targets(title),
            ["kensetsu-1981-1100", "kensetsu-2000-1461"],
        )

    def test_domain_match_tier2_multi_target(self):
        domain = load_domain()
        title = (
            "昭和五十六年建設省告示第千百号及び"
            "平成十二年建設省告示第千四百六十一号の一部を改正する件"
        )
        self.assertTrue(_domain_match(title, "mlit", domain, {"kensetsu-2000-1461"}))
        self.assertFalse(_domain_match(title, "mlit", domain, {"kensetsu-1970-1"}))

    def test_non_amendment_citation_ignored(self):
        self.assertEqual(
            extract_amend_targets("昭和五十六年建設省告示第千百号の規定に基づき定める件"),
            [],
        )


class TestSliceNoticePreservesStructure(unittest.TestCase):
    def test_preserves_newlines_and_no_3page_or_12000_cap(self):
        long_para = "建築基準法施行令の規定により次のように定める。\n" + ("第一項の規定。\n" * 1800)
        self.assertGreater(len(long_para), 12000)
        pages = [
            "〇国土交通省告示\n第千三百十六号\n" + long_para[:4000],
            long_para[4000:8000],
            long_para[8000:12000],
            long_para[12000:],
            "〇総務省告示第三百号\n別の告示。",
        ]
        sliced = _slice_notice_from_pages(pages, "国土交通省", 1316)
        self.assertIn("\n", sliced)
        self.assertGreater(len(sliced), 12000)
        self.assertTrue(sliced.startswith("国土交通省告示\n第千三百十六号"))
        self.assertNotIn("総務省告示", sliced)


if __name__ == "__main__":
    unittest.main()
