"""obtain_text のページ直取りテスト。実行: python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kenchiku_kokuji.build import accept_page_text, decode_html  # noqa: E402


def _page(title, body):
    return ("<html><head><title>国土交通省</title></head><body>"
            f"<h1>{title}</h1><p>{body}</p>"
            "<p>All Rights Reserved, Copyright (C) 2003, Ministry of Land, "
            "Infrastructure and Transport</p>"
            "</body></html>")


class TestAcceptPageText(unittest.TestCase):
    def test_short_notice_accepted(self):
        # 実測: kensetsu-1962-2754 のSGMLページは全文で約410字
        title = ("建設業法施行令第二十七条の七の規定による二級の技術検定に合格した"
                 "者について免除する一級の技術検定の実地試験")
        body = ("昭和三十七年十一月一日 建設省告示第二千七百五十四号 "
                "建設業法施行令第二十七条の七の規定により二級の技術検定に合格した"
                "者について免除する一級の技術検定の実地試験の一部は、検定種目を"
                "建設機械施工とする場合における二級の技術検定の実地試験において"
                "合格した科目に関するものとする。")
        text, how = accept_page_text(_page(title, body), title)
        self.assertEqual(how, "page_text_short")
        self.assertIn("建設機械施工", text)

    def test_long_page_unchanged(self):
        title = "なんらかの告示の件名"
        text, how = accept_page_text(_page(title, "あ" * 900), title)
        self.assertEqual(how, "page_text")

    def test_title_missing_rejected(self):
        html = _page("別の告示の件名", "x" * 300)
        text, how = accept_page_text(html, "求める告示の件名がここに入る")
        self.assertEqual((text, how), ("", "no_text_found"))

    def test_tiny_page_rejected(self):
        title = "短い件名"
        text, how = accept_page_text(_page(title, "短"), title)
        self.assertEqual((text, how), ("", "no_text_found"))


class TestDecodeHtml(unittest.TestCase):
    def test_shift_jis_page(self):
        # 実測: 国交省SGML原文ページは charset=shift_jis 宣言。
        # UTF-8決め打ちでは文字化けし、タイトル照合が効かず no_text_found になった。
        title = ("建築基準法施行令第百四十四条の四第一項第一号ハの規定により"
                 "国土交通大臣が定める自動車の転回広場に関する基準")
        html = ("<html><head><meta http-equiv=\"Content-Type\" "
                "content=\"text/html; charset=shift_jis\"></head><body>"
                f"<h1>{title}</h1><p>一 道の中心線からの水平距離が二メートルを"
                "こえる区域内において小型四輪自動車のうち最大なものが二台以上"
                "停車することができるものであること。</p></body></html>")
        text = decode_html(html.encode("shift_jis"), title)
        self.assertIn(title, text)
        self.assertIn("小型四輪自動車", text)

    def test_utf8_page_without_meta(self):
        title = "なんらかの告示の件名"
        html = f"<html><body><h1>{title}</h1><p>本文あいうえお。</p></body></html>"
        text = decode_html(html.encode("utf-8"), title)
        self.assertIn(title, text)


if __name__ == "__main__":
    unittest.main()
