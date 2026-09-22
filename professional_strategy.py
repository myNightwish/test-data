import pandas as pd
import numpy as np
import json

class ProfessionalStrategyEngine:
    # 区间事件研究的有效样本门槛，与 html_generator.MIN_ZONE_RESOLVED 保持一致。
    MIN_ZONE_RESOLVED = 10

    PROFILES = {
        "stable_index": {
            "label": "稳健指数策略",
            "probability_step_atr": 0.75,
            "stop_atr": 1.8,
            "target_atr": 2.7,
            "horizon": 20,
            "min_rr": 1.5,
            "trail_atr": 2.5,
        },
        "high_beta": {
            "label": "高贝塔波动策略",
            "probability_step_atr": 1.5,
            "stop_atr": 2.2,
            "target_atr": 4.0,
            "horizon": 15,
            "min_rr": 1.8,
            "trail_atr": 3.5,
        },
        "standard": {
            "label": "标准趋势策略",
            "probability_step_atr": 1.0,
            "stop_atr": 1.5,
            "target_atr": 2.25,
            "horizon": 15,
            "min_rr": 1.5,
            # LeBeau 吊灯止损的通用默认值是 3×ATR；高波动放宽，低波动收紧。
            "trail_atr": 3.0,
        },
    }

    def __init__(self, config: dict):
        self.config = config

    def _profile(self):
        profile_name = self.config.get("strategy_profile", "standard")
        return profile_name, self.PROFILES.get(profile_name, self.PROFILES["standard"])

    def _safe_buy_price(self, df: pd.DataFrame, atr: float, current_close: float) -> float:
        """Estimate an asset-specific buy center from support, trend and volatility."""
        support = float(df.tail(20)["low"].min())
        trend_value = float(df.iloc[-1]["SMA_20"])
        candidates = [support + 0.25 * atr, trend_value, current_close - 0.5 * atr]
        return float(np.median([price for price in candidates if price > 0]))

    def _historical_safe_buy_price(self, df: pd.DataFrame, index: int, atr: float) -> float:
        start = max(0, index - 20)
        support = float(df.iloc[start:index]["low"].min())
        trend_value = float(df.iloc[index]["SMA_20"])
        close = float(df.iloc[index]["close"])
        return float(np.median([support + 0.25 * atr, trend_value, close - 0.5 * atr]))

    @staticmethod
    def _cluster_levels(values: list[float], tolerance: float) -> list[dict]:
        """Merge nearby swing prices into repeatable price levels."""
        clusters = []
        for value in sorted(values):
            if not clusters or abs(value - clusters[-1]["center"]) > tolerance:
                clusters.append({"prices": [value], "center": value})
            else:
                clusters[-1]["prices"].append(value)
                clusters[-1]["center"] = float(np.mean(clusters[-1]["prices"]))
        return [
            {"price": round(cluster["center"], 2), "touches": len(cluster["prices"])}
            for cluster in clusters
            if len(cluster["prices"]) >= 2
        ]

    def _price_zones(self, history: pd.DataFrame) -> list[dict]:
        """Create confirmed, non-uniform zones from information available at history[-1]."""
        lookback = self.config.get("zone_lookback", 60)
        strength = self.config.get("swing_strength", 3)
        window = history.tail(lookback).reset_index(drop=True)
        curr = window.iloc[-1]
        atr = float(curr["ATR_14"])
        zone_width = max(atr * self.config.get("zone_width_atr", 0.25), float(curr["close"]) * 0.001)
        pivots = []
        # A pivot is usable only after `strength` bars on its right have closed.
        for index in range(strength, len(window) - strength):
            low = float(window.iloc[index]["low"])
            high = float(window.iloc[index]["high"])
            left = window.iloc[index - strength:index]
            right = window.iloc[index + 1:index + strength + 1]
            if low <= float(left["low"].min()) and low <= float(right["low"].min()):
                pivots.append({"price": low, "kind": "support"})
            if high >= float(left["high"].max()) and high >= float(right["high"].max()):
                pivots.append({"price": high, "kind": "resistance"})
        support_levels = self._cluster_levels(
            [pivot["price"] for pivot in pivots if pivot["kind"] == "support"],
            zone_width,
        )
        resistance_levels = self._cluster_levels(
            [pivot["price"] for pivot in pivots if pivot["kind"] == "resistance"],
            zone_width,
        )
        price = float(curr["close"])
        below = sorted((level for level in support_levels if level["price"] <= price), key=lambda level: level["price"])
        above = sorted((level for level in resistance_levels if level["price"] > price), key=lambda level: level["price"])
        support = below[-1] if below else {"price": float(window["low"].quantile(0.25)), "touches": 0}
        lower_support = below[-2] if len(below) > 1 else {"price": float(window["low"].min()), "touches": 0}
        resistance = above[0] if above else {"price": float(window["high"].quantile(0.75)), "touches": 0}
        breakout = {
            "price": resistance["price"] + 0.5 * atr,
            "touches": 0,
        }
        value = float(curr["SMA_20"])
        raw_zones = [
            ("结构失效/重建区", lower_support, "支撑下沿被有效跌破后，等待结构重建"),
            ("支撑区", support, "已确认局部低点集群形成的支撑区域"),
            ("趋势均衡区", {"price": value, "touches": 0}, "SMA20 参考区，不代表基本面内在价值"),
            ("阻力区", resistance, "已确认局部高点集群形成的阻力区域"),
            ("突破确认区", breakout, "阻力区上沿上方0.5 ATR，需收盘确认突破"),
        ]
        zones = []
        for name, level, rationale in raw_zones:
            center = float(level["price"])
            zones.append({
                "name": name,
                "zone_type": name,
                "center": round(center, 2),
                "lower": round(center - zone_width, 2),
                "upper": round(center + zone_width, 2),
                "width_atr": round(zone_width / atr, 2) if atr else None,
                "distance_atr": round((center - price) / atr, 2) if atr else None,
                "rationale": rationale,
                "touches": int(level.get("touches", 0)),
                "established_by": (
                    "确认后的3根右侧K线局部低点/高点集群"
                    if "区" in name and name != "趋势均衡区" else "SMA20"
                ),
            })
        return zones

    def _zone_backtest(self, df: pd.DataFrame) -> list[dict]:
        """Walk-forward event study: every zone is rebuilt using information available then."""
        _, profile = self._profile()
        horizon = profile["horizon"]
        start = max(self.config.get("zone_lookback", 60) + 5, 80)
        summary = {}
        for index in range(start, len(df) - horizon):
            history = df.iloc[:index + 1]
            event = history.iloc[-1]
            atr = float(event["ATR_14"])
            if not np.isfinite(atr) or atr <= 0:
                continue
            previous = df.iloc[index - 1]
            for zone in self._price_zones(history):
                entered_zone = (
                    float(event["low"]) <= zone["upper"]
                    and float(event["high"]) >= zone["lower"]
                )
                was_in_previous_zone = (
                    float(previous["low"]) <= zone["upper"]
                    and float(previous["high"]) >= zone["lower"]
                )
                # 连续停留只算一次独立事件。
                if not entered_zone or was_in_previous_zone:
                    continue
                market_regime = event.get("market_regime", "数据不足")
                bucket_key = (zone["name"], market_regime)
                bucket = summary.setdefault(bucket_key, {
                    "name": zone["name"], "market_regime": market_regime,
                    "wins": 0, "losses": 0, "timeouts": 0,
                    "untriggered": 0, "candidates": 0, "triggered": 0,
                    "distances": [], "returns": [], "holding_days": [],
                })
                bucket["candidates"] += 1
                bucket["triggered"] += 1
                entry = zone["center"]
                stop = entry - profile["stop_atr"] * atr
                target = entry + profile["target_atr"] * atr
                entered = True
                outcome = None
                for holding_days, (_, future) in enumerate(
                    df.iloc[index + 1:index + horizon + 1].iterrows(), start=1
                ):
                    hit_stop = float(future["low"]) <= stop
                    hit_target = float(future["high"]) >= target
                    if hit_stop or hit_target:
                        outcome = "loss" if hit_stop else "win"
                        bucket["returns"].append(
                            (float(future["close"]) / entry - 1) * 100
                        )
                        bucket["holding_days"].append(holding_days)
                        break
                if outcome == "win":
                    bucket["wins"] += 1
                elif outcome == "loss":
                    bucket["losses"] += 1
                else:
                    bucket["timeouts"] += 1
                bucket["distances"].append(zone["distance_atr"])
        rows = []
        for bucket in summary.values():
            resolved = bucket["wins"] + bucket["losses"]
            # 有效样本不足时不输出胜率和均值，避免 1胜3负 被读成 25.0% 这种伪精确值。
            reliable = resolved >= self.MIN_ZONE_RESOLVED
            rows.append({
                **{key: value for key, value in bucket.items() if key != "distances"},
                "resolved": resolved,
                "win_rate": round(bucket["wins"] / resolved * 100, 1) if reliable else None,
                "min_resolved": self.MIN_ZONE_RESOLVED,
                "avg_distance_atr": round(float(np.nanmean(bucket["distances"])), 2),
                "avg_return_pct": round(float(np.mean(bucket["returns"])), 2) if reliable and bucket["returns"] else None,
                "median_return_pct": round(float(np.median(bucket["returns"])), 2) if reliable and bucket["returns"] else None,
                "max_return_pct": round(float(np.max(bucket["returns"])), 2) if bucket["returns"] else None,
                "max_loss_pct": round(float(np.min(bucket["returns"])), 2) if bucket["returns"] else None,
                "avg_holding_days": round(float(np.mean(bucket["holding_days"])), 1) if bucket["holding_days"] else None,
                "confidence": (
                    "样本不足" if resolved < self.MIN_ZONE_RESOLVED
                    else "初步参考" if resolved < 30
                    else "有一定参考价值" if resolved < 50
                    else "样本相对充分"
                ),
            })
        return rows

    @staticmethod
    def _asymmetric_grid(history: pd.DataFrame, atr: float) -> list[float]:
        """Derive non-symmetric offsets from historical intraday excursion density."""
        sample = history.tail(252).copy()
        downside = ((sample["close"] - sample["low"]) / sample["ATR_14"]).replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        upside = ((sample["high"] - sample["close"]) / sample["ATR_14"]).replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        down_q75 = float(downside.quantile(0.75)) if not downside.empty else 0.75
        down_q90 = float(downside.quantile(0.90)) if not downside.empty else 1.25
        up_q75 = float(upside.quantile(0.75)) if not upside.empty else 0.75
        up_q90 = float(upside.quantile(0.90)) if not upside.empty else 1.25
        return [
            -max(0.5, down_q90),
            -max(0.25, down_q75),
            0.0,
            max(0.25, up_q75),
            max(0.5, up_q90),
        ]

    def _probability_ladder(self, df: pd.DataFrame, atr: float, safe_buy_price: float) -> list:
        """Backtest five historical-excursion-derived, non-symmetric entries."""
        _, profile = self._profile()
        offsets = self._asymmetric_grid(df.iloc[:-1], atr)
        stop_atr = profile["stop_atr"]
        target_atr = profile["target_atr"]
        horizon = profile["horizon"]
        rows = []
        min_history = max(55, horizon + 1)

        for offset_index, offset in enumerate(offsets):
            wins = losses = timeouts = triggered = untriggered = opportunities = 0
            for i in range(min_history, len(df) - horizon):
                row = df.iloc[i]
                historical_atr = float(row["ATR_14"])
                if not np.isfinite(historical_atr) or historical_atr <= 0:
                    continue
                opportunities += 1
                historical_center = self._historical_safe_buy_price(df, i, historical_atr)
                historical_offsets = self._asymmetric_grid(df.iloc[:i], historical_atr)
                # 按位置取档位；用 offsets.index(offset) 会在分位被下限钳位成
                # 同一数值时错配到第一个同值档位。
                historical_offset = historical_offsets[offset_index]
                entry = historical_center + historical_offset * historical_atr
                stop = entry - stop_atr * historical_atr
                target = entry + target_atr * historical_atr
                outcome = None
                entered = False
                use_buy_stop = entry >= float(row["close"])
                for _, future in df.iloc[i + 1:i + horizon + 1].iterrows():
                    if not entered:
                        entered = (
                            float(future["high"]) >= entry
                            if use_buy_stop
                            else float(future["low"]) <= entry
                        )
                        if not entered:
                            continue
                        triggered += 1
                    hit_stop = float(future["low"]) <= stop
                    hit_target = float(future["high"]) >= target
                    if hit_stop or hit_target:
                        # Same-bar ambiguity is conservatively classified as a loss.
                        outcome = "loss" if hit_stop else "win"
                        break
                if not entered:
                    untriggered += 1
                elif outcome:
                    wins += outcome == "win"
                    losses += outcome == "loss"
                else:
                    timeouts += 1
            resolved = wins + losses
            win_rate = wins / resolved if resolved else None
            rows.append({
                "offset_atr": offset,
                "offset_source": "过去252日真实日内高低点/ATR的75%与90%分位",
                "entry_price": round(safe_buy_price + offset * atr, 2),
                "direction": "下方" if offset < 0 else "中心" if offset == 0 else "上方",
                "target_distance": round(target_atr * atr, 2),
                "stop_distance": round(stop_atr * atr, 2),
                "win_rate": round(win_rate * 100, 1) if win_rate is not None else None,
                "wins": wins,
                "losses": losses,
                "timeouts": timeouts,
                "triggered": triggered,
                "untriggered": untriggered,
                "opportunities": opportunities,
                "samples": resolved,
                "confidence": "可参考" if resolved >= 30 else "样本不足",
            })
        return rows

    @staticmethod
    def _volume_profile(df: pd.DataFrame, bins: int = 40, value_area: float = 0.7):
        """真正的成交量分布：VPOC 与 70% 价值区间，而不是拿 SMA20 冒充密集带。"""
        if "volume" not in df or not np.isfinite(float(df["volume"].sum())) or float(df["volume"].sum()) <= 0:
            return None
        low, high = float(df["low"].min()), float(df["high"].max())
        if not (np.isfinite(low) and np.isfinite(high)) or high <= low:
            return None
        edges = np.linspace(low, high, bins + 1)
        centers = (edges[:-1] + edges[1:]) / 2
        hist = np.zeros(bins)
        for bar_low, bar_high, volume in zip(df["low"], df["high"], df["volume"]):
            bar_low, bar_high, volume = float(bar_low), float(bar_high), float(volume)
            if not np.isfinite(volume) or volume <= 0:
                continue
            # 单根K线的成交量按其高低区间等分摊入各价格桶（Market Profile 常用近似）
            lo_idx = min(max(int(np.searchsorted(edges, bar_low, side="right")) - 1, 0), bins - 1)
            hi_idx = min(max(int(np.searchsorted(edges, bar_high, side="left")) - 1, lo_idx), bins - 1)
            hist[lo_idx:hi_idx + 1] += volume / (hi_idx - lo_idx + 1)
        total = float(hist.sum())
        if total <= 0:
            return None
        poc = int(np.argmax(hist))
        lower = upper = poc
        covered = float(hist[poc])
        # 从 VPOC 向成交量更大的一侧逐桶扩张，直到覆盖 value_area 比例
        while covered < total * value_area and (lower > 0 or upper < bins - 1):
            down = float(hist[lower - 1]) if lower > 0 else -1.0
            up = float(hist[upper + 1]) if upper < bins - 1 else -1.0
            if up >= down:
                upper += 1
                covered += float(hist[upper])
            else:
                lower -= 1
                covered += float(hist[lower])
        return {
            "vpoc": round(float(centers[poc]), 2),
            "value_area_low": round(float(edges[lower]), 2),
            "value_area_high": round(float(edges[upper + 1]), 2),
            "coverage_pct": round(covered / total * 100, 1),
            "range_low": round(low, 2),
            "range_high": round(high, 2),
            "basis": f"{len(df)} 个交易日 × {bins} 个价格桶，成交量按每根K线高低区间摊入",
        }

    def _position_levels(self, df: pd.DataFrame, atr: float, cost_basis: float) -> dict:
        """持仓的各道价格防线。多头取最严者（最高的止损位）作为有效止损。"""
        _, profile = self._profile()
        curr = df.iloc[-1]
        price = float(curr["close"])
        initial_risk = profile["stop_atr"] * atr
        # 吊灯止损（LeBeau Chandelier Exit）需要"持仓以来最高收盘"。没有 entry_date
        # 时退化为最近 horizon 根K线，并在 basis 里说明，不假装知道建仓点。
        entry_date = self.config.get("entry_date")
        dates = df["date"].astype(str)
        if entry_date and str(entry_date) in set(dates):
            held = df.loc[dates >= str(entry_date)]
            peak_basis = f"自 {entry_date} 起 {len(held)} 个交易日最高收盘"
        else:
            held = df.tail(profile["horizon"])
            peak_basis = f"未提供 entry_date，取最近 {len(held)} 个交易日最高收盘"
        peak_close = float(held["close"].max())
        sma_20, sma_50 = float(curr["SMA_20"]), float(curr["SMA_50"])
        # 只有这三条是"真实止损"：都锚定在成本或持仓高点上，跌破即按纪律离场。
        stop_lines = [
            {"name": "初始硬止损", "level": cost_basis - initial_risk, "active": True,
             "kind": "stop",
             "basis": f"成本 - {profile['stop_atr']}×ATR，即建仓时定死的 1R"},
            {"name": "保本止损", "level": cost_basis,
             "active": price >= cost_basis + initial_risk, "kind": "stop",
             "basis": "浮盈达到 +1R 后把止损推到成本价"},
            {"name": "吊灯移动止损", "level": peak_close - profile["trail_atr"] * atr,
             "active": True, "kind": "stop",
             "basis": f"{peak_basis} - {profile['trail_atr']}×ATR，只上移不下移"},
        ]
        # SMA50 是趋势健康度参考，不是止损：回调时它常常高于现价，
        # 若混进止损里参与 breach 判定，会在真实止损远未触及时误发清仓。
        structure_line = {
            "name": "结构参考 SMA50", "level": sma_50, "active": True, "kind": "structure",
            "basis": "收盘跌破 SMA50 记回调预警；若 SMA20 同时下穿 SMA50 才算趋势反转",
        }
        lines = stop_lines + [structure_line]
        for line in lines:
            line["level"] = round(float(line["level"]), 2)
            line["breached"] = line["active"] and price <= line["level"]
        effective = max(
            (line for line in stop_lines if line["active"] and not line["breached"]),
            key=lambda line: line["level"],
            default=None,
        )
        below_sma50 = price <= round(sma_50, 2)
        return {
            "lines": lines,
            "effective_stop": effective["level"] if effective else None,
            "effective_stop_name": effective["name"] if effective else None,
            # breached 只统计真实止损，结构参考单独用两个标志表达。
            "breached": [line["name"] for line in stop_lines if line["breached"]],
            "structure_warning": bool(below_sma50 and sma_20 > sma_50),
            "structure_failed": bool(below_sma50 and sma_20 <= sma_50),
            "initial_risk": round(initial_risk, 2),
            "tp1": round(cost_basis + profile["target_atr"] * atr, 2),
            "tp2": round(cost_basis + 2 * profile["target_atr"] * atr, 2),
        }

    def _action_plan(self, df: pd.DataFrame, atr: float, status: str,
                     volatility_expanding: bool, entry_price: float = 0.0,
                     stop_loss: float = 0.0, suggested_shares: int = 0) -> dict:
        """把当前状态翻译成明确的动作标注：买入/卖出/止损/止盈/加仓/减仓。"""
        _, profile = self._profile()
        curr = df.iloc[-1]
        price = float(curr["close"])
        cost_basis = self.config.get("cost_basis")
        has_position = isinstance(cost_basis, (int, float)) and cost_basis > 0
        if not has_position:
            return self._decide_entry_action(
                status, entry_price, stop_loss, suggested_shares, profile, atr, price,
            )

        levels = self._position_levels(df, atr, float(cost_basis))
        initial_risk = levels["initial_risk"]
        r_multiple = (price - cost_basis) / initial_risk if initial_risk > 0 else 0.0
        pnl_pct = (price / cost_basis - 1) * 100
        # 结构完好 = 没跌破 SMA50；跌破分"回调预警"和"趋势反转"两级，见 _position_levels。
        structure_intact = not (levels["structure_warning"] or levels["structure_failed"])
        below_sma20 = price < float(curr["SMA_20"])
        return self._decide_position_action(
            df, price, cost_basis, levels, r_multiple, pnl_pct,
            structure_intact, below_sma20, volatility_expanding, status,
        )

    def _decide_entry_action(self, status, entry_price, stop_loss, suggested_shares,
                             profile, atr, price) -> dict:
        """空仓时的动作标注。空仓只有"建仓"一种买入，没有加仓/减仓/止盈可言。"""
        buy_ready = status == "ACTIONABLE_SIGNAL"
        watching = status == "WATCHLIST_SETUP"
        shares = suggested_shares if buy_ready else 0
        if buy_ready:
            primary = {"side": "买入", "action": "买入建仓", "kind": "BUY", "urgency": "可执行",
                       "shares": shares,
                       "reason": (f"空仓且入场信号成立：在 ${entry_price:.2f} 挂 Buy Stop 建仓 "
                                  f"{shares} 股，止损 ${stop_loss:.2f}")}
        elif watching:
            primary = {"side": "观望", "action": "挂单等待突破确认", "kind": "WAIT", "urgency": "等待",
                       "shares": 0,
                       "reason": f"空仓，结构尚可但需价格突破 ${entry_price:.2f} 才确认，不提前接飞刀"}
        else:
            primary = {"side": "观望", "action": "不操作", "kind": "NONE", "urgency": "无",
                       "shares": 0,
                       "reason": "空仓且入场条件不满足，没有需要执行的动作"}
        entry_levels = {
            "lines": [
                {"name": "挂单触发价", "level": round(float(entry_price), 2), "breached": False,
                 "basis": "最新收盘日高点 + 0.1×ATR，突破才确认"},
                {"name": "建仓后初始止损", "level": round(float(stop_loss), 2), "breached": False,
                 "basis": f"触发价 - {profile['stop_atr']}×ATR，即建仓后的 1R"},
                {"name": "TP1", "level": round(float(entry_price + profile["target_atr"] * atr), 2),
                 "breached": False, "basis": f"触发价 + {profile['target_atr']}×ATR"},
                {"name": "结构参考 SMA50", "level": None, "breached": False,
                 "basis": "建仓后跌破 SMA50 视为结构失效"},
            ],
            "effective_stop": round(float(stop_loss), 2),
            "effective_stop_name": "建仓后初始止损",
            "tp1": round(float(entry_price + profile["target_atr"] * atr), 2),
            "tp2": round(float(entry_price + 2 * profile["target_atr"] * atr), 2),
        }
        entry_levels["lines"] = [line for line in entry_levels["lines"] if line["level"] is not None]
        options = [
            {"side": "买入", "action": "买入建仓", "kind": "BUY", "applicable": buy_ready,
             "shares": shares,
             "reason": (f"入场信号成立，按风险预算可买 {shares} 股" if buy_ready
                        else f"需入场信号成立（当前 {status}）")},
            {"side": "观望", "action": "挂单等待突破", "kind": "WAIT", "applicable": watching,
             "shares": 0,
             "reason": (f"等价格突破 ${entry_price:.2f}" if watching
                        else "仅在趋势成立但仍处回落惯性时适用")},
            {"side": "卖出", "action": "止损清仓", "kind": "STOP_LOSS", "applicable": False,
             "shares": 0, "reason": "空仓，无持仓可止损"},
            {"side": "卖出", "action": "止盈减仓", "kind": "TAKE_PROFIT", "applicable": False,
             "shares": 0, "reason": "空仓，无浮盈可止盈"},
            {"side": "卖出", "action": "减仓摊低风险", "kind": "REDUCE_RISK", "applicable": False,
             "shares": 0, "reason": "空仓，无暴露可减"},
            {"side": "买入", "action": "加仓（摊低成本/顺势）", "kind": "ADD", "applicable": False,
             "shares": 0, "reason": "空仓时只有建仓，没有加仓；建仓成交后才会区分摊低成本与顺势加仓"},
        ]
        return {
            "has_position": False, "cost_basis": None, "pnl_pct": None,
            "r_multiple": None, "primary": primary, "levels": entry_levels,
            "options": options,
            "headline": f"空仓 → {primary['side']}·{primary['action']}",
            "entry_signal": status,
            "entry_signal_note": "空仓状态，入场信号就是当前结论",
            "shares": 0, "position_value": None, "pnl_amount": None,
            "current_risk": None,
            "risk_budget": round(self.config.get("total_capital", 10000.0)
                                 * self.config.get("risk_pct", 0.01), 2),
            "risk_over_budget": False, "risk_utilization_pct": None,
            "trim_shares": 0, "reduce_shares": 0, "half_shares": 0,
            "add_on_shares": 0,
            "add_on_basis": "空仓无加仓概念；建仓股数 = floor(单笔风险预算 / 每股风险)。",
            "risk_basis": (
                f"建仓风险 = {shares} 股 × (触发价 ${entry_price:.2f} - 止损 ${stop_loss:.2f}) "
                f"= ${shares * max(entry_price - stop_loss, 0):.2f}"
            ) if shares else "未建仓，暂无持仓风险。",
            "note": "未配置 cost_basis，按空仓处理；建仓后填入 cost_basis 和 stock_num 才会输出持仓侧标注。",
        }

    def _decide_position_action(self, df, price, cost_basis, levels, r_multiple,
                                pnl_pct, structure_intact, below_sma20,
                                volatility_expanding, status) -> dict:
        """持仓状态下的动作优先级：止损 > 止盈 > 减仓 > 加仓 > 持有。"""
        capital = self.config.get("total_capital", 10000.0)
        risk_pct = self.config.get("risk_pct", 0.01)
        max_risk = capital * risk_pct
        stop = levels["effective_stop"]
        raw_shares = self.config.get("stock_num")
        shares = int(raw_shares) if isinstance(raw_shares, (int, float)) and raw_shares > 0 else 0
        position_value = shares * price
        pnl_amount = (price - cost_basis) * shares
        # 当前持仓风险 = 股数 × 到有效止损的距离；已跌破时距离记 0，风险已实现。
        stop_distance = max(price - stop, 0.0) if stop is not None else 0.0
        current_risk = shares * stop_distance
        risk_over_budget = shares > 0 and current_risk > max_risk
        keep_shares = int(max_risk // stop_distance) if stop_distance > 0 else 0
        trim_shares = max(0, shares - keep_shares) if shares else 0
        half_shares = max(1, shares // 2) if shares else 0
        # 加仓档位的每股风险不能用"价格到止损的距离"直接算：价格贴着止损时
        # 这个距离会趋近 0，算出天文数字的股数。用 1R 作为下限，并再受资金约束。
        add_on_risk = max(stop_distance, levels["initial_risk"])
        remaining_risk = max(max_risk - current_risk, 0.0)
        risk_capped = int(remaining_risk // add_on_risk) if add_on_risk > 0 else 0
        free_capital = max(capital - position_value, 0.0)
        capital_capped = int(free_capital // price) if price > 0 else 0
        add_on_shares = max(0, min(risk_capped, capital_capped))
        breached = levels["breached"]
        # 加仓摊低成本只在"结构没坏且没跌破任何止损"时才允许；否则正确动作是止损。
        can_add = (r_multiple < 0 and structure_intact and not breached
                   and not below_sma20 and add_on_shares > 0)
        # 顺势加仓（金字塔）：已有安全垫 + 新入场信号成立时才加，与"摊低成本"是两回事。
        can_add_up = (status == "ACTIONABLE_SIGNAL" and r_multiple >= 0.5
                      and structure_intact and not breached and add_on_shares > 0)
        can_reduce = risk_over_budget or volatility_expanding
        # 超预算时减到预算内；因波动率扩张而减仓时没有预算算法可依，按减半处理。
        reduce_shares = trim_shares if risk_over_budget else half_shares
        shares_note = "" if shares else "（未提供 stock_num，股数类建议无法给出具体数量）"
        structure_warning = levels["structure_warning"]
        structure_failed = levels["structure_failed"]
        # 均线死叉是滞后指标（由最长 50 天的历史算出），它不能决定今天要不要离场。
        # 唯一有权清仓的是真实止损：那是建仓时就定死、风险可量化的那条线。
        # 想让死叉直接清仓的，显式打开 exit_on_trend_break。
        exit_on_trend_break = bool(self.config.get("exit_on_trend_break", False))
        trend_break_exit = structure_failed and exit_on_trend_break
        exit_all = bool(breached) or trend_break_exit
        exit_all_reason = (
            f"已跌破真实止损 {'、'.join(breached)}" if breached
            else "SMA20 下穿 SMA50 且收盘在 SMA50 下方，且已开启 exit_on_trend_break"
            if trend_break_exit else ""
        )
        take_profit = price >= levels["tp1"]
        exit_part = (take_profit or can_reduce) and not exit_all
        part_shares = (half_shares if take_profit else reduce_shares) if exit_part else 0
        can_add_any = (can_add_up or can_add) and not exit_all
        add_shares = add_on_shares if can_add_any else 0
        hold_only = not exit_all and not exit_part and not can_add_any
        options = [
            {"side": "卖出", "action": "全部卖出（清仓）", "kind": "EXIT_ALL",
             "applicable": bool(exit_all),
             "shares": shares if exit_all else 0,
             "reason": (f"{exit_all_reason}，清掉全部 {shares} 股" if exit_all else
                        f"真实止损（有效止损 ${levels['effective_stop']}）未破，不清仓；"
                        f"均线死叉是滞后信号，不作为清仓依据（可用 exit_on_trend_break 改变）")},
            {"side": "卖出", "action": "部分卖出（止盈/降风险）", "kind": "EXIT_PART",
             "applicable": bool(exit_part),
             "shares": part_shares,
             "reason": ((f"已达 TP1 ${levels['tp1']}，减 {part_shares} 股并把止损推到保本"
                         if take_profit else
                         f"持仓风险 ${current_risk:.0f} 超预算 ${max_risk:.0f}，减 {part_shares} 股回到预算内"
                         if risk_over_budget else
                         f"波动率扩张，先减 {part_shares} 股降暴露")
                        if exit_part else
                        f"未达 TP1 ${levels['tp1']}，且持仓风险 ${current_risk:.0f} ≤ 预算 "
                        f"${max_risk:.0f}、波动率未失控{shares_note}")},
            {"side": "买入", "action": "追加（顺势/摊低成本）", "kind": "ADD",
             "applicable": bool(can_add_any),
             "shares": add_shares,
             "reason": ((f"顺势加仓：浮盈 {r_multiple:.2f}R 且入场信号成立，可再买 {add_shares} 股"
                         if can_add_up else
                         f"摊低成本：浮亏 {pnl_pct:.2f}% 但结构完好，可再买 {add_shares} 股")
                        if can_add_any else
                        ("收盘在 SMA50 下方（回调预警），结构修复前不追加" if structure_warning or structure_failed
                         else f"顺势加仓需浮盈 ≥ 0.5R（当前 {r_multiple:.2f}R）且入场信号成立"
                              f"（当前 {status}）；摊低成本需浮亏且结构完好"))},
            {"side": "持有", "action": "维持不动", "kind": "HOLD",
             "applicable": bool(hold_only),
             "shares": 0,
             "reason": ("止损与止盈都未触发，交给吊灯止损跟踪" if hold_only
                        else "当前有更优先的动作需要执行")},
        ]
        if exit_all:
            primary = {"side": "卖出", "action": "全部卖出（清仓）", "kind": "EXIT_ALL",
                       "detail": "止损" if breached else "趋势反转",
                       "urgency": "立即", "shares": shares,
                       "reason": f"{exit_all_reason}，清掉全部 {shares} 股"}
        elif price >= levels["tp2"]:
            primary = {"side": "卖出", "action": "部分卖出（止盈第二批）", "kind": "EXIT_PART",
                       "detail": "止盈", "urgency": "高", "shares": half_shares,
                       "reason": f"已达 TP2 ${levels['tp2']}，再减 {half_shares} 股，余仓交给吊灯止损"}
        elif take_profit:
            primary = {"side": "卖出", "action": "部分卖出（止盈第一批）+ 止损上移保本",
                       "kind": "EXIT_PART", "detail": "止盈", "urgency": "中", "shares": half_shares,
                       "reason": f"已达 TP1 ${levels['tp1']}，减 {half_shares} 股锁定利润并把止损推到成本价"}
        elif risk_over_budget:
            primary = {"side": "卖出", "action": "部分卖出（降风险）", "kind": "EXIT_PART",
                       "detail": "降风险", "urgency": "中", "shares": trim_shares,
                       "reason": f"持仓风险 ${current_risk:.0f} 超出预算 ${max_risk:.0f}，减 {trim_shares} 股回到预算内"}
        elif volatility_expanding:
            primary = {"side": "卖出", "action": "部分卖出（降风险）", "kind": "EXIT_PART",
                       "detail": "降风险", "urgency": "中", "shares": reduce_shares,
                       "reason": f"波动率扩张，减 {reduce_shares} 股降低单笔暴露"}
        elif can_add_up:
            primary = {"side": "买入", "action": "追加（顺势加仓）", "kind": "ADD",
                       "detail": "顺势", "urgency": "可选", "shares": add_on_shares,
                       "reason": (f"浮盈 {r_multiple:.2f}R 且入场信号成立，可再买 {add_on_shares} 股"
                                  f"（剩余风险预算 ${remaining_risk:.0f}）"
                                  + ("；浮盈已过 1R，同时把止损推到保本" if r_multiple >= 1 else ""))}
        elif can_add:
            primary = {"side": "买入", "action": "追加（摊低成本）", "kind": "ADD",
                       "detail": "摊低成本", "urgency": "可选", "shares": add_on_shares,
                       "reason": f"浮亏 {pnl_pct:.2f}% 但结构完好，可再买 {add_on_shares} 股（剩余风险预算 ${remaining_risk:.0f}）"}
        else:
            hold_reason = "止损与止盈都未触发，交给吊灯止损跟踪"
            sma_50_level = next(
                (line["level"] for line in levels["lines"] if line["kind"] == "structure"), None
            )
            if structure_failed:
                hold_reason = (
                    f"SMA20 已下穿 SMA50（${sma_50_level}）、收盘在其下方，中期趋势转弱，"
                    f"但真实止损 ${levels['effective_stop']}（{levels['effective_stop_name']}）"
                    f"距现价还有 {(price - (levels['effective_stop'] or price)) / price * 100:.1f}%，"
                    "未破就不清仓；均线是滞后信号，提前离场等于放弃自己定好的风险边界。"
                    "结构修复前不追加"
                )
            elif structure_warning:
                hold_reason = (
                    f"收盘 ${price:.2f} 在 SMA50 ${sma_50_level} 下方，属回调预警；"
                    "SMA20 仍在 SMA50 上方、真实止损未破，不构成清仓，结构修复前也不追加"
                )
            if r_multiple >= 1:
                hold_reason += f"；浮盈 {r_multiple:.2f}R ≥ 1R，把止损推到保本价 ${cost_basis:.2f}"
            primary = {"side": "持有", "action": "维持不动", "kind": "HOLD",
                       "detail": "趋势转弱观察" if structure_failed else "观察",
                       "urgency": "低", "shares": 0, "reason": hold_reason}
        return {
            "has_position": True, "cost_basis": round(float(cost_basis), 2),
            "pnl_pct": round(pnl_pct, 2), "r_multiple": round(r_multiple, 2),
            "primary": primary, "levels": levels, "options": options,
            "headline": (
                f"已持仓{f' {shares} 股' if shares else ''} → {primary['side']}·{primary['action']}"
            ),
            "entry_signal": status,
            "entry_signal_note": (
                "入场信号成立——已持仓时这条只作为“是否追加”的前提之一，不构成再次建仓的理由"
                if status == "ACTIONABLE_SIGNAL" else
                "入场信号未成立——不影响已持仓的止损/止盈纪律，只意味着现在不适合追加"
            ),
            "structure_warning": bool(structure_warning),
            "structure_failed": bool(structure_failed),
            "shares": shares,
            "position_value": round(position_value, 2) if shares else None,
            "pnl_amount": round(pnl_amount, 2) if shares else None,
            "current_risk": round(current_risk, 2) if shares else None,
            "risk_budget": round(max_risk, 2),
            "risk_over_budget": bool(risk_over_budget),
            "risk_utilization_pct": round(current_risk / max_risk * 100, 1) if shares and max_risk else None,
            "trim_shares": trim_shares,
            "reduce_shares": reduce_shares,
            "half_shares": half_shares,
            "add_on_shares": add_on_shares,
            "add_on_basis": (
                f"每股风险取 max(价格-有效止损, 1R)=${add_on_risk:.2f}；"
                f"剩余风险预算 ${remaining_risk:.0f} 允许 {risk_capped} 股、"
                f"可用资金 ${free_capital:.0f} 允许 {capital_capped} 股，取小者。"
            ),
            "risk_basis": (
                f"持仓风险 = {shares} 股 × 到有效止损 ${levels['effective_stop']} 的距离 "
                f"${stop_distance:.2f} = ${current_risk:.2f}，预算 = "
                f"${capital:.0f} × {risk_pct:.2%} = ${max_risk:.2f}。"
            ) if shares else "未提供 stock_num，无法计算持仓风险与具体减仓股数。",
            "note": "cost_basis 按每股平均成本理解；补充 entry_date 可让吊灯止损更准确。",
        }

    def _market_context(self, df: pd.DataFrame, atr: float) -> dict:
        """盘面上下文：日内价格行为、流动性、量价配合、成交量分布、均线距离。"""
        curr, prev = df.iloc[-1], df.iloc[-2]
        close, prev_close = float(curr["close"]), float(prev["close"])
        change_pct = (close / prev_close - 1) * 100 if prev_close else None
        has_volume = "volume" in df and float(df["volume"].tail(20).mean()) > 0
        volume = float(curr["volume"]) if has_volume else None
        volume_sma_20 = float(df["volume"].tail(20).mean()) if has_volume else None
        rvol = volume / volume_sma_20 if has_volume and volume_sma_20 else None
        heavy = rvol is not None and rvol >= 1.2
        light = rvol is not None and rvol <= 0.8
        rising = change_pct is not None and change_pct > 0
        # 恐慌抛盘定义为：放量下跌且单日跌幅达到 1 倍 ATR 以上
        panic = bool(not rising and heavy and abs(close - prev_close) >= atr)
        if rvol is None:
            flow = "无成交量数据"
        elif rising and heavy:
            flow = "放量上涨（买盘主动）"
        elif rising and light:
            flow = "缩量上涨（追价意愿不足）"
        elif rising:
            flow = "温和上涨"
        elif panic:
            flow = "放量下跌（恐慌抛压）"
        elif heavy:
            flow = "放量下跌（抛压释放）"
        elif light:
            flow = "缩量调整（无恐慌抛盘）"
        else:
            flow = "温和回落"
        sma_20, sma_50 = float(curr["SMA_20"]), float(curr["SMA_50"])
        lookback = self.config.get("zone_lookback", 60)
        profile_window = df.tail(lookback)
        volume_profile = self._volume_profile(profile_window)
        if volume_profile:
            volume_profile["position"] = (
                "价值区间上方（偏离成交密集带）" if close > volume_profile["value_area_high"]
                else "价值区间下方（偏离成交密集带）" if close < volume_profile["value_area_low"]
                else "价值区间内（成交密集带）"
            )
        return {
            "price_action": {
                "close": round(close, 2),
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "day_high": round(float(curr["high"]), 2),
                "day_low": round(float(curr["low"]), 2),
                "day_range_atr": round((float(curr["high"]) - float(curr["low"])) / atr, 2) if atr else None,
            },
            "liquidity": {
                "volume": int(volume) if volume else None,
                "volume_sma_20": int(volume_sma_20) if volume_sma_20 else None,
                "rvol": round(rvol, 2) if rvol is not None else None,
            },
            "supply_demand": {"flow": flow, "panic_selling": panic,
                              "rule": "相对成交量≥1.2 记放量、≤0.8 记缩量；放量下跌且跌幅≥1×ATR 记恐慌"},
            "volume_profile": volume_profile,
            "trend": {
                "distance_to_sma20_pct": round((close / sma_20 - 1) * 100, 2) if sma_20 else None,
                "distance_to_sma50_pct": round((close / sma_50 - 1) * 100, 2) if sma_50 else None,
                "sma_20": round(sma_20, 2),
                "sma_50": round(sma_50, 2),
            },
            "volatility": {
                "atr": round(atr, 2),
                "atr_pct": round(atr / close * 100, 2) if close else None,
            },
        }

    def _trend_summary(self, df: pd.DataFrame) -> dict:
        close = df["close"].astype(float)
        start_price = float(close.iloc[0])
        end_price = float(close.iloc[-1])
        running_peak = close.cummax()
        drawdown = close / running_peak - 1
        daily_returns = close.pct_change().dropna()
        slope = np.polyfit(np.arange(len(close)), close, 1)[0] if len(close) > 1 else 0
        normalized_slope = slope * len(close) / start_price if start_price else 0
        trend = "上升" if normalized_slope > 0.08 else "下降" if normalized_slope < -0.08 else "震荡"
        step = max(1, len(df) // 120)
        series = [
            {"date": str(df.iloc[i]["date"]), "close": round(float(close.iloc[i]), 2)}
            for i in range(0, len(df), step)
        ]
        if series[-1]["date"] != str(df.iloc[-1]["date"]):
            series.append({"date": str(df.iloc[-1]["date"]), "close": round(end_price, 2)})
        return {
            "start_date": str(df.iloc[0]["date"]),
            "end_date": str(df.iloc[-1]["date"]),
            "observations": len(df),
            "start_price": round(start_price, 2),
            "end_price": round(end_price, 2),
            "change_pct": round((end_price / start_price - 1) * 100, 2) if start_price else None,
            "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
            "annualized_volatility_pct": round(float(daily_returns.std() * np.sqrt(252) * 100), 2),
            "trend": trend,
            "series": series,
        }

    def analyze(self, df: pd.DataFrame) -> dict:
        """
        对传入的历史 OHLCV 数据进行多维结构化分析
        """
        # 提取最新若干周期的数据
        df = df.copy().reset_index(drop=True)
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        ticker = self.config.get("ticker", "UNKNOWN")
        capital = self.config.get("total_capital", 10000.0)
        risk_pct = self.config.get("risk_pct", 0.01)
        min_rr = self.config.get("min_risk_reward", 1.5)
        profile_name, profile = self._profile()
        min_rr = self.config.get("min_risk_reward", profile["min_rr"])

        # ----------------------------------------------------
        # 1. Market Structure (市场结构与支撑阻力)
        # ----------------------------------------------------
        lookback = self.config.get("structure_lookback", 20)
        recent_data = df.tail(lookback)
        
        swing_high = recent_data['high'].max() if 'high' in df.columns else recent_data['close'].max()
        swing_low = recent_data['low'].min() if 'low' in df.columns else recent_data['close'].min()
        
        # 长期趋势与短期结构
        is_macro_uptrend = curr['SMA_20'] > curr['SMA_50']
        dist_to_support = curr['close'] - curr['SMA_50']
        
        # ----------------------------------------------------
        # 2. Price Action (价格行为与反转确认)
        # ----------------------------------------------------
        atr = float(curr["ATR_14"])
        # 检查是否处于连续下跌惯性中 (接飞刀检测)
        is_falling_knife = curr['close'] < prev['close'] and (df.iloc[-3]['close'] > prev['close'])
        
        # 多条件右侧止跌确认：单日上涨本身不再足以触发信号。
        prior_swing_low = float(df.iloc[-6:-1]["low"].min())
        prior_swing_high = float(df.iloc[-6:-1]["high"].max())
        recent_low = float(df.iloc[-3:]["low"].min())
        earlier_low = float(df.iloc[-8:-3]["low"].min())
        volume_available = "volume" in df.columns and float(df["volume"].tail(20).mean()) > 0
        volume_ratio = (
            float(curr["volume"]) / float(df["volume"].tail(20).mean())
            if volume_available else None
        )
        atr_series = df["ATR_14"].tail(21).dropna()
        atr_median = float(atr_series.iloc[:-1].median()) if len(atr_series) > 1 else float(atr)
        volatility_expanding = float(atr) > atr_median * 1.15 if atr_median > 0 else False
        price_action_checks = {
            "前低守住": float(curr["low"]) >= prior_swing_low * 0.995,
            "形成更高低点": recent_low > earlier_low,
            "突破前高": float(curr["close"]) > prior_swing_high,
            "成交量确认": volume_ratio is not None and volume_ratio >= 1.2,
            "RSI非弱势": 45 <= float(curr["RSI_14"]) <= 70,
            "SMA趋势确认": bool(is_macro_uptrend),
            "波动率未失控": not volatility_expanding,
        }
        reversal_score = sum(price_action_checks.values())
        has_reversal_confirmation = reversal_score >= 5

        # ----------------------------------------------------
        # 3. Timing & Entry (入场时机与价格设置)
        # ----------------------------------------------------
        current_price_zones = self._price_zones(df)
        zone_backtest = self._zone_backtest(df)
        safe_buy_price = self._safe_buy_price(df, float(atr), float(curr["close"]))
        
        # 推荐使用 Buy Stop (突破挂单) 而非 Limit (限价单)，防止继续下破
        # 触发价必须基于最新已收盘K线，用 iloc[-2] 会让挂单价天生迟一天。
        entry_price = curr['close'] * 1.002 if 'high' not in df.columns else curr['high'] + (0.1 * atr)
        stop_loss = entry_price - (profile["stop_atr"] * atr)
        risk_per_share = entry_price - stop_loss
        
        # 目标位由 profile 的 target_atr 决定，R:R 才会随策略档位变化；
        # 若用固定 1.5R，rr_ratio 恒为 1.5，min_rr 门槛将失去作用。
        tp1_price = entry_price + (profile["target_atr"] * atr)
        tp2_price = max(entry_price + (risk_per_share * 3.0), swing_high)
        
        rr_ratio = (tp1_price - entry_price) / risk_per_share if risk_per_share > 0 else 0
        # target_atr/stop_atr 在浮点下可能算成 1.4999999999999967（standard 档），
        # 直接和 min_rr 比会让该档永远无法触发信号。按 6 位小数归一后再比。
        rr_ratio_for_gate = round(rr_ratio, 6)

        # ----------------------------------------------------
        # 4. Risk & Position Sizing (风控与仓位)
        # ----------------------------------------------------
        max_risk_amount = capital * risk_pct
        suggested_shares = int(max_risk_amount // risk_per_share) if risk_per_share > 0 else 0
        total_position_val = suggested_shares * entry_price

        # ----------------------------------------------------
        # 5. 综合决策逻辑与理由说明
        # ----------------------------------------------------
        status = "NEUTRAL"
        reasons = []

        if not is_macro_uptrend:
            reasons.append("❌ 宏观结构偏弱：SMA_20 低于 SMA_50，缺乏多头趋势支撑。")
        else:
            reasons.append("✅ 宏观结构良好：SMA_20 > SMA_50，处于多头趋势框架内。")

        if is_falling_knife and not has_reversal_confirmation:
            reasons.append("⚠️ 价格行为警告：价格正处于单边回落惯性中，未出现右侧止跌/反转 K 线，禁止接飞刀。")
        elif has_reversal_confirmation:
            reasons.append(f"✅ 右侧止跌确认：{reversal_score}/7 项条件通过，买盘力量开始介入。")
        else:
            reasons.append(f"⚠️ 右侧止跌证据不足：{reversal_score}/7 项条件通过，不能仅凭单日上涨确认反转。")

        if rr_ratio_for_gate < min_rr:
            reasons.append(f"❌ 潜在盈亏比不足：当前预估 R:R 为 {rr_ratio:.2f}，低于最低要求 {min_rr}。")

        # 触发交易信号的必要条件
        if is_macro_uptrend and not (is_falling_knife and not has_reversal_confirmation) and rr_ratio_for_gate >= min_rr:
            status = "ACTIONABLE_SIGNAL"
        elif is_macro_uptrend and is_falling_knife:
            status = "WATCHLIST_SETUP" # 进入观察池，准备右侧挂单

        backtest_start_index = max(55, profile["horizon"] + 1)
        backtest_end_index = len(df) - profile["horizon"] - 1
        backtest_start_date = (
            str(df.iloc[backtest_start_index]["date"])
            if backtest_start_index < len(df) else "N/A"
        )
        backtest_end_date = (
            str(df.iloc[backtest_end_index]["date"])
            if backtest_end_index >= backtest_start_index else "N/A"
        )

        return {
            "ticker": ticker,
            "date": curr['date'],
            "status": status,
            "current_price": curr['close'],
            "macro_trend": "Bullish" if is_macro_uptrend else "Bearish",
            "strategy_profile": profile_name,
            "strategy_label": profile["label"],
            "action_plan": self._action_plan(
                df, float(atr), status, volatility_expanding,
                entry_price=float(entry_price), stop_loss=float(stop_loss),
                suggested_shares=int(suggested_shares),
            ),
            "market_context": self._market_context(df, float(atr)),
            "trend_summary": self._trend_summary(df),
            "price_zones": current_price_zones,
            "zone_backtest": zone_backtest,
            "zone_backtest_method": {
                "lookback_days": self.config.get("zone_lookback", 60),
                "event_start": str(df.iloc[max(self.config.get("zone_lookback", 60) + 5, 80)]["date"]),
                "event_end": str(df.iloc[-profile["horizon"] - 1]["date"]),
                "horizon_days": profile["horizon"],
                "rule": (
                    "区间由已确认的局部高低点集群或SMA20产生，局部点需等待右侧3根K线确认；"
                    "价格进入已建立区间且前一日不在区间才形成一次独立事件，连续停留不重复计数；"
                    "事件信号在收盘后产生，未来持有期内先触发目标或止损，禁止使用未来高低点定义历史区间。"
                ),
            },
            "market_environment": self.config.get("market_environment", {"regime": "数据不足"}),
            "price_action_diagnostics": {
                "score": reversal_score,
                "max_score": len(price_action_checks),
                "checks": price_action_checks,
                "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
                "atr_median": round(atr_median, 2),
                "volatility_expanding": volatility_expanding,
                "rule": "至少5/7项通过，且不能仅凭连续上涨认定右侧止跌。",
            },
            "swing_high": swing_high,
            "swing_low": swing_low,
            "entry_plan": {
                "order_type": "Buy Stop (突破买入挂单)",
                "trigger_price": round(entry_price, 2),
                "stop_loss": round(stop_loss, 2),
                "tp1_target": round(tp1_price, 2),
                "tp2_target": round(tp2_price, 2),
                "risk_reward_ratio": round(rr_ratio, 2)
                ,"calculation": (
                    f"触发价=最新收盘日高点+0.1×ATR；止损=触发价-{profile['stop_atr']}×ATR；"
                    f"TP1=触发价+{profile['target_atr']}×ATR（≈{profile['target_atr'] / profile['stop_atr']:.2f}R）；"
                    "TP2=max(触发价+3R, 20日区间高点)"
                )
            },
            "risk_management": {
                "max_risk_amount": round(max_risk_amount, 2),
                "suggested_shares": suggested_shares,
                "position_value": round(total_position_val, 2),
                "atr": round(atr, 2),
                "calculation": (
                    f"最大风险={capital:.2f}×{risk_pct:.2%}；"
                    "建议股数=floor(最大风险/每股风险)"
                )
            },
            "probability_center_price": round(safe_buy_price, 2),
            "probability_method": {
                "history_start": str(df.iloc[0]["date"]),
                "history_end": str(df.iloc[-1]["date"]),
                "event_start": backtest_start_date,
                "event_end": backtest_end_date,
                "horizon_days": profile["horizon"],
                "stop_atr": profile["stop_atr"],
                "target_atr": profile["target_atr"],
                "step_atr": "非对称历史分位档位",
                "current_offsets_atr": self._asymmetric_grid(df.iloc[:-1], float(atr)),
                "center_formula": "20日低点+0.25×ATR、SMA20、当前收盘-0.5×ATR 三者中位数",
                "entry_rule": "高于事件日收盘按突破单等待最高价触发；低于收盘按限价单等待最低价触发",
                "outcome_rule": "成交后在持有期内先到目标记胜、先到止损记负；同日同时触发按负处理",
                "win_rate_formula": "胜率=胜/（胜+负）；未成交与到期未决不进入胜率分母",
                "grid_rule": "向下与向上分别使用过去252日真实日内偏离/ATR的75%和90%分位，不再使用对称固定网格",
            },
            "probability_ladder": self._probability_ladder(df, float(atr), safe_buy_price),
            "reasons": reasons
        }

def format_diagnostic_report(res: dict) -> str:
    """生成高级结构化诊断报告"""
    p = res["entry_plan"]
    r = res["risk_management"]
    
    status_symbol = "🚨 【交易动作触发】" if res["status"] == "ACTIONABLE_SIGNAL" else "👀 【观察区/等待确认】"
    
    report = f"""
============================================================
标的名称: {res['ticker']} | 分析日期: {res['date']}
决策状态: {status_symbol}
============================================================

1️⃣ 市场结构与价格行为 (Market Structure & Price Action)
------------------------------------------------------------
* 宏观趋势: {res['macro_trend']} (SMA_20 vs SMA_50)
* 近期区间阻力位 (Swing High): ${res['swing_high']:.2f}
* 近期区间支撑位 (Swing Low):  ${res['swing_low']:.2f}
* 当前收盘价: ${res['current_price']:.2f} (日内 ATR 波动幅度: ${r['atr']:.2f})

2️⃣ 诊断评估逻辑 (Trade Rationale)
------------------------------------------------------------
"""
    for reason in res['reasons']:
        report += f"{reason}\n"

    report += f"""
3️⃣ 执行计划与 Timing (Timing & Execution Setup)
------------------------------------------------------------
* 推荐挂单类型: {p['order_type']}
* 触发条件价 (Entry Trigger): ${p['trigger_price']:.2f} (突破该价位才代表反转确认)
* 初始止损价 (Stop Loss):     ${p['stop_loss']:.2f}
* 第一止盈目标 (TP1):         ${p['tp1_target']:.2f}
* 第二止盈目标 (TP2 - 3.0R):  ${p['tp2_target']:.2f}
* 预估风险收益比 (R:R Ratio): 1 : {p['risk_reward_ratio']}
* 价位计算依据: {p['calculation']}

4️⃣ 风险与交易管理 (Risk & Trade Management)
------------------------------------------------------------
* 单笔最大允许亏损: ${r['max_risk_amount']:.2f}
* 建议建仓股数:     {r['suggested_shares']} 股 (约占账户资金 ${r['position_value']:.2f})
* 止盈/止损管理策略: 
  - 当价格达 TP1 (${p['tp1_target']:.2f}) 时，平仓 50% 并将止损提高至保本位 (${p['trigger_price']:.2f})。
  - 剩余 50% 仓位使用 2.0x ATR 进行移动止损跟踪，直至趋势终结。
============================================================
"""
    return report

# ==========================================================
# 模拟运行示例 (针对你的 MSFT 数据测试)
# ==========================================================
if __name__ == "__main__":
    # 配置信息：完全解耦，可按标的灵活定制
    msft_config = {
        "ticker": "MSFT",
        "total_capital": 20000.0,
        "risk_pct": 0.01,           # 1% 风险
        "structure_lookback": 20,   # 计算结构回溯周期
        "min_risk_reward": 1.5      # 盈亏比门槛
    }

    # 模拟你之前提供的连续 5 天 MSFT 数据
    msft_data = {
        'date': ['2026-09-02', '2026-09-03', '2026-09-04', '2026-09-08', '2026-09-09'],
        'open': [493.00, 497.00, 508.00, 498.00, 495.00],
        'high': [498.00, 510.12, 502.00, 496.00, 494.00],
        'low':  [490.00, 496.00, 497.00, 491.00, 490.00],
        'close': [496.82, 510.12, 499.70, 493.95, 491.65],
        'SMA_20': [495.2110, 495.7240, 495.7095, 495.1040, 494.4960],
        'SMA_50': [438.3416, 441.2348, 444.1722, 446.5918, 449.0534],
        'RSI_14': [49.95, 58.70, 62.10, 57.29, 54.36],
        'ATR_14': [9.57, 10.48, 10.09, 10.27, 9.88]
    }
    df_msft = pd.DataFrame(msft_data)

    engine = ProfessionalStrategyEngine(msft_config)
    analysis_result = engine.analyze(df_msft)
    print(format_diagnostic_report(analysis_result))
