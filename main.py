<<<<<<< HEAD
import logging
from buyback_screener import BuybackScreener
from rsi_analyzer import RSIAnalyzer, AutoFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

print("===== 自社株買いスクリーナー 起動 =====")

# フェーズ1：TDnetから自社株買い銘柄を検知
print("\n【フェーズ1】TDnetスクリーニング中...")
screener = BuybackScreener()
stocks = screener.run()

if not stocks:
    print("本日の対象銘柄はありませんでした。")
else:
    # フェーズ2：RSI底打ちチェック
    print(f"\n【フェーズ2】{len(stocks)}銘柄のRSIを分析中...")
    analyzer = RSIAnalyzer(AutoFetcher())
    results = analyzer.analyze_batch(
        [{"code": s.code, "name": s.name} for s in stocks]
    )

    # 結果表示
    print("\n===== 最終結果 =====")
    signal_found = False
    for r in results:
        if r.is_signal:
            signal_found = True
            print(f"【シグナル】{r.code} {r.name}")
            print(f"  RSI: {r.rsi_current}  株価: {r.price_current}円")
            print(f"  スコア: {r.score}点  シグナル: {r.signals_hit}")
            print()

    if not signal_found:
        print("RSIシグナルが出た銘柄はありませんでした。")

=======
import logging
from buyback_screener import BuybackScreener
from rsi_analyzer import RSIAnalyzer, AutoFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

print("===== 自社株買いスクリーナー 起動 =====")

# フェーズ1：TDnetから自社株買い銘柄を検知
print("\n【フェーズ1】TDnetスクリーニング中...")
screener = BuybackScreener()
stocks = screener.run()

if not stocks:
    print("本日の対象銘柄はありませんでした。")
else:
    # フェーズ2：RSI底打ちチェック
    print(f"\n【フェーズ2】{len(stocks)}銘柄のRSIを分析中...")
    analyzer = RSIAnalyzer(AutoFetcher())
    results = analyzer.analyze_batch(
        [{"code": s.code, "name": s.name} for s in stocks]
    )

    # 結果表示
    print("\n===== 最終結果 =====")
    signal_found = False
    for r in results:
        if r.is_signal:
            signal_found = True
            print(f"【シグナル】{r.code} {r.name}")
            print(f"  RSI: {r.rsi_current}  株価: {r.price_current}円")
            print(f"  スコア: {r.score}点  シグナル: {r.signals_hit}")
            print()

    if not signal_found:
        print("RSIシグナルが出た銘柄はありませんでした。")

>>>>>>> ad2630cf30b591b581aa12460d6e9649adbbd2f4
print("===== 終了 =====")