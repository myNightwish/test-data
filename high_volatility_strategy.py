import pandas as pd
import numpy as np

class HighVolatilityStrategyEngine:
    """
    专门针对高波动标的/妖股的动能突破与严格风控策略
    """
    def __init__(self, config: dict):
        self.config = config

    def analyze(self, df: pd.DataFrame) -> dict:
        df = df.copy().reset_index(drop=True)
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        ticker = self.config.get("ticker", "UNKNOWN")
        capital = self.config.get("total_capital", 10000.0)
        
        # 高波动标的建议降低单笔风险至 0.5%
        risk_pct = self.config.get("risk_pct", 0.005) 
        atr_mult = self.config.get("atr_stop_multiplier", 2.5) # 宽止损倍数
        vol_factor = self.config.get("volume_spike_factor", 1.5) # 放量倍数

        # ----------------------------------------------------
        # 1. 波动率属性校验 (ATR%)
        # ----------------------------------------------------
        atr = curr['ATR_14']
        close_price = curr['close']
        atr_pct = (atr / close_price) * 100 # ATR 占股价百分比
        is_high_vol = atr_pct >= 3.0 # 日均波幅 > 3% 视为高波动标的

        # ----------------------------------------------------
        # 2. 动能突破与成交量信号 (Price & Volume Momentum)
        # ----------------------------------------------------
        lookback = 10
        prev_10_high = df['high'].iloc[-(lookback+1):-1].max() if 'high' in df.columns else df['close'].iloc[-(lookback+1):-1].max()
        
        # 条件 A: 突破近 10 日高点
        is_breakout = curr['close'] > prev_10_high
        
        # 条件 B: 放量确认 (成交量 > 20日均量 * 1.5)
        volume_sma = curr.get('Volume_SMA_20', curr.get('volume', 1))
        curr_volume = curr.get('volume', 1)
        is_volume_spiked = curr_volume >= (volume_sma * vol_factor)

        # ----------------------------------------------------
        # 3. 宽幅风控与仓位计算
        # ----------------------------------------------------
        entry_price = curr['close']
        stop_loss = entry_price - (atr * atr_mult) # 2.5x - 3.0x ATR 宽止损
        risk_per_share = entry_price - stop_loss

        max_risk_amount = capital * risk_pct
        suggested_shares = int(max_risk_amount // risk_per_share) if risk_per_share > 0 else 0
        total_position_val = suggested_shares * entry_price

        # 止盈计划：1.0R 快速锁利润，2.0R 第二目标
        tp1_price = entry_price + risk_per_share
        tp2_price = entry_price + (risk_per_share * 2.0)

        # ----------------------------------------------------
        # 4. 诊断决策逻辑
        # ----------------------------------------------------
        reasons = []
        status = "NEUTRAL"

        if is_high_vol:
            reasons.append(f"🔥 标的波幅特征：日均波幅高达 {atr_pct:.2f}%，符合高波动/妖股特征。")
        else:
            reasons.append(f"ℹ️ 标的波幅特征：日均波幅 {atr_pct:.2f}%，属于中低波动，建议使用普通趋势策略。")

        if is_breakout:
            reasons.append(f"🚀 动能突破：收盘价 ${close_price:.2f} 突破前 {lookback} 日最高价 ${prev_10_high:.2f}。")
        else:
            reasons.append(f"⏳ 尚无突破：价格未创近 {lookback} 日新高 (${prev_10_high:.2f})，继续观察。")

        if is_volume_spiked:
            reasons.append(f"📊 成交量爆发：当前成交量达到 20 日均量的 {curr_volume/volume_sma:.1f} 倍，资金介入明显。")
        else:
            reasons.append(f"⚠️ 缩量突破警告：成交量未达均量 {vol_factor} 倍，谨防假突破。")

        # 触发条件：高波动 + 放量 + 突破
        if is_breakout and is_volume_spiked:
            status = "ACTIONABLE_SIGNAL"

        return {
            "ticker": ticker,
            "date": curr['date'],
            "status": status,
            "current_price": close_price,
            "atr_pct": round(atr_pct, 2),
            "entry_plan": {
                "order_type": "Market/Limit (突破放量即入场)",
                "entry_price": round(entry_price, 2),
                "stop_loss": round(stop_loss, 2),
                "tp1_quick_target": round(tp1_price, 2),
                "tp2_target": round(tp2_price, 2),
            },
            "risk_management": {
                "max_risk_amount": round(max_risk_amount, 2),
                "suggested_shares": suggested_shares,
                "position_value": round(total_position_val, 2),
                "atr": round(atr, 2),
                "atr_mult": atr_mult
            },
            "reasons": reasons
        }

def format_high_vol_report(res: dict) -> str:
    p = res["entry_plan"]
    r = res["risk_management"]
    status_symbol = "⚡ 【高波动突破信号】" if res["status"] == "ACTIONABLE_SIGNAL" else "👀 【观望/未达爆发条件】"

    report = f"""
============================================================
高波动标的诊断报告: {res['ticker']} | 日期: {res['date']}
决策状态: {status_symbol}
============================================================

1️⃣ 动能与量价特征 (Momentum & Volume)
------------------------------------------------------------
* 当前股价: ${res['current_price']:.2f}
* 日均 ATR 波幅比例: {res['atr_pct']}% (单日波动幅度约为 ${r['atr']:.2f})

2️⃣ 评估逻辑判断 (Rationale)
------------------------------------------------------------
"""
    for reason in res['reasons']:
        report += f"{reason}\n"

    report += f"""
3️⃣ 交易执行与快进快出计划 (Execution & Quick TP)
------------------------------------------------------------
* 入场参考价: ${p['entry_price']:.2f}
* 宽幅止损价: ${p['stop_loss']:.2f} (采用 {r['atr_mult']}x ATR 防洗盘)
* 快速第一止盈位 (1.0R): ${p['tp1_quick_target']:.2f} (建议达到后平仓 50% 并拉保本)
* 第二止盈目标 (2.0R):   ${p['tp2_target']:.2f}

4️⃣ 严格风控与时间止损规则 (Time & Risk Management)
------------------------------------------------------------
* 单笔最大允许风险 (0.5% 资金): ${r['max_risk_amount']:.2f}
* 建议控制建仓股数:             {r['suggested_shares']} 股 (仓位金额: ${r['position_value']:.2f})
* 🚨 特殊退出规则 (时间止损):
  - 入场后若 2 个交易日内未突破出新高，说明动能衰竭，无条件市价平仓。
============================================================
"""
    return report

# ==========================================================
# 测试示例：模拟 TSLA 巨量突破数据
# ==========================================================
if __name__ == "__main__":
    tsla_config = {
        "ticker": "TSLA",
        "total_capital": 20000.0,
        "risk_pct": 0.005,             # 高波动仅提 0.5% 账户风险
        "atr_stop_multiplier": 2.5,    # 2.5x ATR 宽止损
        "volume_spike_factor": 1.5     # 要求 1.5 倍放量
    }

    # 模拟 TSLA 突破数据
    tsla_data = {
        'date': ['2026-09-09'],
        'close': [250.00],
        'high': [252.00],
        'low': [235.00],
        'volume': [150000000],          # 当日 1.5 亿成交量
        'Volume_SMA_20': [80000000],    # 均量 8000 万 (显著放量)
        'ATR_14': [12.50]               # 日均波动 $12.5 (ATR% = 5%)
    }
    
    # 假设前 10 日最高价为 242.00
    df_tsla = pd.DataFrame(tsla_data)
    df_tsla['high_10d_prev'] = 242.00 

    engine = HighVolatilityStrategyEngine(tsla_config)
    
    # 模拟测试
    res = engine.analyze(df_tsla)
    print(format_high_vol_report(res))