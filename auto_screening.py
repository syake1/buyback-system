"""
auto_screening.py
GitHub Actionsから毎日自動実行されるスクリーニングスクリプト。
結果を data/latest.csv と data/history.csv に保存する。
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("auto_screening")

# data フォルダを作成
Path("data").mkdir(exist_ok=True)


def run():
    from buyback_screener import BuybackScreener
    from tdnet_history import HistoryScreener

    logger.info("===== 自動スクリーニング開始 =====")
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 過去30営業日を遡ってスクリーニング
    screener = HistoryScreener(days=30)
    stocks = screener.run()

    if not stocks:
        logger.info("本日の対象銘柄はありませんでした。")
        # 空のCSVを保存（Streamlitが読み込めるように）
        with open("data/latest.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["code", "name", "market_cap", "disclosed_at", "screened_at"])
        return

    # data/latest.csv に最新結果を保存（Streamlitが読み込む）
    with open("data/latest.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["code", "name", "market_cap", "disclosed_at", "screened_at"]
        )
        writer.writeheader()
        for s in stocks:
            writer.writerow({
                "code":         s.code,
                "name":         s.name,
                "market_cap":   s.market_cap,
                "disclosed_at": s.disclosed_at,
                "screened_at":  today,
            })

    # data/history.csv に蓄積（過去の記録を残す）
    history_path = Path("data/history.csv")
    existing_codes = set()

    if history_path.exists():
        with open(history_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_codes.add(row.get("code", ""))

    with open(history_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["code", "name", "market_cap", "disclosed_at", "screened_at"]
        )
        if not history_path.stat().st_size:
            writer.writeheader()
        for s in stocks:
            if s.code not in existing_codes:
                writer.writerow({
                    "code":         s.code,
                    "name":         s.name,
                    "market_cap":   s.market_cap,
                    "disclosed_at": s.disclosed_at,
                    "screened_at":  today,
                })

    logger.info(f"===== 完了: {len(stocks)}銘柄を保存 =====")


if __name__ == "__main__":
    run()
