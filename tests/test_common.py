"""正規化ロジックのテスト。実行: python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kenchiku_kokuji.common import kanji_to_int, parse_era_date, parse_notice_number  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_kanji(self):
        self.assertEqual(kanji_to_int("千四百三十八"), 1438)
        self.assertEqual(kanji_to_int("千二十四"), 1024)
        self.assertEqual(kanji_to_int("一〇二四"), 1024)
        self.assertEqual(kanji_to_int("１０２４"), 1024)
        self.assertEqual(kanji_to_int("386"), 386)
        self.assertIsNone(kanji_to_int(""))

    def test_era_date(self):
        self.assertEqual(parse_era_date("令和8年10月30日公布"), "2026-10-30")
        self.assertEqual(parse_era_date("平成31年1月1日"), "2019-01-01")
        self.assertEqual(parse_era_date("令和元年6月25日"), "2019-06-25")

    def test_number(self):
        p = parse_notice_number("国土交通省告示第386号", "2019-03-20")
        self.assertEqual((p["issuer"], p["year"], p["number"]), ("mlit", 2019, 386))
        p = parse_notice_number("平成30年国土交通省告示第1086号", None)
        self.assertEqual((p["issuer"], p["year"], p["number"]), ("mlit", 2018, 1086))
        p = parse_notice_number("建設省告示第千三百九十九号", "2000-12-01")
        self.assertEqual((p["issuer"], p["year"], p["number"]), ("kensetsu", 2000, 1399))
        p = parse_notice_number("令和8年厚生労働省・国土交通省・環境省告示第2号", None)
        self.assertEqual(p["year"], 2026)
        self.assertEqual(p["number"], 2)
        self.assertTrue(p["issuer"].startswith("joint-"))


if __name__ == "__main__":
    unittest.main()


class TestWebHeading(unittest.TestCase):
    def test_heading_with_enforcement_date(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from kenchiku_kokuji.source_web import HEADING
        m = HEADING.search("【令和8年3月3日公布・令和8年5月29日施行】")
        self.assertIsNotNone(m)
        self.assertEqual(parse_era_date(m.group(1)), "2026-03-03")
