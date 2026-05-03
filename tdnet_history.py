"""
tdnet_history.py
TDnetの過去履歴を遡って「現在進行中の自社株買い」を検索するモジュール。

【使い方】
    python tdnet_history.py          # デフォルト: 過去30営業日を検索
    python tdnet_history.py --days 60  # 過去60営業日を検索

【仕組み】
    TDnetは日付をURLパラメータで指定すると、その日の開示一覧を返す。
    例: https://www.release.tdnet.info/inbs/I_main_00.html?id=20260428
    これを営業日ごとに遡って「自己株式取得」を収集する。
"""

import argparse
import logging
import time
import csv
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# buyback_screener.py のクラスを再利用
from buyback_screener import (
    TDnetFetcher,
    PDFAnalyzer,
    MarketCapFetcher,
    ScreenedStock,
    TDNET_BASE_URL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tdnet_history")

# 祝日リスト（2025〜2026年の主な祝日）
# 本番運用時は jpholiday ライブラリを使うとより正確
HOLIDAYS = {
    "20260101", "20260112", "20260211", "20260223",
    "20260320", "20260429", "20260503", "20260504", "20260505",
    "20260720", "20260811", "20260921", "20260923", "20261012",
    "20261103", "20261123",
    "20250101", "20250113", "20250211", "20250224",
    "20250321", "20250429", "20250503", "20250504", "20250505",
    "20250721", "20250811", "20250915", "20250923", "20251013",
    "20251103", "20251124",
}


def get_business_days(num_days: int) -> list[str]:
    """
    今日から遡って num_days 営業日分の日付リストを返す（新しい順）。
    土日・祝日は除外する。

    Returns:
        List[str]: "YYYYMMDD" 形式の文字列リスト
    """
    result = []
    current = datetime.today()
    checked = 0

    while len(result) < num_days and checked < num_days * 3:
        checked += 1
        # 土日を除外
        if current.weekday() >= 5:
            current -= timedelta(days=1)
            continue
        # 祝日を除外
        date_str = current.strftime("%Y%m%d")
        if date_str in HOLIDAYS:
            current -= timedelta(days=1)
            continue
        result.append(date_str)
        current -= timedelta(days=1)

    return result


class TDnetHistoryFetcher:
    """
    TDnetの過去の開示一覧を日付指定で取得するクラス。

    TDnetのURL構造:
        https://www.release.tdnet.info/inbs/I_main_00.html?id=YYYYMMDD
    上記URLで指定日の開示一覧HTMLが取得できる。
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    BASE_URL = "https://www.release.tdnet.info/inbs/I_main_00.html"

    def __init__(self, keyword: str = "自己株式取得", sleep_sec: float = 1.5):
        self.keyword   = keyword
        self.sleep_sec = sleep_sec
        self.session   = requests.Session()
        self.session.headers.update(self.HEADERS)

    def fetch_day(self, date_str: str) -> list[dict]:
        """
        指定日の開示一覧から keyword に合致するものを返す。

        Args:
            date_str: "YYYYMMDD" 形式の日付文字列

        Returns:
            List[dict]: {"code", "name", "title", "pdf_url", "disclosed_at", "date"} のリスト
        """
        url = f"{self.BASE_URL}?id={date_str}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"  [{date_str}] 取得失敗: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        results = []

        # TDnetのテーブル行を解析
        for row in soup.select("tr"):
            cols = row.find_all("td")
            if len(cols) < 4:
                continue

            # タイトルリンクを探す
            title_tag = None
            for col in cols:
                a = col.find("a", href=True)
                if a and self.keyword in a.get_text(strip=True):
                    title_tag = a
                    break

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            texts = [c.get_text(strip=True) for c in cols]

            # 銘柄コードらしき4桁数字を探す
            code = ""
            for t in texts:
                m = re.search(r"\b(\d{4})\b", t)
                if m:
                    code = m.group(1)
                    break

            # 銘柄名（コードの隣の列が多い）
            name = ""
            for t in texts:
                if t and not re.match(r"^\d+$", t) and t != title:
                    name = t
                    break

            # PDFリンク
            href = title_tag.get("href", "")
            pdf_url = (
                href if href.startswith("http")
                else f"{TDNET_BASE_URL}{href}"
            )

            if not code:
                continue

            results.append({
                "code":         code.zfill(4),
                "name":         name,
                "title":        title,
                "pdf_url":      pdf_url,
                "disclosed_at": date_str,
            })

        return results

    def fetch_range(self, business_days: list[str]) -> list[dict]:
        """
        複数営業日分の開示を収集する。重複（同一銘柄）は除去する。

        Args:
            business_days: "YYYYMMDD" 形式の日付リスト（新しい順）

        Returns:
            重複除去済みの開示リスト
        """
        all_results = []
        seen_codes  = set()

        for date_str in business_days:
            logger.info(f"  {date_str} を検索中...")
            day_results = self.fetch_day(date_str)

            for item in day_results:
                code = item["code"]
                if code not in seen_codes:
                    seen_codes.add(code)
                    all_results.append(item)
                    logger.info(f"    発見: [{code}] {item['name']} ({date_str})")

            time.sleep(self.sleep_sec)

        logger.info(f"合計 {len(all_results)} 件の開示を発見（重複除去済み）")
        return all_results


class HistoryScreener:
    """
    過去履歴を遡って現在進行中の自社株買い銘柄をスクリーニングする。
    buyback_screener.py のフィルター（PDF解析・時価総額）を再利用する。
    """

    def __init__(
        self,
        days: int = 30,
        output_csv: Path = Path("buyback_history_candidates.csv"),
    ):
        self.days            = days
        self.output_csv      = output_csv
        self.history_fetcher = TDnetHistoryFetcher()
        self.tdnet_fetcher   = TDnetFetcher()   # PDFダウンロード用
        self.pdf_analyzer    = PDFAnalyzer()
        self.market_cap      = MarketCapFetcher()

    def run(self) -> list[ScreenedStock]:
        logger.info("=" * 60)
        logger.info(f"過去 {self.days} 営業日の自社株買い履歴を検索")
        logger.info("=" * 60)

        # ── STEP1: 営業日リストを生成 ──
        business_days = get_business_days(self.days)
        logger.info(f"検索期間: {business_days[-1]} 〜 {business_days[0]}")

        # ── STEP2: TDnet過去履歴を収集 ──
        disclosures = self.history_fetcher.fetch_range(business_days)

        if not disclosures:
            logger.warning("該当する開示が見つかりませんでした。")
            return []

        candidates: list[ScreenedStock] = []

        for disc in disclosures:
            logger.info(f"\n--- [{disc['code']}] {disc['name']} ({disc['disclosed_at']}) ---")

            # ── STEP3: PDF解析（市場外買付チェック） ──
            pdf_bytes = self.tdnet_fetcher.download_pdf(disc["pdf_url"])
            if pdf_bytes is None:
                logger.warning("  PDFをスキップ")
                continue

            pdf_text = self.pdf_analyzer.extract_text(pdf_bytes)
            if self.pdf_analyzer.is_market_outside(pdf_text):
                logger.info(f"  → 除外（市場外買付）")
                continue

            # ── STEP4: 時価総額チェック ──
            passed, cap = self.market_cap.passes_threshold(disc["code"])
            cap_oku = cap / 1_000_000_00
            logger.info(f"  時価総額: {cap_oku:,.1f}億円")

            if not passed:
                logger.info(f"  → 除外（時価総額不足）")
                continue

            stock = ScreenedStock(
                code=disc["code"],
                name=disc["name"],
                market_cap=cap,
                disclosed_at=disc["disclosed_at"],
            )
            candidates.append(stock)
            logger.info(f"  ✅ 通過: [{disc['code']}] {disc['name']} ({cap_oku:,.1f}億円) 発表:{disc['disclosed_at']}")

        # ── STEP5: 結果出力 ──
        self._print_results(candidates)
        self._save_csv(candidates)
        return candidates

    def _print_results(self, candidates: list[ScreenedStock]) -> None:
        print("\n" + "=" * 65)
        print(f"【現在進行中の自社株買い候補】（過去{self.days}営業日）")
        print("=" * 65)
        if not candidates:
            print("該当銘柄はありませんでした。")
        else:
            print(f"{'コード':<8} {'銘柄名':<20} {'時価総額(億円)':>14} {'発表日'}")
            print("-" * 65)
            for s in candidates:
                cap_oku = s.market_cap / 1_000_000_00
                print(f"{s.code:<8} {s.name:<20} {cap_oku:>14,.1f} {s.disclosed_at}")
        print("=" * 65)

    def _save_csv(self, candidates: list[ScreenedStock]) -> None:
        if not candidates:
            return
        run_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        path   = Path(f"buyback_history_{run_at}.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=["code", "name", "market_cap", "disclosed_at"]
            )
            writer.writeheader()
            for s in candidates:
                writer.writerow({
                    "code":         s.code,
                    "name":         s.name,
                    "market_cap":   s.market_cap,
                    "disclosed_at": s.disclosed_at,
                })
        logger.info(f"CSVを保存しました: {path}")


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="TDnet過去履歴から自社株買いを検索")
    parser.add_argument(
        "--days", type=int, default=30,
        help="遡る営業日数（デフォルト: 30営業日 ≒ 約6週間）"
    )
    args = parser.parse_args()

    screener = HistoryScreener(days=args.days)
    screener.run()


if __name__ == "__main__":
    main()
