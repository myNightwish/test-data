import pandas as pd
import numpy as np
import traceback
import re
import os
from professional_strategy import ProfessionalStrategyEngine
from html_generator import generate_pro_html_dashboard
from quant_engine import run_daily_research
from signal_notifier import send_daily_report
# 引入上面写好的数据抓取函数
# from your_data_module import get_stock_comprehensive_data 
import yfinance as yf


def _flatten_yfinance_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance single-ticker MultiIndex columns to plain field names."""
    frame = data.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        field_names = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        flattened = []
        for column in frame.columns:
            parts = [str(part) for part in column if str(part) != "None"]
            field = next((part for part in parts if part in field_names), parts[0])
            flattened.append(field)
        frame.columns = flattened
        frame = frame.loc[:, ~frame.columns.duplicated(keep="first")]
    return frame


def _numeric_scalar(value, default=0.0):
    """Convert numpy/list-like scalar responses from finance APIs safely."""
    while isinstance(value, (list, tuple)):
        value = value[0] if value else default
    if isinstance(value, pd.Series):
        value = value.iloc[0] if not value.empty else default
    if isinstance(value, pd.DataFrame):
        value = value.iloc[0, 0] if not value.empty else default
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except ValueError:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_statement_value(statement: pd.DataFrame, row_names: list[str], default=0):
    """Return the newest available statement value for one of several row aliases."""
    if statement is None or statement.empty:
        return default
    for row_name in row_names:
        if row_name in statement.index:
            values = statement.loc[row_name].dropna()
            if not values.empty:
                return _numeric_scalar(values.iloc[0], default)
    return default


def _statement_history(statement: pd.DataFrame, row_names: list[str], periods: int = 5):
    if statement is None or statement.empty:
        return []
    for row_name in row_names:
        if row_name in statement.index:
            values = statement.loc[row_name].dropna().iloc[:periods]
            return [
                {"period": str(period.date()) if hasattr(period, "date") else str(period),
                 "value": _numeric_scalar(value)}
                for period, value in values.items()
            ]
    return []


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """使用 Wilder 平滑，和主流行情平台的 RSI 口径一致。"""
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask(avg_loss.eq(0) & avg_gain.gt(0), 100)
    rsi = rsi.mask(avg_gain.eq(0) & avg_loss.gt(0), 0)
    return rsi


def _split_adjust_ohlcv(data: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """只按股票拆分统一历史价格，不做股息复权，保持当前盘口价不变。"""
    frame = data.copy()
    if actions is None or actions.empty or "Stock Splits" not in actions:
        return frame
    frame_dates = pd.to_datetime(frame["date"]).dt.tz_localize(None)
    split_events = actions[actions["Stock Splits"].fillna(0) != 0]
    for event_date, event in split_events["Stock Splits"].items():
        ratio = _numeric_scalar(event, default=0)
        if ratio <= 0 or ratio == 1:
            continue
        event_date = pd.Timestamp(event_date).tz_localize(None)
        before_event = frame_dates < event_date
        for column in ("open", "high", "low", "close"):
            if column in frame:
                frame.loc[before_event, column] = frame.loc[before_event, column] / ratio
        if "volume" in frame:
            frame["volume"] = frame["volume"].astype(float)
            frame.loc[before_event, "volume"] = frame.loc[before_event, "volume"] * ratio
    return frame


def _statement_period(statement: pd.DataFrame):
    if statement is not None and not statement.empty and len(statement.columns):
        period = statement.columns[0]
        return str(period.date()) if hasattr(period, "date") else str(period)
    return "N/A"


def _fundamental_assessment(fundamentals: dict):
    financials = fundamentals.get("financials", {})
    rules = []
    revenue_growth = fundamentals.get("revenue_growth")
    rules.append({
        "name": "营收同比增长",
        "passed": isinstance(revenue_growth, (int, float)) and revenue_growth > 0,
        "threshold": "> 0%",
        "value": revenue_growth,
    })
    rules.append({
        "name": "营业利润为正",
        "passed": financials.get("operating_income", 0) > 0,
        "threshold": "> $0",
        "value": financials.get("operating_income"),
    })
    rules.append({
        "name": "自由现金流为正",
        "passed": financials.get("free_cash_flow", 0) > 0,
        "threshold": "> $0",
        "value": financials.get("free_cash_flow"),
    })
    rules.append({
        "name": "现金覆盖债务",
        "passed": financials.get("net_debt", 0) <= 0,
        "threshold": "净负债 <= $0",
        "value": financials.get("net_debt"),
    })
    fcf_conversion = financials.get("fcf_conversion")
    rules.append({
        "name": "现金流转化健康",
        "passed": isinstance(fcf_conversion, (int, float)) and fcf_conversion >= 0.8,
        "threshold": "自由现金流/净利润 >= 80%",
        "value": fcf_conversion,
    })
    score = sum(rule["passed"] for rule in rules)
    return {
        "score": score,
        "max_score": 5,
        "rating": "强" if score >= 4 else "中" if score >= 2 else "弱",
        "rules": rules,
    }


def _latest_state(history: list, metric: str, higher_is_better: bool = True):
    ordered = sorted(history, key=lambda item: item["period"])
    if len(ordered) < 2:
        return {"metric": metric, "state": "数据不足", "change_pct": None}
    previous, current = ordered[-2]["value"], ordered[-1]["value"]
    change_pct = ((current / previous) - 1) * 100 if previous else None
    improved = current > previous if higher_is_better else current < previous
    return {
        "metric": metric,
        "state": "改善" if improved else "恶化" if current != previous else "持平",
        "current": current,
        "previous": previous,
        "change_pct": round(change_pct, 1) if change_pct is not None else None,
        "observations": len(ordered),
    }


def _ratio_history(numerator_history: list, denominator_history: list):
    denominator_by_period = {item["period"]: item["value"] for item in denominator_history}
    return [
        {"period": item["period"], "value": item["value"] / denominator_by_period[item["period"]]}
        for item in numerator_history
        if denominator_by_period.get(item["period"])
    ]


def _classify_asset(info: dict, beta, strategy_profile: str | None = None):
    sector = str(info.get("sector", "") or "")
    industry = str(info.get("industry", "") or "")
    text = f"{sector} {industry}".lower()
    keyword_map = (
        (("semiconductor", "半导体"), "半导体"),
        (("memory", "存储"), "存储"),
        (("software", "软件"), "软件"),
        (("auto", "汽车"), "汽车"),
        (("internet", "互联网"), "互联网"),
    )
    subtype = next(
        (label for keywords, label in keyword_map if any(keyword in text for keyword in keywords)),
        industry or "其他",
    )
    if not sector and not industry:
        subtype = "指数/ETF"
    high_beta = strategy_profile == "high_beta"
    return {
        "sector": sector or "未分类",
        "industry": industry or "未分类",
        "category": f"成长科技-{subtype}" if sector in {"Technology", "科技"} else f"{sector or '其他'}-{subtype}",
        "volatility_level": "高波动" if high_beta else "常规波动",
        "volatility_basis": (
            f"beta={beta:.2f}" if isinstance(beta, (int, float))
            else "strategy_profile=high_beta" if strategy_profile == "high_beta"
            else "beta 数据不足"
        ),
    }


def get_market_environment_history(period: str = "1y"):
    """Fetch market series separately so market filters stay auditable."""
    symbols = {"nasdaq_close": "^IXIC", "vix_close": "^VIX", "ten_year_yield": "^TNX"}
    frames = []
    for column, symbol in symbols.items():
        try:
            data = _flatten_yfinance_columns(
                yf.Ticker(symbol).history(period=period, auto_adjust=False).reset_index()
            )
            date_column = "Date" if "Date" in data.columns else "Datetime"
            frame = data[[date_column, "Close"]].rename(columns={date_column: "date", "Close": column})
            frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frames.append(frame)
        except Exception as error:
            print(f"⚠️ 市场环境数据 {symbol} 拉取失败: {error}")
    if not frames:
        return pd.DataFrame(columns=["date", "market_regime"])
    market = frames[0]
    for frame in frames[1:]:
        market = market.merge(frame, on="date", how="outer")
    market = market.sort_values("date").reset_index(drop=True)
    if "nasdaq_close" not in market:
        print("⚠️ 纳指序列缺失，市场环境无法判定")
        return pd.DataFrame(columns=["date", "market_regime"])
    # 外连接会插入纳指没有、但 VIX/TNX 独有的日期（各指数假期口径不同）。
    # 这些行的 nasdaq_close 为 NaN，rolling(50) 会把其后连续 50 个交易日的
    # SMA 一并变成 NaN，使 market_regime 大面积误判为"数据不足"。
    # 以纳指交易日历为基准，再对另外两条序列做前向填充。
    market = market.dropna(subset=["nasdaq_close"]).reset_index(drop=True)
    for column in ("vix_close", "ten_year_yield"):
        if column not in market:
            market[column] = pd.NA
        market[column] = market[column].ffill()
    market["nasdaq_sma_50"] = market["nasdaq_close"].rolling(50).mean()
    market["vix_change_20"] = market["vix_close"].pct_change(20)
    market["yield_change_20"] = market["ten_year_yield"].diff(20)

    def classify(row):
        if pd.isna(row.get("nasdaq_sma_50")):
            return "数据不足"
        if (
            row["nasdaq_close"] > row["nasdaq_sma_50"]
            and row.get("vix_close", 99) < 25
            and row.get("vix_change_20", 1) <= 0.1
            and abs(row.get("yield_change_20", 99)) <= 0.4
        ):
            return "Risk-on"
        if row["nasdaq_close"] < row["nasdaq_sma_50"] or row.get("vix_close", 0) >= 30:
            return "Risk-off"
        return "Neutral"

    market["market_regime"] = market.apply(classify, axis=1)
    return market


def merge_market_environment(df: pd.DataFrame, market: pd.DataFrame):
    if market.empty:
        df["market_regime"] = "数据不足"
        return df
    return df.merge(market, on="date", how="left").assign(
        market_regime=lambda data: data["market_regime"].fillna("数据不足")
    )


def get_stock_comprehensive_data(ticker: str, period: str = "1y"):
    """
    同时获取历史行情（用于技术指标）与公司基本面/财报数据（用于多维评估）
    """
    stock = yf.Ticker(ticker)
    
    # 1. 抓取基本面概要和完整财报快照
    info = stock.info
    income_stmt = stock.quarterly_income_stmt
    cashflow = stock.quarterly_cashflow
    balance_sheet = stock.quarterly_balance_sheet

    revenue = _latest_statement_value(income_stmt, ["Total Revenue", "Operating Revenue"])
    gross_profit = _latest_statement_value(income_stmt, ["Gross Profit"])
    operating_income = _latest_statement_value(income_stmt, ["Operating Income"])
    net_income = _latest_statement_value(income_stmt, ["Net Income", "Net Income Common Stockholders"])
    operating_cash_flow = _latest_statement_value(
        cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]
    )
    capex = _latest_statement_value(
        cashflow, ["Capital Expenditure", "Capital Expenditure Reported"]
    )
    debt = _latest_statement_value(
        balance_sheet, ["Total Debt", "Long Term Debt And Capital Lease Obligation"]
    )
    cash = _latest_statement_value(
        balance_sheet, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]
    )
    free_cash_flow = operating_cash_flow + capex if capex < 0 else operating_cash_flow - capex
    revenue_history = _statement_history(income_stmt, ["Total Revenue", "Operating Revenue"], 8)
    gross_profit_history = _statement_history(income_stmt, ["Gross Profit"], 8)
    net_income_history = _statement_history(
        income_stmt, ["Net Income", "Net Income Common Stockholders"], 8
    )
    eps_history = _statement_history(income_stmt, ["Diluted EPS", "Basic EPS"], 8)
    operating_cash_flow_history = _statement_history(
        cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], 8
    )
    capex_history = _statement_history(
        cashflow, ["Capital Expenditure", "Capital Expenditure Reported"], 8
    )
    capex_by_period = {item["period"]: item["value"] for item in capex_history}
    free_cash_flow_history = [
        {"period": item["period"], "value": item["value"] + capex_by_period.get(item["period"], 0)}
        for item in operating_cash_flow_history
    ]
    gross_margin_history = _ratio_history(gross_profit_history, revenue_history)

    beta = _numeric_scalar(info.get("beta"), default=None)
    revenue_growth = _numeric_scalar(info.get("revenueGrowth"), default=None)
    fundamentals = {
        "market_cap": info.get("marketCap", 0),
        "trailing_pe": info.get("trailingPE", "N/A"),
        "forward_pe": info.get("forwardPE", "N/A"),
        "total_revenue": revenue or info.get("totalRevenue", 0),
        "revenue_growth": revenue_growth,
        "profit_margins": info.get("profitMargins", 0),
        "roe": info.get("returnOnEquity", "N/A"),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "beta": beta,
        "beta_source": "yfinance.info.beta（供应商口径未核实，仅作参考）" if beta is not None else "缺失",
        "financial_period": _statement_period(income_stmt),
        "financial_period_type": "季度财报（yfinance quarterly_income_stmt）",
        "financial_history_observations": {
            "income": len(income_stmt.columns) if income_stmt is not None else 0,
            "cashflow": len(cashflow.columns) if cashflow is not None else 0,
            "balance_sheet": len(balance_sheet.columns) if balance_sheet is not None else 0,
        },
        "point_in_time_note": "基本面为当前抓取快照，仅用于展示，不参与历史回测。",
        "price_basis": "OHLC 使用未做股息复权的价格；历史拆分按 Stock Splits 比例统一到当前股本口径，挂单价与最新盘口保持同一坐标。",
        "financials": {
            "revenue": revenue,
            "gross_profit": gross_profit,
            "operating_income": operating_income,
            "net_income": net_income,
            "operating_cash_flow": operating_cash_flow,
            "capital_expenditure": capex,
            "free_cash_flow": free_cash_flow,
            "total_debt": debt,
            "cash_and_equivalents": cash,
            "net_debt": debt - cash,
            "operating_margin": operating_income / revenue if revenue else None,
            "net_margin": net_income / revenue if revenue else None,
            "fcf_conversion": free_cash_flow / net_income if net_income else None,
        },
        "financial_history": {
            "revenue": revenue_history,
            "gross_profit": gross_profit_history,
            "gross_margin": gross_margin_history,
            "operating_income": _statement_history(income_stmt, ["Operating Income"], 8),
            "net_income": net_income_history,
            "eps": eps_history,
            "operating_cash_flow": operating_cash_flow_history,
            "capital_expenditure": capex_history,
            "free_cash_flow": free_cash_flow_history,
        },
    }
    fundamentals["assessment"] = _fundamental_assessment(fundamentals)
    fundamentals["financial_states"] = [
        _latest_state(revenue_history, "营收"),
        _latest_state(eps_history, "EPS"),
        _latest_state(gross_margin_history, "毛利率"),
        _latest_state(free_cash_flow_history, "自由现金流"),
    ]

    # 2. 抓取历史 OHLCV 数据并计算技术指标
    df = _flatten_yfinance_columns(
        stock.history(period=period or "1y", auto_adjust=False).reset_index()
    )
    date_col = 'Date' if 'Date' in df.columns else 'Datetime'
    df = df.rename(columns={
        date_col: 'date', 'Open': 'open', 'High': 'high', 
        'Low': 'low', 'Close': 'close', 'Volume': 'volume'
    })
    for column in ("open", "high", "low", "close", "volume"):
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    df = _split_adjust_ohlcv(df, stock.actions)

    # 技术指标计算
    df['SMA_20'] = df['close'].rolling(20).mean()
    df['SMA_50'] = df['close'].rolling(50).mean()
    
    df['RSI_14'] = _wilder_rsi(df['close'], period=14)

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR_14'] = tr.rolling(14).mean()
    df['Volume_SMA_20'] = df['volume'].rolling(20).mean()

    df_cleaned = df.dropna().reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume", "SMA_20", "SMA_50", "RSI_14", "ATR_14", "Volume_SMA_20"):
        if column in df_cleaned:
            df_cleaned[column] = df_cleaned[column].map(_numeric_scalar)
    return df_cleaned, fundamentals
if __name__ == "__main__":
    watchlist = [
        {"ticker": "NBIS", "total_capital": 1000, "risk_pct": 0.06},
        {"ticker": "MU", "total_capital": 1000, "risk_pct": 0.06, 'cost_basis': 973, 'stock_num': 2},
        {"ticker": "WDC", "total_capital": 800, "risk_pct": 0.06, 'cost_basis': 451, 'stock_num': 2},
        {"ticker": "CRWV", "total_capital": 800, "risk_pct": 0.06},
        {"ticker": "MRVL", "total_capital": 800, "risk_pct": 0.06},
        {"ticker": "AVGO", "total_capital": 500, "risk_pct": 0.06},
        {"ticker": "IBKR", "total_capital": 500, "risk_pct": 0.03, 'cost_basis': 89.25, 'stock_num': 4},
        {"ticker": "QQQM", "total_capital": 2000, "risk_pct": 0.06, 'cost_basis': 294.48, 'stock_num': 4},
        {"ticker": "VOO", "total_capital": 2000, "risk_pct": 0.08, 'cost_basis': 706.25, 'stock_num': 6.83},
        {"ticker": "TSLA", "total_capital": 20000, "risk_pct": 0.01},
    ]

    all_reports = []
    market_history = get_market_environment_history("5y")

    print("🔍 正在并行拉取行情技术指标与财报基本面数据...\n")

    for item in watchlist:
        ticker = item["ticker"]
        try:
            # 1. 获取带基本面和指标的数据
            full_df, fundamentals = get_stock_comprehensive_data(ticker, period="5y")
            research_df = full_df.copy()
            # 策略和趋势展示保留近一年，研究统计使用完整五年历史。
            df = full_df.tail(252).reset_index(drop=True)
            df = merge_market_environment(df, market_history)
            research_df = merge_market_environment(research_df, market_history)
            
            # 2. 根据资产属性选择策略；未显式配置时用 beta 自动识别高波动标的
            strategy_config = item.copy()
            beta = fundamentals.get("beta")
            current_atr_pct = float(df.iloc[-1]["ATR_14"] / df.iloc[-1]["close"] * 100)
            if "strategy_profile" not in strategy_config:
                # 不再让 yfinance.info.beta 单独决定策略；使用近期真实 OHLC 波动分类。
                strategy_config["strategy_profile"] = (
                    "high_beta" if current_atr_pct >= 4.0 else "standard"
                )
            asset_profile = _classify_asset(
                fundamentals,
                beta,
                strategy_config.get("strategy_profile"),
            )
            if current_atr_pct >= 4.0 and asset_profile["volatility_level"] != "高波动":
                asset_profile["volatility_level"] = "高波动"
                asset_profile["volatility_basis"] += f"; ATR/价格={current_atr_pct:.1f}%"
            asset_profile["atr_pct"] = round(current_atr_pct, 2)
            strategy_config["asset_profile"] = asset_profile
            latest_market = df.iloc[-1]
            strategy_config["market_environment"] = {
                "regime": latest_market.get("market_regime", "数据不足"),
                "nasdaq_close": latest_market.get("nasdaq_close"),
                "vix_close": latest_market.get("vix_close"),
                "ten_year_yield": latest_market.get("ten_year_yield"),
            }
            engine = ProfessionalStrategyEngine(strategy_config)
            report = engine.analyze(df)
            period_rows = {"1年": 252, "3年": 756, "5年": 1260}
            period_research = {}
            for label, rows in period_rows.items():
                window = research_df.tail(rows).reset_index(drop=True)
                period_research[label] = run_daily_research(window, lookback=252)
                method = period_research[label]["method"]
                actual_rows = len(window)
                method["research_data_start"] = str(window.iloc[0]["date"]) if actual_rows else "N/A"
                method["research_data_end"] = str(window.iloc[-1]["date"]) if actual_rows else "N/A"
                method["period_label"] = label
                method["requested_rows"] = rows
                method["actual_rows"] = actual_rows
                method["data_sufficient"] = actual_rows >= rows
                method["data_status"] = "完整" if actual_rows >= rows else "数据不足"
            report["daily_research_periods"] = period_research
            report["daily_research"] = period_research["1年"]
            report["daily_research"]["method"]["strategy_display_start"] = str(df.iloc[0]["date"])
            report["daily_research"]["method"]["strategy_display_end"] = str(df.iloc[-1]["date"])
            
            # 3. 将基本面字典与 RSI 挂载到报告中供 HTML 渲染
            report['fundamentals'] = fundamentals
            report["asset_profile"] = asset_profile
            report['rsi_14'] = round(df.iloc[-1]['RSI_14'], 2)
            
            all_reports.append(report)
        except Exception as e:
            print(f"❌ 处理标的 {ticker} 时出现异常: {e}")
            traceback.print_exc()

    # 每个标的独立生成一个 HTML，避免不同标的的统计相互干扰
    if all_reports:
        # 定时任务里没有桌面会话，不能弹浏览器
        headless = os.environ.get("US_HEADLESS") == "1"
        dashboard_dir = "dashboard"
        os.makedirs(dashboard_dir, exist_ok=True)
        generated_files = []
        attachments = []
        for index, report in enumerate(all_reports):
            safe_ticker = re.sub(r"[^A-Za-z0-9_-]+", "_", report["ticker"]).strip("_") or "UNKNOWN"
            output_file = os.path.join(dashboard_dir, f"dashboard_{safe_ticker}.html")
            generate_pro_html_dashboard(
                [report],
                output_file=output_file,
                open_browser=(index == 0 and not headless),
            )
            generated_files.append(output_file)
            # 附件名带上标的和数据日期，收件箱里一眼能分清
            attachments.append((f"{safe_ticker}_{report.get('date', '')}.html", output_file))
        print("📄 已生成独立标的看板: " + ", ".join(generated_files))

        send_daily_report(all_reports, attachments)
