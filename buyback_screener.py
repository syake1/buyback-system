"""
自社株買い・遅延反発狙いトレードシステム
フェーズ1: TDnet適時開示の検知とスクリーニング

【処理フロー】
1. TDnetから適時開示の最新一覧を取得
2. 「自己株式取得」を含む開示を抽出
3. PDFをダウンロードし「ToSTNeT」「立会外」の文字列を検索 → 該当銘柄を除外
4. yfinanceで時価総額を取得 → 1000億円未満を除外
5. スクリーニング通過銘柄をCSVに出力

【設計方針】
- フェーズ2（テクニカル分析）・フェーズ3（自動発注）への拡張を想定したクラスベース設計
- 各モジュールは疎結合 → 差し替え・拡張が容易
"""

import time
import logging
import re
import io
import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
import pdfplumber
import yfinance as yf

# ──────────────────────────────────────────────
# ロギング設定
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("buyback_screener")


# ──────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────
TDNET_BASE_URL = "https://www.release.tdnet.info"
TDNET_LIST_URL = f"{TDNET_BASE_URL}/inbs/I_main_00.html"

# 市場外買付を示すキーワード（いずれか1つでも含めば除外）
MARKET_OUTSIDE_KEYWORDS = ["ToSTNeT", "立会外", "ToSTNet", "TOSTNET"]

# 時価総額の閾値（円）: 1000億円
MARKET_CAP_THRESHOLD = 100_000_000_000  # 100 billion JPY

# yfinanceのレート制限対策: リクエスト間隔（秒）
YFINANCE_SLEEP_SEC = 1.5
TDNET_SLEEP_SEC = 1.0

# 出力CSVのデフォルトパス
OUTPUT_CSV = Path("buyback_candidates.csv")


# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────
@dataclass
class Disclosure:
    """TDnetから取得した適時開示の1件分のデータ"""
    code: str                    # 銘柄コード（例: "7203"）
    name: str                    # 銘柄名（例: "トヨタ自動車"）
    title: str                   # 開示タイトル
    pdf_url: str                 # PDFのURL
    disclosed_at: str            # 開示日時（文字列）


@dataclass
class ScreenedStock:
    """スクリーニングを通過した銘柄の情報（フェーズ2・3で拡張予定）"""
    code: str
    name: str
    market_cap: float            # 時価総額（円）
    market_cap_unit: str = "JPY"
    disclosed_at: str = ""
    # フェーズ2以降で追加予定のフィールド（プレースホルダー）
    # rsi_5min: Optional[float] = None
    # gap_filled: Optional[bool] = None
    extra: dict = field(default_factory=dict)


# ──────────────────────────────────────────────
# モジュール1: TDnet取得クラス
# ──────────────────────────────────────────────
class TDnetFetcher:
    """
    TDnet（東証適時開示情報閲覧サービス）から開示情報を取得する。

    【注意】
    - TDnetは商用利用・大量アクセスを禁止している。
    - 必ずsleepを挟み、User-Agentを適切に設定すること。
    - 本番運用時はキャッシュやスケジューリング（例: 15:30以降に1回）を検討すること。
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BuybackScreener/1.0; "
            "+https://example.com/bot)"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }

    def __init__(self, keyword: str = "自己株式取得", sleep_sec: float = TDNET_SLEEP_SEC):
        self.keyword = keyword
        self.sleep_sec = sleep_sec
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def fetch_disclosures(self) -> list[Disclosure]:
        """
        TDnetの当日開示一覧を取得し、keywordに合致する開示を返す。

        Returns:
            List[Disclosure]: キーワードに合致した開示のリスト
        """
        logger.info(f"TDnetから開示情報を取得中... キーワード: '{self.keyword}'")

        try:
            resp = self.session.get(TDNET_LIST_URL, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"TDnet取得失敗: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        disclosures = []

        # TDnetのHTMLテーブルから開示情報を解析
        # ※ TDnetのHTML構造が変更された場合はここを修正する
        for row in soup.select("table#main-list-table tr"):
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            title_tag = cols[2].find("a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)

            # キーワードフィルタ
            if self.keyword not in title:
                continue

            # 銘柄コード・銘柄名・PDF URLを抽出
            code = cols[1].get_text(strip=True).zfill(4)  # 4桁ゼロ埋め
            name = cols[0].get_text(strip=True)
            disclosed_at = cols[3].get_text(strip=True)

            # PDFリンクを取得（相対URLを絶対URLに変換）
            pdf_href = title_tag.get("href", "")
            if not pdf_href:
                continue
            pdf_url = (
                pdf_href if pdf_href.startswith("http")
                else f"{TDNET_BASE_URL}{pdf_href}"
            )

            disc = Disclosure(
                code=code,
                name=name,
                title=title,
                pdf_url=pdf_url,
                disclosed_at=disclosed_at,
            )
            disclosures.append(disc)
            logger.info(f"  発見: [{code}] {name} | {title}")

            time.sleep(self.sleep_sec)

        logger.info(f"取得完了: {len(disclosures)}件")
        return disclosures

    def download_pdf(self, pdf_url: str) -> Optional[bytes]:
        """
        PDFをバイト列としてダウンロードする。

        Args:
            pdf_url: PDFのURL

        Returns:
            bytes: PDFのバイト列、失敗時はNone
        """
        try:
            resp = self.session.get(pdf_url, timeout=20)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as e:
            logger.warning(f"PDFダウンロード失敗 ({pdf_url}): {e}")
            return None


# ──────────────────────────────────────────────
# モジュール2: PDF解析クラス
# ──────────────────────────────────────────────
class PDFAnalyzer:
    """
    PDFを解析し、市場外買付（ToSTNeT・立会外）の記述を検出する。

    【設計メモ】
    - pdfplumberを使用（表・レイアウト保持に強い）
    - PyPDF2へのフォールバックも実装済み（pdfplumber失敗時）
    """

    def __init__(self, exclude_keywords: list[str] = None):
        self.exclude_keywords = exclude_keywords or MARKET_OUTSIDE_KEYWORDS

    def extract_text(self, pdf_bytes: bytes) -> str:
        """
        PDFバイト列からテキストを抽出する。

        Args:
            pdf_bytes: PDFのバイト列

        Returns:
            str: 抽出されたテキスト全文
        """
        text = ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    text += page_text + "\n"
        except Exception as e:
            logger.warning(f"pdfplumber解析失敗: {e} → PyPDF2でリトライ")
            text = self._extract_with_pypdf2(pdf_bytes)
        return text

    def _extract_with_pypdf2(self, pdf_bytes: bytes) -> str:
        """PyPDF2によるフォールバックテキスト抽出"""
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            return "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except Exception as e:
            logger.error(f"PyPDF2でも抽出失敗: {e}")
            return ""

    def is_market_outside(self, pdf_text: str) -> bool:
        """
        PDFテキストに市場外買付キーワードが含まれるか判定する。

        Args:
            pdf_text: PDFから抽出したテキスト

        Returns:
            bool: True = 市場外買付 → 除外対象
        """
        for kw in self.exclude_keywords:
            if kw in pdf_text:
                logger.info(f"  除外キーワード検出: '{kw}'")
                return True
        return False


# ──────────────────────────────────────────────
# モジュール3: 時価総額取得クラス
# ──────────────────────────────────────────────
class MarketCapFetcher:
    """
    yfinanceを使って日本株の時価総額を取得する。

    【日本株コードの扱い】
    - yfinanceでは日本株コードに ".T" を付与する（例: "7203.T"）
    - 時価総額は米ドル建てで返ってくる場合があるため、JPY換算が必要
    - info['marketCap'] は通常 USD建て → USD/JPY レートで換算
    - ただし yfinance は不安定なため、本番ではJQuants API等も検討すること
    """

    def __init__(self, threshold: float = MARKET_CAP_THRESHOLD, sleep_sec: float = YFINANCE_SLEEP_SEC):
        self.threshold = threshold
        self.sleep_sec = sleep_sec
        self._usd_jpy: Optional[float] = None  # USD/JPY レートのキャッシュ

    def _get_usd_jpy(self) -> float:
        """USD/JPY 為替レートを取得してキャッシュする"""
        if self._usd_jpy is not None:
            return self._usd_jpy
        try:
            ticker = yf.Ticker("USDJPY=X")
            hist = ticker.history(period="1d")
            if not hist.empty:
                self._usd_jpy = float(hist["Close"].iloc[-1])
                logger.info(f"USD/JPY レート: {self._usd_jpy:.2f}")
                return self._usd_jpy
        except Exception as e:
            logger.warning(f"USD/JPY取得失敗: {e} → デフォルト150円を使用")
        self._usd_jpy = 150.0
        return self._usd_jpy

    def get_market_cap_jpy(self, code: str) -> Optional[float]:
        """
        銘柄コードから時価総額（円）を取得する。

        Args:
            code: 4桁の銘柄コード（例: "7203"）

        Returns:
            float: 時価総額（円）、取得失敗時は None
        """
        ticker_symbol = f"{code}.T"
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info

            market_cap = info.get("marketCap")
            if market_cap is None:
                logger.warning(f"  [{code}] marketCapが取得できませんでした")
                return None

            # yfinanceの marketCap は通常 USD建て
            # currency フィールドで通貨を確認する
            currency = info.get("currency", "USD")
            if currency == "JPY":
                # 既にJPY建て（稀なケース）
                return float(market_cap)
            else:
                # USD建て → JPY換算
                usd_jpy = self._get_usd_jpy()
                market_cap_jpy = float(market_cap) * usd_jpy
                return market_cap_jpy

        except Exception as e:
            logger.warning(f"  [{code}] yfinance取得エラー: {e}")
            return None
        finally:
            time.sleep(self.sleep_sec)  # レート制限対策

    def passes_threshold(self, code: str) -> tuple[bool, float]:
        """
        時価総額が閾値以上かどうかを判定する。

        Args:
            code: 4桁の銘柄コード

        Returns:
            (bool: 通過したか, float: 時価総額[円])
        """
        cap = self.get_market_cap_jpy(code)
        if cap is None:
            return False, 0.0
        passed = cap >= self.threshold
        return passed, cap


# ──────────────────────────────────────────────
# メインスクリーナークラス（統合・拡張ポイント）
# ──────────────────────────────────────────────
class BuybackScreener:
    """
    自社株買いスクリーナーのメインクラス。

    各モジュールを統合し、フェーズ1のスクリーニングパイプラインを実行する。
    フェーズ2（TechnicalAnalyzer）やフェーズ3（OrderManager）は
    このクラスに追加のメソッド・依存として注入する設計とする。

    【拡張例（フェーズ2）】
        screener = BuybackScreener()
        screener.technical_analyzer = TechnicalAnalyzer()  # 後から注入可能
        results = screener.run()
        for stock in results:
            screener.technical_analyzer.analyze(stock)

    【拡張例（フェーズ3）】
        screener.order_manager = OrderManager(api_key="...")
        screener.order_manager.submit(stock)
    """

    def __init__(
        self,
        tdnet_fetcher: TDnetFetcher = None,
        pdf_analyzer: PDFAnalyzer = None,
        market_cap_fetcher: MarketCapFetcher = None,
        output_csv: Path = OUTPUT_CSV,
    ):
        # 依存性注入（DI）パターン: テスト時にモックを差し込める
        self.tdnet_fetcher = tdnet_fetcher or TDnetFetcher()
        self.pdf_analyzer = pdf_analyzer or PDFAnalyzer()
        self.market_cap_fetcher = market_cap_fetcher or MarketCapFetcher()
        self.output_csv = output_csv

    def run(self) -> list[ScreenedStock]:
        """
        フェーズ1のメインパイプラインを実行する。

        Returns:
            List[ScreenedStock]: スクリーニング通過銘柄のリスト
        """
        logger.info("=" * 60)
        logger.info("自社株買いスクリーナー フェーズ1 開始")
        logger.info("=" * 60)

        # ── STEP 1 & 2: TDnetから自己株式取得の開示を取得 ──
        disclosures = self.tdnet_fetcher.fetch_disclosures()

        if not disclosures:
            logger.warning("該当する開示が見つかりませんでした。")
            return []

        candidates: list[ScreenedStock] = []

        for disc in disclosures:
            logger.info(f"\n--- [{disc.code}] {disc.name} を処理中 ---")

            # ── STEP 3: PDFダウンロード & 市場外買付チェック ──
            pdf_bytes = self.tdnet_fetcher.download_pdf(disc.pdf_url)
            if pdf_bytes is None:
                logger.warning(f"  PDFをスキップ: {disc.pdf_url}")
                continue

            pdf_text = self.pdf_analyzer.extract_text(pdf_bytes)
            if self.pdf_analyzer.is_market_outside(pdf_text):
                logger.info(f"  → 除外（市場外買付）: [{disc.code}] {disc.name}")
                continue

            # ── STEP 4: 時価総額チェック ──
            passed, cap = self.market_cap_fetcher.passes_threshold(disc.code)
            cap_oku = cap / 1_000_000_00  # 億円表示
            logger.info(f"  時価総額: {cap_oku:,.1f}億円")

            if not passed:
                logger.info(
                    f"  → 除外（時価総額不足 < 1000億円）: [{disc.code}] {disc.name}"
                )
                continue

            # ── STEP 5: 候補リストに追加 ──
            stock = ScreenedStock(
                code=disc.code,
                name=disc.name,
                market_cap=cap,
                disclosed_at=disc.disclosed_at,
            )
            candidates.append(stock)
            logger.info(f"  ✅ スクリーニング通過: [{disc.code}] {disc.name} ({cap_oku:,.1f}億円)")

        # ── 結果の出力 ──
        self._print_results(candidates)
        self._save_csv(candidates)

        logger.info("\n" + "=" * 60)
        logger.info(f"フェーズ1 完了: {len(candidates)}銘柄がスクリーニングを通過")
        logger.info("=" * 60)

        return candidates

    def _print_results(self, candidates: list[ScreenedStock]) -> None:
        """結果をコンソールに表形式で出力する"""
        if not candidates:
            print("\n【結果】スクリーニングを通過した銘柄はありませんでした。")
            return

        print("\n" + "=" * 60)
        print("【スクリーニング通過銘柄】")
        print("=" * 60)
        print(f"{'銘柄コード':<10} {'銘柄名':<20} {'時価総額（億円）':>15} {'開示日時'}")
        print("-" * 60)
        for s in candidates:
            cap_oku = s.market_cap / 1_000_000_00
            print(f"{s.code:<10} {s.name:<20} {cap_oku:>15,.1f} {s.disclosed_at}")
        print("=" * 60)

    def _save_csv(self, candidates: list[ScreenedStock]) -> None:
        """スクリーニング通過銘柄をCSVに保存する"""
        if not candidates:
            return

        fieldnames = ["code", "name", "market_cap", "market_cap_unit", "disclosed_at"]
        run_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_csv.parent / f"{self.output_csv.stem}_{run_at}.csv"

        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for s in candidates:
                row = asdict(s)
                row.pop("extra", None)  # 内部フィールドは除く
                writer.writerow({k: row[k] for k in fieldnames})

        logger.info(f"CSVを保存しました: {output_path}")


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────
def main():
    """
    フェーズ1 実行エントリポイント。

    カスタマイズしたい場合は各クラスをインスタンス化してBuybackScreenerに渡す:
        fetcher = TDnetFetcher(keyword="自己株式取得", sleep_sec=2.0)
        screener = BuybackScreener(tdnet_fetcher=fetcher)
        screener.run()
    """
    screener = BuybackScreener()
    screener.run()


if __name__ == "__main__":
    main()
