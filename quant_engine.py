"""可审计的日样本量化研究引擎。

模块边界：
data -> features -> labels -> statistics -> backtest -> report

这里的研究统计与交易信号分离。所有特征只使用样本日及之前的数据，
未来行情只用于生成标签，不参与价格分组或特征计算。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


HORIZONS = (5, 10, 20, 40)
MIN_EFFECTIVE_SAMPLES = 30
GROUP_ORDER = ("Q1 低位", "Q2 偏低", "Q3 中位", "Q4 偏高", "Q5 高位")


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    """标准化日期和价格列，保留原始行情数据。"""
    required = {"date", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"研究数据缺少字段: {sorted(missing)}")
    data = df.copy().sort_values("date").reset_index(drop=True)
    data["date"] = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def build_features(df: pd.DataFrame, lookback: int = 252) -> pd.DataFrame:
    """为每个交易日建立只依赖过去数据的价格分位和环境特征。"""
    data = prepare_data(df)
    close = data["close"]
    prior_close = close.shift(1)
    rolling = prior_close.rolling(lookback, min_periods=max(60, lookback // 4))
    # Rolling.quantile 在不同 pandas 版本中不一致；逐个计算可兼容当前 .venv。
    data["price_q20"] = rolling.quantile(0.2)
    data["price_q40"] = rolling.quantile(0.4)
    data["price_q60"] = rolling.quantile(0.6)
    data["price_q80"] = rolling.quantile(0.8)
    data["price_group"] = np.select(
        [
            close <= data["price_q20"],
            close <= data["price_q40"],
            close <= data["price_q60"],
            close <= data["price_q80"],
        ],
        ["Q1 低位", "Q2 偏低", "Q3 中位", "Q4 偏高"],
        default="Q5 高位",
    )
    data.loc[data["price_q20"].isna(), "price_group"] = "数据不足"

    data["sma_20"] = close.rolling(20).mean()
    data["sma_50"] = close.rolling(50).mean()
    data["distance_to_sma50_pct"] = (close / data["sma_50"] - 1) * 100
    if "market_regime" not in data:
        data["market_regime"] = "未提供"
    data["market_regime"] = data["market_regime"].fillna("数据不足")
    return data


def build_labels(features: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """用未来行情生成标签；末尾不足完整持有期的样本保持为空。"""
    data = features.copy()
    for horizon in horizons:
        future_close = data["close"].shift(-horizon)
        data[f"return_{horizon}d_pct"] = (future_close / data["close"] - 1) * 100
        future_lows = pd.concat(
            [data["low"].shift(-offset) for offset in range(1, horizon + 1)],
            axis=1,
        )
        data[f"max_drawdown_{horizon}d_pct"] = (
            future_lows.min(axis=1) / data["close"] - 1
        ) * 100
        data[f"win_{horizon}d"] = data[f"return_{horizon}d_pct"] > 0
        if "ATR_14" in data:
            data[f"stop_hit_{horizon}d"] = (
                future_lows.min(axis=1) <= data["close"] - 2 * data["ATR_14"]
            )
        benchmark = "nasdaq_close" if "nasdaq_close" in data else None
        if benchmark:
            future_benchmark = data[benchmark].shift(-horizon)
            data[f"alpha_{horizon}d_pct"] = (
                data[f"return_{horizon}d_pct"]
                - (future_benchmark / data[benchmark] - 1) * 100
            )
    return data


def _safe_mean(series: pd.Series):
    return round(float(series.mean()), 2) if series.notna().any() else None


def _sample_quality(sample_count: int) -> str:
    if sample_count < 10:
        return "样本不足"
    if sample_count < 30:
        return "初步参考"
    if sample_count < 50:
        return "有一定参考价值"
    return "样本相对充分"


def _return_statistics(returns: pd.Series, min_samples: int):
    returns = returns.dropna()
    count = len(returns)
    if count == 0:
        return {
            "effective_count": 0, "win_rate_pct": None, "avg_return_pct": None,
            "avg_profit_pct": None, "avg_loss_pct": None, "profit_loss_ratio": None,
            "expected_value_pct": None, "profit_factor": None,
        }
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    avg_profit = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = abs(float(losses.mean())) if not losses.empty else 0.0
    total_loss = abs(float(losses.sum()))
    reliable = count >= min_samples
    return {
        "effective_count": count,
        "win_rate_pct": round(float((returns > 0).mean() * 100), 1) if reliable else None,
        "avg_return_pct": round(float(returns.mean()), 2) if reliable else None,
        "avg_profit_pct": round(avg_profit, 2) if reliable and not wins.empty else None,
        "avg_loss_pct": round(avg_loss, 2) if reliable and not losses.empty else None,
        "profit_loss_ratio": round(avg_profit / avg_loss, 2) if reliable and avg_loss else None,
        "expected_value_pct": round(
            float((returns > 0).mean() * avg_profit - (returns <= 0).mean() * avg_loss), 2
        ) if reliable else None,
        "profit_factor": round(float(wins.sum() / total_loss), 2) if reliable and total_loss else None,
    }


def build_statistics(
    labeled: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    group_column: str = "price_group",
    include_market_regime: bool = False,
) -> list[dict]:
    """按价格区域和环境汇总统计，小于门槛的分组不输出伪精确胜率。"""
    valid = labeled[labeled[group_column] != "数据不足"].copy()
    if valid.empty:
        return []
    group_columns = [group_column, "market_regime"] if include_market_regime else [group_column]
    rows = []
    for keys, group in valid.groupby(group_columns, dropna=False, sort=False):
        if include_market_regime:
            price_group, market_regime = keys
        else:
            # pandas 对单字段 groupby 也可能返回单元素 tuple；
            # 统一解包，避免报告中出现 ('Q2 偏低',) 这类键。
            price_group = keys[0] if isinstance(keys, tuple) else keys
            market_regime = "全部环境"
        row = {
            "price_group": price_group,
            "market_regime": market_regime,
            "sample_count": int(len(group)),
            "date_start": group["date"].min(),
            "date_end": group["date"].max(),
            # 该分组样本自身的实际收盘价范围。不能用当前 252 日的分位边界来描述，
            # 因为每个样本日是按它自己当时的滚动分位归组的。
            "price_min": round(float(group["close"].min()), 2),
            "price_max": round(float(group["close"].max()), 2),
            "current_group": False,
        }
        for horizon in horizons:
            returns = group[f"return_{horizon}d_pct"].dropna()
            drawdowns = group[f"max_drawdown_{horizon}d_pct"].dropna()
            wins = group[f"win_{horizon}d"].dropna()
            quality = _sample_quality(len(returns))
            return_stats = _return_statistics(returns, MIN_EFFECTIVE_SAMPLES)
            row[f"win_rate_{horizon}d_pct"] = return_stats["win_rate_pct"]
            row[f"avg_return_{horizon}d_pct"] = return_stats["avg_return_pct"]
            row[f"avg_max_drawdown_{horizon}d_pct"] = (
                _safe_mean(drawdowns) if len(drawdowns) >= MIN_EFFECTIVE_SAMPLES else None
            )
            row[f"label_count_{horizon}d"] = int(len(returns))
            row[f"quality_{horizon}d"] = quality
            for key in ("avg_profit_pct", "avg_loss_pct", "profit_loss_ratio",
                        "expected_value_pct", "profit_factor"):
                row[f"{key}_{horizon}d"] = return_stats[key]
            if f"stop_hit_{horizon}d" in group:
                stop_hits = group.loc[returns.index, f"stop_hit_{horizon}d"].dropna()
                row[f"stop_hit_rate_{horizon}d_pct"] = (
                    round(float(stop_hits.mean() * 100), 1)
                    if len(stop_hits) >= MIN_EFFECTIVE_SAMPLES else None
                )
            if f"alpha_{horizon}d_pct" in group:
                alpha_stats = _return_statistics(group.loc[returns.index, f"alpha_{horizon}d_pct"], MIN_EFFECTIVE_SAMPLES)
                row[f"alpha_win_rate_{horizon}d_pct"] = alpha_stats["win_rate_pct"]
                row[f"alpha_avg_{horizon}d_pct"] = alpha_stats["avg_return_pct"]
        rows.append(row)
    rows.sort(key=lambda item: (
        GROUP_ORDER.index(item["price_group"]) if item["price_group"] in GROUP_ORDER else len(GROUP_ORDER),
        str(item["market_regime"]),
    ))
    return rows


def calculate_current_context(
    labeled: pd.DataFrame,
    lookback: int = 252,
) -> dict:
    """使用当前日之前的数据解释当前价格所处的历史分位。"""
    data = labeled
    current = data.iloc[-1]
    prior_frame = data.iloc[:-1].tail(lookback)
    prior = prior_frame["close"]
    if len(prior) < max(60, lookback // 4):
        return {"price_group": "数据不足", "sample_count": len(prior)}
    edges = prior.quantile([0.2, 0.4, 0.6, 0.8]).tolist()
    price = float(current["close"])
    group = (
        "Q1 低位" if price <= edges[0]
        else "Q2 偏低" if price <= edges[1]
        else "Q3 中位" if price <= edges[2]
        else "Q4 偏高" if price <= edges[3]
        else "Q5 高位"
    )
    group_ranges = {
        "Q1 低位": {"lower": None, "upper": round(edges[0], 2), "label": f"≤ {edges[0]:.2f}"},
        "Q2 偏低": {"lower": round(edges[0], 2), "upper": round(edges[1], 2), "label": f"{edges[0]:.2f} - {edges[1]:.2f}"},
        "Q3 中位": {"lower": round(edges[1], 2), "upper": round(edges[2], 2), "label": f"{edges[1]:.2f} - {edges[2]:.2f}"},
        "Q4 偏高": {"lower": round(edges[2], 2), "upper": round(edges[3], 2), "label": f"{edges[2]:.2f} - {edges[3]:.2f}"},
        "Q5 高位": {"lower": round(edges[3], 2), "upper": None, "label": f"> {edges[3]:.2f}"},
    }
    return {
        "price": round(price, 2),
        "price_group": group,
        "history_start": str(prior_frame.iloc[0]["date"]),
        "history_observations": len(prior),
        "quantile_edges": [round(float(edge), 2) for edge in edges],
        "group_ranges": group_ranges,
        "market_regime": current.get("market_regime", "未提供"),
    }


def run_daily_research(
    df: pd.DataFrame,
    lookback: int = 252,
    horizons: tuple[int, ...] = HORIZONS,
) -> dict:
    """执行完整的日样本研究链路并返回可序列化报告。"""
    features = build_features(df, lookback=lookback)
    labeled = build_labels(features, horizons=horizons)
    statistics = build_statistics(labeled, horizons=horizons, include_market_regime=False)
    environment_statistics = build_statistics(
        labeled, horizons=horizons, include_market_regime=True
    )
    current_context = calculate_current_context(labeled, lookback=lookback)
    # 标出当前价所处的分组，否则读者很容易把别的分组（通常是样本最多的 Q5）
    # 的胜率当成当前位置的胜率。
    current_group = current_context.get("price_group")
    current_regime = current_context.get("market_regime")
    for row in statistics:
        row["current_group"] = row["price_group"] == current_group
    for row in environment_statistics:
        row["current_group"] = (
            row["price_group"] == current_group and row["market_regime"] == current_regime
        )
    return {
        "method": {
            "lookback_days": lookback,
            "horizons": list(horizons),
            "no_lookahead": True,
            "price_group_rule": "每个样本日使用此前lookback个交易日收盘价的20/40/60/80分位",
            "win_rule": "未来收盘收益大于0%",
            "drawdown_rule": "未来持有期最低日内低点相对样本日收盘价的跌幅",
            "minimum_effective_samples": MIN_EFFECTIVE_SAMPLES,
            "small_sample_rule": "有效样本少于30时不显示胜率、EV、利润因子等推断指标，只显示样本数和样本等级",
            "profit_loss_rule": "平均盈利/平均亏损",
            "expected_value_rule": "(胜率×平均盈利)-(败率×平均亏损)",
            "stop_loss_rule": "未来持有期内最低价触及样本日收盘-2×ATR14",
            "alpha_rule": "标的未来收益减去同期纳斯达克收益（数据可用时）",
            "price_range_rule": "表中价格范围是该分组样本自身的实际收盘价区间；分位边界随样本日滚动变化",
            "current_group_rule": "current_group=true 的行才是当前价所处的分组，其余分组仅作对照",
            "default_grouping": "默认只按价格区域统计，市场环境交叉统计单独保存，避免小样本网格",
            "research_period_recommendation": "建议至少5年或1000个交易日；策略展示窗口可独立保持1年",
        },
        "current_context": current_context,
        "statistics": statistics,
        "environment_statistics": environment_statistics,
        "sample_rows": int(len(labeled)),
    }
