"""
フェーズ2: 5分足RSI 底打ち検出モジュール
rsi_analyzer.py

【検出するシグナル（スコアリング方式）】
  S1: RSI が閾値（デフォルト30）を下回った          +1点
  S2: 強気ダイバージェンス（価格は安値更新、RSIは切り上げ）+2点
  S3: RSI が閾値を下から上抜け（売られすぎ脱出）      +2点
  S4: RSI の傾きが負→正に反転（局所的な底）          +1点
  合計6点満点。閾値（デフォルト3点以上）でシグナルを判定。

【データ取得】
  - yfinance と J-Quants API の両方に対応
  - DataFetcher 基底クラスを継承して差し替え可能

【フェーズ1との連携】
  BuybackScreener の ScreenedStock を受け取り、
  RSISignalResult を返すように設計してある。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("rsi_analyzer")


# ──────────────────────────────────────────────
# 定数・デフォルト設定
# ──────────────────────────────────────────────
DEFAULT_RSI_PERIOD      = 14     # RSI の計算期間（5分足）
DEFAULT_OVERSOLD        = 30     # 売られすぎ閾値
DEFAULT_SCORE_THRESHOLD = 3      # このスコア以上をシグナルとして採用
DEFAULT_LOOKBACK_BARS   = 60     # ダイバージェンス探索に使う足の数
DEFAULT_SLOPE_WINDOW    = 3      # 傾き計算に使うバー数


# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────
@dataclass
class RSISignalResult:
    """
    RSI底打ち判定の結果。
    フェーズ1の ScreenedStock と組み合わせて通知に使う。
    """
    code:            str
    name:            str
    score:           int                   # 0〜6点
    is_signal:       bool                  # score >= score_threshold
    rsi_current:     float                 # 直近RSI値
    price_current:   float                 # 直近終値
    signals_hit:     list[str] = field(default_factory=list)  # 発火したシグナル名
    detail:          dict      = field(default_factory=dict)  # 詳細情報


# ──────────────────────────────────────────────
# データ取得レイヤー（抽象基底 + 実装2種）
# ──────────────────────────────────────────────
class DataFetcher(ABC):
    """5分足OHLCVを取得する抽象基底クラス"""

    @abstractmethod
    def fetch_5min(self, code: str, bars: int = 200) -> pd.DataFrame:
        """
        5分足データを取得する。

        Args:
            code: 4桁の銘柄コード（例: "7203"）
            bars: 取得する足の本数

        Returns:
            pd.DataFrame: columns=[open, high, low, close, volume]
                          index=DatetimeTZAware（Asia/Tokyo）
        """


class YfinanceFetcher(DataFetcher):
    """
    yfinance で5分足を取得する実装。

    【制約】
    - yfinance の5分足は直近60日分のみ取得可能
    - 日本株は ".T" サフィックスが必要（例: "7203.T"）
    - 非公式ライブラリのため突然壊れることがある
    """

    def __init__(self, sleep_sec: float = 1.0):
        self.sleep_sec = sleep_sec

    def fetch_5min(self, code: str, bars: int = 200) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("pip install yfinance を実行してください")

        ticker_symbol = f"{code}.T"
        logger.info(f"[yfinance] {ticker_symbol} の5分足を取得中...")
        try:
            ticker = yf.Ticker(ticker_symbol)
            # period="60d" で最大60日分、interval="5m" で5分足
            df = ticker.history(period="60d", interval="5m")
            if df.empty:
                logger.warning(f"[yfinance] {ticker_symbol}: データが空です")
                return pd.DataFrame()
            # カラム名を小文字統一
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].copy()
            # 直近 bars 本に絞る
            return df.iloc[-bars:] if len(df) > bars else df
        except Exception as e:
            logger.error(f"[yfinance] 取得失敗 ({ticker_symbol}): {e}")
            return pd.DataFrame()
        finally:
            time.sleep(self.sleep_sec)


class JQuantsFetcher(DataFetcher):
    """
    J-Quants API で5分足を取得する実装。

    【前提】
    - J-Quants API への登録と refresh_token の取得が必要
    - pip install jquants-api-client
    - 無料プランでは当日の分足データは翌営業日以降に取得可能
    - 詳細: https://jpx-jquants.com/

    【使い方】
        fetcher = JQuantsFetcher(refresh_token="YOUR_TOKEN")
    """

    def __init__(self, refresh_token: str, sleep_sec: float = 0.5):
        self.refresh_token = refresh_token
        self.sleep_sec = sleep_sec
        self._client = None

    def _get_client(self):
        """クライアントの遅延初期化"""
        if self._client is not None:
            return self._client
        try:
            import jquantsapi
        except ImportError:
            raise ImportError("pip install jquants-api-client を実行してください")
        self._client = jquantsapi.Client(refresh_token=self.refresh_token)
        return self._client

    def fetch_5min(self, code: str, bars: int = 200) -> pd.DataFrame:
        logger.info(f"[J-Quants] {code} の5分足を取得中...")
        try:
            client = self._get_client()
            # J-Quants の分足エンドポイント（要プラン確認）
            df = client.get_prices_am(code=code)
            if df is None or df.empty:
                logger.warning(f"[J-Quants] {code}: データが空です")
                return pd.DataFrame()
            df = df.rename(columns={
                "Open": "open", "High": "high",
                "Low": "low",  "Close": "close", "Volume": "volume",
            })
            df = df[["open", "high", "low", "close", "volume"]].copy()
            return df.iloc[-bars:] if len(df) > bars else df
        except Exception as e:
            logger.error(f"[J-Quants] 取得失敗 ({code}): {e}")
            return pd.DataFrame()
        finally:
            time.sleep(self.sleep_sec)


class AutoFetcher(DataFetcher):
    """
    yfinance → J-Quants の順でフォールバックする自動選択フェッチャー。
    どちらでも動くようにしたい場合に使う。
    """

    def __init__(
        self,
        jquants_refresh_token: Optional[str] = None,
        prefer_jquants: bool = False,
    ):
        self._yf = YfinanceFetcher()
        self._jq = (
            JQuantsFetcher(jquants_refresh_token)
            if jquants_refresh_token else None
        )
        self.prefer_jquants = prefer_jquants

    def fetch_5min(self, code: str, bars: int = 200) -> pd.DataFrame:
        fetchers: list[DataFetcher] = []
        if self.prefer_jquants and self._jq:
            fetchers = [self._jq, self._yf]
        elif self._jq:
            fetchers = [self._yf, self._jq]
        else:
            fetchers = [self._yf]

        for fetcher in fetchers:
            df = fetcher.fetch_5min(code, bars)
            if not df.empty:
                return df
            logger.warning(f"フォールバック: 次のフェッチャーを試みます")
        return pd.DataFrame()


# ──────────────────────────────────────────────
# RSI計算ユーティリティ
# ──────────────────────────────────────────────
def calc_rsi(close: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> pd.Series:
    """
    Wilder平滑化法（真のRSI計算）でRSIを計算する。

    pandas-ta や ta-lib を使っても良いが、
    依存を増やさないため手実装する。

    Args:
        close: 終値のSeriesf
        period: RSI計算期間

    Returns:
        pd.Series: RSI値（0〜100）
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilderの指数移動平均（alpha = 1/period）
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def find_local_minima(series: pd.Series, order: int = 3) -> pd.Series:
    """
    直近の局所最小値のインデックスを返す。

    Args:
        series: RSIなどの時系列Series
        order:  前後 order 本より小さければ局所最小と判定

    Returns:
        pd.Series: 局所最小のインデックスが True のboolシリーズ
    """
    from scipy.signal import argrelmin
    # scipy がなければ手実装にフォールバック
    try:
        idx = argrelmin(series.values, order=order)[0]
        result = pd.Series(False, index=series.index)
        result.iloc[idx] = True
        return result
    except ImportError:
        # scipy なし: 前後 order 本との比較で判定
        result = pd.Series(False, index=series.index)
        for i in range(order, len(series) - order):
            window = series.iloc[i - order: i + order + 1]
            if series.iloc[i] == window.min():
                result.iloc[i] = True
        return result


# ──────────────────────────────────────────────
# シグナル検出ロジック（個別）
# ──────────────────────────────────────────────
def detect_s1_oversold(
    rsi: pd.Series,
    oversold: float = DEFAULT_OVERSOLD,
) -> bool:
    """
    S1: RSI が直近バーで oversold 以下に突入しているか。

    判定: 直近RSI <= oversold かつ 前バーも oversold 以下
          （一時的なノイズではなく、ゾーン滞在中であること）
    """
    if len(rsi) < 2:
        return False
    return bool(rsi.iloc[-1] <= oversold and rsi.iloc[-2] <= oversold)


def detect_s2_divergence(
    close: pd.Series,
    rsi: pd.Series,
    lookback: int = DEFAULT_LOOKBACK_BARS,
    oversold: float = DEFAULT_OVERSOLD,
    min_rsi_diff: float = 3.0,
) -> tuple[bool, dict]:
    """
    S2: 強気ダイバージェンス検出。
    価格は前回安値を更新しているが、RSIは前回安値より高い。

    Args:
        close:       終値Series
        rsi:         RSIのSeries
        lookback:    何本前まで遡って安値を探すか
        oversold:    RSIが oversold 以下の局所底のみ対象
        min_rsi_diff: RSI差の最小幅（ノイズ除去）

    Returns:
        (bool: ダイバージェンス検出, dict: 詳細情報)
    """
    detail: dict = {}
    if len(rsi) < lookback:
        return False, detail

    rsi_recent  = rsi.iloc[-lookback:]
    close_recent = close.iloc[-lookback:]

    # oversold ゾーン内での局所最小値を取得
    local_mins = find_local_minima(rsi_recent, order=3)
    min_indices = local_mins[local_mins & (rsi_recent <= oversold)].index

    if len(min_indices) < 2:
        return False, detail

    # 最後の2つの安値を比較
    prev_idx = min_indices[-2]
    last_idx = min_indices[-1]

    rsi_prev  = rsi_recent[prev_idx]
    rsi_last  = rsi_recent[last_idx]
    price_prev = close_recent[prev_idx]
    price_last = close_recent[last_idx]

    # 条件: 価格は安値更新、RSIは切り上げ、かつ差が min_rsi_diff 以上
    price_lower = price_last < price_prev
    rsi_higher  = rsi_last > rsi_prev + min_rsi_diff

    detail = {
        "prev_rsi": round(rsi_prev, 2),
        "last_rsi": round(rsi_last, 2),
        "prev_price": round(price_prev, 1),
        "last_price": round(price_last, 1),
    }

    return bool(price_lower and rsi_higher), detail


def detect_s3_cross_up(
    rsi: pd.Series,
    oversold: float = DEFAULT_OVERSOLD,
    lookback: int = 5,
) -> bool:
    """
    S3: RSI が oversold ラインを下から上抜けしたか。
    直近 lookback 本の中で「oversold 以下 → 以上」の交差を検出。

    「売られすぎゾーンからの脱出」が確認できる最も信頼性の高いシグナル。
    """
    if len(rsi) < lookback + 1:
        return False

    window = rsi.iloc[-(lookback + 1):]
    # ウィンドウ内に「oversold以下の値」と「oversold以上の値」の両方があり、
    # かつ直近バーが oversold 以上であれば上抜け
    has_below = (window.iloc[:-1] <= oversold).any()
    current_above = window.iloc[-1] > oversold
    return bool(has_below and current_above)


def detect_s4_slope_reversal(
    rsi: pd.Series,
    window: int = DEFAULT_SLOPE_WINDOW,
    oversold: float = DEFAULT_OVERSOLD,
) -> tuple[bool, float]:
    """
    S4: RSIの傾きが負から正に反転したか（局所最小値の直後）。

    計算方法:
      - 直近 window 本の RSI で線形回帰の傾きを計算
      - さらに window 本前の傾きと比較して「負→正」を確認

    Args:
        rsi:     RSIのSeries
        window:  傾き計算に使うバー数
        oversold: RSIが oversold 以下の時のみ有効とする

    Returns:
        (bool: 傾き反転検出, float: 現在の傾き)
    """
    if len(rsi) < window * 2 + 1:
        return False, 0.0

    def slope(series_slice: pd.Series) -> float:
        """線形回帰の傾きを計算"""
        x = np.arange(len(series_slice), dtype=float)
        y = series_slice.values.astype(float)
        if np.isnan(y).any():
            return 0.0
        coeffs = np.polyfit(x, y, 1)
        return float(coeffs[0])

    current_slope = slope(rsi.iloc[-window:])
    prev_slope    = slope(rsi.iloc[-(window * 2):-window])

    # 傾きが負→正に転じ、かつ RSI が oversold 近辺にある場合のみ有効
    rsi_in_zone = rsi.iloc[-1] <= oversold + 10  # 少し余裕を持たせる
    reversal = prev_slope < 0 and current_slope > 0 and rsi_in_zone

    return bool(reversal), round(current_slope, 4)


# ──────────────────────────────────────────────
# メインアナライザークラス
# ──────────────────────────────────────────────
@dataclass
class RSIConfig:
    """RSIアナライザーの設定値（デフォルトで動作、必要に応じて調整）"""
    rsi_period:      int   = DEFAULT_RSI_PERIOD
    oversold:        float = DEFAULT_OVERSOLD
    score_threshold: int   = DEFAULT_SCORE_THRESHOLD
    lookback_bars:   int   = DEFAULT_LOOKBACK_BARS
    slope_window:    int   = DEFAULT_SLOPE_WINDOW
    fetch_bars:      int   = 200   # 取得する足の本数


class RSIAnalyzer:
    """
    5分足RSI底打ち検出クラス。

    【使い方】
        fetcher  = AutoFetcher()   # yfinance / J-Quants 自動切替
        analyzer = RSIAnalyzer(fetcher)
        result   = analyzer.analyze("7203", name="トヨタ自動車")
        if result.is_signal:
            print(f"シグナル発生！ スコア: {result.score} / {result.signals_hit}")

    【フェーズ1との統合例】
        screener = BuybackScreener()
        candidates = screener.run()           # ScreenedStock のリスト
        for stock in candidates:
            result = analyzer.analyze(stock.code, stock.name)
            if result.is_signal:
                notifier.send(result)
    """

    # スコア配点
    SCORE_MAP = {
        "S1_oversold":       1,
        "S2_divergence":     2,
        "S3_cross_up":       2,
        "S4_slope_reversal": 1,
    }

    def __init__(
        self,
        fetcher: DataFetcher = None,
        config: RSIConfig = None,
    ):
        self.fetcher = fetcher or AutoFetcher()
        self.config  = config  or RSIConfig()

    def analyze(self, code: str, name: str = "") -> RSISignalResult:
        """
        銘柄コードを受け取り、RSI底打ちシグナルを判定して返す。

        Args:
            code: 4桁の銘柄コード
            name: 銘柄名（ログ・通知用）

        Returns:
            RSISignalResult
        """
        cfg = self.config

        # ── データ取得 ──
        df = self.fetcher.fetch_5min(code, bars=cfg.fetch_bars)
        if df.empty or "close" not in df.columns:
            logger.warning(f"[{code}] データ取得失敗: スキップ")
            return RSISignalResult(
                code=code, name=name, score=0,
                is_signal=False, rsi_current=float("nan"),
                price_current=float("nan"),
                signals_hit=["DATA_ERROR"],
            )

        close = df["close"].dropna()
        if len(close) < cfg.rsi_period + cfg.slope_window * 2 + 1:
            logger.warning(f"[{code}] データ不足: {len(close)}本")
            return RSISignalResult(
                code=code, name=name, score=0,
                is_signal=False, rsi_current=float("nan"),
                price_current=float(close.iloc[-1]) if len(close) > 0 else float("nan"),
                signals_hit=["INSUFFICIENT_DATA"],
            )

        # ── RSI計算 ──
        rsi = calc_rsi(close, period=cfg.rsi_period)
        rsi_current   = float(rsi.iloc[-1])
        price_current = float(close.iloc[-1])

        logger.info(
            f"[{code}] {name} | 直近RSI: {rsi_current:.1f} | 株価: {price_current:.1f}"
        )

        # ── 各シグナル判定 ──
        score        = 0
        signals_hit  = []
        detail: dict = {"rsi_series_tail": rsi.iloc[-5:].round(2).tolist()}

        # S1: 売られすぎゾーン突入
        if detect_s1_oversold(rsi, oversold=cfg.oversold):
            score += self.SCORE_MAP["S1_oversold"]
            signals_hit.append("S1_oversold")
            logger.info(f"  [S1] 売られすぎゾーン検出 (RSI={rsi_current:.1f})")

        # S2: 強気ダイバージェンス
        div_detected, div_detail = detect_s2_divergence(
            close, rsi,
            lookback=cfg.lookback_bars,
            oversold=cfg.oversold,
        )
        if div_detected:
            score += self.SCORE_MAP["S2_divergence"]
            signals_hit.append("S2_divergence")
            detail["divergence"] = div_detail
            logger.info(f"  [S2] ダイバージェンス検出: {div_detail}")

        # S3: RSI 30ライン上抜け
        if detect_s3_cross_up(rsi, oversold=cfg.oversold):
            score += self.SCORE_MAP["S3_cross_up"]
            signals_hit.append("S3_cross_up")
            logger.info(f"  [S3] RSI{cfg.oversold}ライン上抜け検出")

        # S4: 傾き反転
        slope_reversed, current_slope = detect_s4_slope_reversal(
            rsi,
            window=cfg.slope_window,
            oversold=cfg.oversold,
        )
        if slope_reversed:
            score += self.SCORE_MAP["S4_slope_reversal"]
            signals_hit.append("S4_slope_reversal")
            detail["slope"] = current_slope
            logger.info(f"  [S4] RSI傾き反転検出 (傾き={current_slope:.4f})")

        is_signal = score >= cfg.score_threshold

        result = RSISignalResult(
            code=code,
            name=name,
            score=score,
            is_signal=is_signal,
            rsi_current=round(rsi_current, 2),
            price_current=round(price_current, 1),
            signals_hit=signals_hit,
            detail=detail,
        )

        if is_signal:
            logger.info(
                f"  => シグナル発生！ スコア: {score}点 / {signals_hit}"
            )
        else:
            logger.info(f"  => シグナルなし (スコア: {score}点)")

        return result

    def analyze_batch(
        self,
        stocks: list[dict],  # [{"code": "7203", "name": "トヨタ"}]
        sleep_between: float = 1.5,
    ) -> list[RSISignalResult]:
        """
        複数銘柄をまとめて分析する（フェーズ1のスクリーニング結果に使う）。

        Args:
            stocks: {"code": ..., "name": ...} の辞書リスト
            sleep_between: リクエスト間のスリープ秒数

        Returns:
            RSISignalResult のリスト（シグナルあり銘柄が先頭）
        """
        results = []
        for s in stocks:
            result = self.analyze(s.get("code", ""), s.get("name", ""))
            results.append(result)
            time.sleep(sleep_between)

        # スコアの高い順にソート
        results.sort(key=lambda r: r.score, reverse=True)
        return results


# ──────────────────────────────────────────────
# フェーズ1との統合ヘルパー
# ──────────────────────────────────────────────
def run_phase2(screened_stocks, fetcher: DataFetcher = None) -> list[RSISignalResult]:
    """
    フェーズ1の BuybackScreener.run() の結果を受け取り、
    RSIアナライザーを走らせるヘルパー関数。

    使用例:
        from buyback_screener import BuybackScreener
        from rsi_analyzer import run_phase2

        screener = BuybackScreener()
        candidates = screener.run()          # ScreenedStock のリスト
        signals    = run_phase2(candidates)  # RSISignalResult のリスト

        for s in signals:
            if s.is_signal:
                print(f"{s.code} {s.name}: {s.score}点 {s.signals_hit}")
    """
    analyzer = RSIAnalyzer(fetcher=fetcher or AutoFetcher())

    stocks = [{"code": s.code, "name": s.name} for s in screened_stocks]
    results = analyzer.analyze_batch(stocks)

    signal_stocks = [r for r in results if r.is_signal]
    logger.info(
        f"フェーズ2 完了: {len(signal_stocks)}/{len(results)}銘柄がRSIシグナルを発生"
    )
    return results


# ──────────────────────────────────────────────
# 単体実行用エントリポイント
# ──────────────────────────────────────────────
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # テスト用: トヨタ(7203)とソニー(6758)を直接指定して検証
    test_stocks = [
        {"code": "7203", "name": "トヨタ自動車"},
        {"code": "6758", "name": "ソニーグループ"},
    ]

    # J-Quants トークンがある場合は以下を有効化:
    # fetcher = AutoFetcher(jquants_refresh_token="YOUR_REFRESH_TOKEN")
    fetcher = AutoFetcher()

    config = RSIConfig(
        rsi_period=14,
        oversold=30,
        score_threshold=3,   # 3点以上でシグナル採用
    )
    analyzer = RSIAnalyzer(fetcher=fetcher, config=config)
    results  = analyzer.analyze_batch(test_stocks)

    print("\n" + "=" * 60)
    print("【RSI底打ちスクリーニング結果】")
    print("=" * 60)
    for r in results:
        status = "<<シグナル>>" if r.is_signal else "---"
        print(
            f"{status} [{r.code}] {r.name} | "
            f"RSI:{r.rsi_current:.1f} | "
            f"株価:{r.price_current:.1f} | "
            f"スコア:{r.score}点 | "
            f"{r.signals_hit}"
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
