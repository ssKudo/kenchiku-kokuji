"""共通ユーティリティ: 元号・漢数字の正規化、告示IDの生成、HTTP取得。

告示の文書番号は資料によって表記が揺れる(例: 1024 / １０２４ / 千二十四 /
一〇二四)。ここで半角アラビア数字に統一し、(発出者, 西暦年, 番号) を
一意キーとして扱えるようにする。
"""
from __future__ import annotations

import re
import time
from urllib.request import Request, urlopen

USER_AGENT = "kenchiku-kokuji/0.1 (public-source archive; polite crawler)"

ERA_OFFSET = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
KANJI_DIGIT = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9}
FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def kanji_to_int(s: str) -> int | None:
    """漢数字(千四百三十八など)・アラビア数字・混在を int に変換する。"""
    s = s.translate(FULLWIDTH).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if not any(u in s for u in "十百千万") and all(c in KANJI_DIGIT for c in s):
        # 位取り表記(一〇二四 = 1024)
        return int("".join(str(KANJI_DIGIT[c]) for c in s))
    total, cur, seen = 0, 0, False
    for ch in s:
        if ch in KANJI_DIGIT:
            cur = KANJI_DIGIT[ch]
            seen = True
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "千":
            total += (cur or 1) * 1000
            cur = 0
        elif ch == "万":
            total = (total + cur) * 10000
            cur = 0
        else:
            return None
    return total + cur if seen else None


def era_to_gregorian(era: str, year: int) -> int:
    return ERA_OFFSET[era] + year


def parse_era_date(text: str) -> str | None:
    """「令和8年10月30日」「平成30年9月10日」などを ISO 日付に変換する。"""
    text = text.translate(FULLWIDTH)
    m = re.search(r"(明治|大正|昭和|平成|令和)(元|\d+)年(\d+)月(\d+)日", text)
    if not m:
        return None
    y = 1 if m.group(2) == "元" else int(m.group(2))
    return f"{era_to_gregorian(m.group(1), y):04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


# 発出者の正規化キー
ISSUER_KEYS = [
    ("建設省", "kensetsu"),
    ("国土交通省", "mlit"),
    ("厚生労働省", "mhlw"),
    ("環境省", "env"),
    ("経済産業省", "meti"),
    ("総務省", "mic"),
    ("法務省", "moj"),
    ("農林水産省", "maff"),
    ("財務省", "mof"),
    ("文部科学省", "mext"),
    ("外務省", "mofa"),
    ("防衛省", "mod"),
]


def parse_notice_number(number_raw: str, date_iso: str | None) -> dict:
    """文書番号文字列から (issuer_key, year, number) を取り出す。

    例:
      "国土交通省告示第386号" + date 2019-03-20 -> mlit / 2019 / 386
      "平成30年国土交通省告示第1086号"          -> mlit / 2018 / 1086
      "建設省告示第千三百九十九号" + date       -> kensetsu / year / 1399
      "令和8年国土交通省告示第1316号"           -> mlit / 2026 / 1316
      "令和8年厚生労働省・国土交通省・環境省告示第2号" -> joint / 2026 / 2
    """
    raw = number_raw.translate(FULLWIDTH)
    m = re.search(r"第([〇一二三四五六七八九十百千万\d]+)号", raw)
    number = kanji_to_int(m.group(1)) if m else None

    year = None
    em = re.search(r"(明治|大正|昭和|平成|令和)(元|\d+)年", raw)
    if em:
        y = 1 if em.group(2) == "元" else int(em.group(2))
        year = era_to_gregorian(em.group(1), y)
    elif date_iso:
        year = int(date_iso[:4])

    issuers = [key for name, key in ISSUER_KEYS if name in raw]
    if len(issuers) >= 2:
        issuer = "joint-" + "-".join(sorted(set(issuers)))
    elif issuers:
        issuer = issuers[0]
    else:
        issuer = "unknown"
    return {"issuer": issuer, "year": year, "number": number}


def make_id(issuer: str, year: int | None, number: int | None, fallback: str) -> str:
    if year is not None and number is not None:
        return f"{issuer}-{year}-{number}"
    return fallback


def fetch(url: str, timeout: int = 30, retries: int = 2) -> bytes:
    """礼儀正しい取得: 明示的なUA、リトライは控えめに。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001 - 呼び出し側で status に記録する
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]
