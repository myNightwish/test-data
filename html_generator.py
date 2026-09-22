import os
import webbrowser

# 区间事件研究的有效样本门槛：低于此值不显示胜率与均值，避免 1胜3负 被读成 25%。
MIN_ZONE_RESOLVED = 10


def _money(value):
    if not isinstance(value, (int, float)) or value == 0:
        return "N/A"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e9:
        return f"{sign}${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{sign}${value / 1e6:.1f}M"
    return f"{sign}${value:,.0f}"


def _percent(value):
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "N/A"


def _decimal(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else "N/A"


def _trend_svg(summary):
    series = summary.get("series", [])
    if len(series) < 2:
        return "<div class='empty-chart'>暂无足够行情数据</div>"
    values = [point["close"] for point in series]
    low, high = min(values), max(values)
    span = high - low or 1
    left, top, width, height = 78, 18, 900, 220
    points = " ".join(
        f"{left + i / (len(values) - 1) * width:.1f},{top + height - (value - low) / span * height:.1f}"
        for i, value in enumerate(values)
    )
    point_nodes = "".join(
        f"<circle class='trend-point' cx='{left + i / (len(values) - 1) * width:.1f}' "
        f"cy='{top + height - (point['close'] - low) / span * height:.1f}' r='8' "
        f"data-date='{point['date']}' data-close='{point['close']:.2f}'/>"
        for i, point in enumerate(series)
    )
    date_indices = [0, len(series) // 2, len(series) - 1]
    date_labels = "".join(
        f"<text x='{left + i / (len(values) - 1) * width:.1f}' y='263' text-anchor='middle'>{series[i]['date']}</text>"
        for i in date_indices
    )
    price_labels = "".join(
        f"<text x='68' y='{top + height - fraction * height + 5:.1f}' text-anchor='end'>{low + span * fraction:.2f}</text>"
        for fraction in (0, 0.25, 0.5, 0.75, 1)
    )
    grid = "".join(
        f"<line x1='{left}' y1='{top + height - fraction * height}' x2='{left + width}' y2='{top + height - fraction * height}'/>"
        for fraction in (0, 0.25, 0.5, 0.75, 1)
    )
    return f"""<div class="trend-chart-wrap">
        <svg class="trend-chart" viewBox="0 0 1000 285" role="img" aria-label="价格趋势图">
            {grid}
            <line class="axis-line" x1="{left}" y1="{top}" x2="{left}" y2="{top + height}"/>
            <line class="axis-line" x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}"/>
            <polyline points="{points}"/>{point_nodes}{date_labels}{price_labels}
            <text class="axis-title" x='528' y='282' text-anchor='middle'>日期</text>
            <text class="axis-title" x='18' y='128' transform='rotate(-90 18 128)' text-anchor='middle'>价格（美元）</text>
        </svg>
        <div class="trend-tooltip" role="status"></div>
    </div>"""


def _quarterly_svg(history):
    metrics = [
        ("revenue", "营收", "#38bdf8"),
        ("net_income", "净利润", "#34d399"),
        ("free_cash_flow", "自由现金流", "#fbbf24"),
    ]
    available = [
        (key, label, color, history.get(key, []))
        for key, label, color in metrics
        if len(history.get(key, [])) >= 2
    ]
    if not available:
        return "<div class='empty-chart'>暂无足够季度财报数据</div>"
    left, width = 105, 860
    panel_height, panel_gap = 88, 38
    chart_parts = []
    first_values = sorted(available[0][3], key=lambda item: item["period"])
    periods = [item["period"][:10] for item in first_values]

    for panel_index, (_, label, color, raw_values) in enumerate(available):
        values = sorted(raw_values, key=lambda item: item["period"])
        low = min(item["value"] for item in values)
        high = max(item["value"] for item in values)
        padding = max((high - low) * 0.12, abs(high) * 0.03, 1)
        axis_low, axis_high = low - padding, high + padding
        span = axis_high - axis_low or 1
        top = 18 + panel_index * (panel_height + panel_gap)
        points = " ".join(
            f"{left + i / (len(values) - 1) * width:.1f},"
            f"{top + panel_height - (item['value'] - axis_low) / span * panel_height:.1f}"
            for i, item in enumerate(values)
        )
        circles = "".join(
            f"<circle cx='{left + i / (len(values) - 1) * width:.1f}' "
            f"cy='{top + panel_height - (item['value'] - axis_low) / span * panel_height:.1f}' "
            f"r='5' fill='{color}'><title>{label} · {item['period'][:10]} · {_money(item['value'])}</title></circle>"
            for i, item in enumerate(values)
        )
        chart_parts.append(
            f"<text class='quarterly-series-label' x='12' y='{top + 18}' fill='{color}'>{label}</text>"
            f"<line x1='{left}' y1='{top}' x2='{left + width}' y2='{top}'/>"
            f"<line x1='{left}' y1='{top + panel_height}' x2='{left + width}' y2='{top + panel_height}'/>"
            f"<text x='{left - 10}' y='{top + 5}' text-anchor='end'>{_money(axis_high)}</text>"
            f"<text x='{left - 10}' y='{top + panel_height + 5}' text-anchor='end'>{_money(axis_low)}</text>"
            f"<polyline points='{points}' stroke='{color}'/>{circles}"
        )

    bottom = 18 + len(available) * (panel_height + panel_gap)
    labels = "".join(
        f"<text x='{left + i / (len(periods) - 1) * width:.1f}' y='{bottom}' text-anchor='middle'>{period}</text>"
        for i, period in enumerate(periods)
    )
    return f"""<svg class="quarterly-chart" viewBox="0 0 1000 {bottom + 22}" role="img" aria-label="季度财报趋势图">
        {''.join(chart_parts)}{labels}
        <text class="axis-title" x="535" y="{bottom + 20}" text-anchor="middle">报告期</text>
        </svg>"""


def generate_pro_html_dashboard(
    reports: list,
    output_file: str = "dashboard.html",
    open_browser: bool = True,
):
    """
    生成高密度、专业级的多维策略与基本面分析看板
    """
    STATUS_MAP = {
        "ACTIONABLE_SIGNAL": {"badge": "🚨 入场信号成立", "bg": "#064e3b", "border": "#10b981", "text": "#34d399"},
        "WATCHLIST_SETUP":   {"badge": "👀 入场观察区 / 等待确认", "bg": "#78350f", "border": "#f59e0b", "text": "#fbbf24"},
        "NEUTRAL":           {"badge": "➖ 入场信号未达阈值", "bg": "#1f2937", "border": "#4b5563", "text": "#9ca3af"}
    }

    cards_html = ""
    for r in reports:
        status_info = dict(STATUS_MAP.get(r['status'], STATUS_MAP["NEUTRAL"]))
        if (r.get("action_plan") or {}).get("has_position"):
            # 已持仓时入场信号只用于判断能否追加，标签必须说清楚，否则会和持仓动作打架。
            status_info["badge"] += "（仅判断能否追加）"
        p = r.get('entry_plan', {})
        rm = r.get('risk_management', {})
        f = r.get('fundamentals', {}) # 基本面数据
        financials = f.get('financials', {})
        assessment = f.get('assessment', {})
        trend = r.get('trend_summary', {})
        price_zones = r.get('price_zones', [])
        zone_backtest = r.get('zone_backtest', [])
        zone_method = r.get('zone_backtest_method', {})
        market_environment = r.get('market_environment', {})
        asset_profile = r.get('asset_profile', {})
        daily_research = r.get('daily_research', {})
        research_context = daily_research.get('current_context', {})
        research_method = daily_research.get('method', {})
        research_stats = daily_research.get('statistics', [])
        group_ranges = research_context.get('group_ranges', {})
        quarterly_history = f.get('financial_history', {})
        financial_states = f.get('financial_states', [])
        price_action = r.get('price_action_diagnostics', {})
        rule_rows = "".join(
            f"<tr><td>{rule['name']}</td><td>{'通过' if rule['passed'] else '未通过'}</td>"
            f"<td>{rule['threshold']}</td></tr>"
            for rule in assessment.get("rules", [])
        )
        
        # 格式化数值显示
        mcap_str = f"${f.get('market_cap', 0) / 1e9:.2f}B" if f.get('market_cap', 0) else "N/A"
        rev_str = f"${f.get('total_revenue', 0) / 1e9:.2f}B" if f.get('total_revenue', 0) else "N/A"
        rev_growth_str = f"{f.get('revenue_growth', 0)*100:.1f}%" if isinstance(f.get('revenue_growth'), (int, float)) else "N/A"
        margin_str = f"{f.get('profit_margins', 0)*100:.1f}%" if isinstance(f.get('profit_margins'), (int, float)) else "N/A"
        roe_str = f"{f.get('roe', 0)*100:.1f}%" if isinstance(f.get('roe'), (int, float)) else "N/A"
        pe_str = f"{f.get('trailing_pe', 'N/A')}" if f.get('trailing_pe') != 'N/A' else "N/A"
        current_regime = market_environment.get("regime", "数据不足")
        zone_by_name = {zone["name"]: zone for zone in price_zones}
        zone_rows_by_name = {}
        for row in zone_backtest:
            zone_rows_by_name.setdefault(row["name"], []).append(row)

        market_context = r.get("market_context", {})
        ctx_price = market_context.get("price_action", {})
        ctx_liquidity = market_context.get("liquidity", {})
        ctx_flow = market_context.get("supply_demand", {})
        ctx_trend = market_context.get("trend", {})
        ctx_vol = market_context.get("volatility", {})
        ctx_profile = market_context.get("volume_profile") or {}

        def _signed(value, suffix="%"):
            if not isinstance(value, (int, float)):
                return "N/A"
            return f'<span class="{"green" if value >= 0 else "red"}">{value:+.2f}{suffix}</span>'

        if ctx_profile:
            profile_html = (
                f'<div class="context-row"><span>成交量分布 (VPOC)</span>'
                f'<strong>${ctx_profile.get("vpoc")}</strong></div>'
                f'<div class="context-row"><span>价值区间 (70% 成交量)</span>'
                f'<strong>${ctx_profile.get("value_area_low")} - ${ctx_profile.get("value_area_high")}</strong></div>'
                f'<div class="context-row"><span>当前价位置</span>'
                f'<strong>{ctx_profile.get("position", "N/A")}</strong></div>'
            )
            profile_note = f'成交量分布口径：{ctx_profile.get("basis", "N/A")}，实际覆盖 {ctx_profile.get("coverage_pct")}%。'
        else:
            profile_html = '<div class="context-row"><span>成交量分布</span><strong>无成交量数据</strong></div>'
            profile_note = "该标的无可用成交量，成交量分布与量价配合不参与判断。"
        context_panel_html = (
            f"""<div class="context-panel">
                <div class="section-title">🔬 盘面与流动性指标</div>
                <div class="context-grid">
                    <div class="context-row"><span>收盘 / 日内涨跌</span><strong>${ctx_price.get('close', 'N/A')} · {_signed(ctx_price.get('change_pct'))}</strong></div>
                    <div class="context-row"><span>当日高 / 低</span><strong>${ctx_price.get('day_high', 'N/A')} - ${ctx_price.get('day_low', 'N/A')}（{ctx_price.get('day_range_atr', 'N/A')} ATR）</strong></div>
                    <div class="context-row"><span>当日成交量</span><strong>{ctx_liquidity.get('volume') or 'N/A'}</strong></div>
                    <div class="context-row"><span>相对成交量 (RVOL)</span><strong>{ctx_liquidity.get('rvol', 'N/A')}x · 20日均量 {ctx_liquidity.get('volume_sma_20') or 'N/A'}</strong></div>
                    <div class="context-row"><span>量价配合</span><strong class="{'red' if ctx_flow.get('panic_selling') else ''}">{ctx_flow.get('flow', 'N/A')}</strong></div>
                    <div class="context-row"><span>恐慌抛盘</span><strong class="{'red' if ctx_flow.get('panic_selling') else ''}">{'是' if ctx_flow.get('panic_selling') else '否'}</strong></div>
                    {profile_html}
                    <div class="context-row"><span>距 SMA20</span><strong>{_signed(ctx_trend.get('distance_to_sma20_pct'))} （${ctx_trend.get('sma_20', 'N/A')}）</strong></div>
                    <div class="context-row"><span>距 SMA50</span><strong>{_signed(ctx_trend.get('distance_to_sma50_pct'))} （${ctx_trend.get('sma_50', 'N/A')}）</strong></div>
                    <div class="context-row"><span>ATR / ATR占价格比</span><strong>${ctx_vol.get('atr', 'N/A')} · {ctx_vol.get('atr_pct', 'N/A')}%</strong></div>
                </div>
                <div class="calculation-note context-note">{ctx_flow.get('rule', '')}<br>{profile_note}</div>
            </div>"""
            if market_context else ""
        )

        action_plan = r.get("action_plan", {})
        action_primary = action_plan.get("primary", {})
        action_levels = action_plan.get("levels") or {}
        side_class = {"买入": "side-buy", "卖出": "side-sell",
                      "持有": "side-hold", "观望": "side-wait"}
        option_chips = "".join(
            f'<span class="action-chip {"chip-on" if option.get("applicable") else "chip-off"}">'
            f'{"✓" if option.get("applicable") else "✗"} {option.get("action", "")}'
            f'{f" · {option['shares']} 股" if option.get("shares") else ""}'
            f'<small>{option.get("reason", "")}</small></span>'
            for option in action_plan.get("options", [])
        )
        def _level_chip_class(line):
            if not line.get("breached"):
                return ""
            # 结构参考不是止损，跌破只是回调预警，不能用"已跌破"的红色当止损处理。
            return "level-warning" if line.get("kind") == "structure" else "level-breached"

        def _level_chip_tag(line):
            if not line.get("breached"):
                return ""
            return " · 回调预警" if line.get("kind") == "structure" else " · 已跌破"

        level_chips = "".join(
            f'<span class="level-chip {_level_chip_class(line)}">'
            f'{line["name"]} ${line["level"]}'
            f'{_level_chip_tag(line)}<small>{line.get("basis", "")}</small></span>'
            for line in action_levels.get("lines", [])
        )
        if action_plan.get("has_position"):
            shares_held = action_plan.get("shares") or 0
            risk_used = action_plan.get("risk_utilization_pct")
            risk_class = "red" if action_plan.get("risk_over_budget") else "green"
            holding_line = (
                f'持仓 {shares_held} 股 · 成本 ${action_plan.get("cost_basis")} · 市值 '
                f'${action_plan.get("position_value") if shares_held else "N/A"} · 浮动盈亏 '
                f'{action_plan.get("pnl_pct")}%'
                f'（${action_plan.get("pnl_amount") if shares_held else "N/A"}）'
                f' · {action_plan.get("r_multiple")}R'
                if shares_held else
                f'成本 ${action_plan.get("cost_basis")} · 浮动盈亏 {action_plan.get("pnl_pct")}%'
                f' · {action_plan.get("r_multiple")}R · 未提供 stock_num'
            )
            risk_line = (
                f'持仓风险 <strong class="{risk_class}">${action_plan.get("current_risk")}</strong>'
                f' / 预算 ${action_plan.get("risk_budget")} = '
                f'<strong class="{risk_class}">{risk_used}%</strong>'
                if shares_held else f'单笔风险预算 ${action_plan.get("risk_budget")}'
            )
            position_summary = (
                f'{holding_line}<br>{risk_line} · 有效止损 ${action_levels.get("effective_stop")}'
                f'（{action_levels.get("effective_stop_name")}） · TP1 ${action_levels.get("tp1")}'
                f' · TP2 ${action_levels.get("tp2")}'
            )
        else:
            trigger_level = next(
                (line["level"] for line in action_levels.get("lines", [])
                 if line["name"] == "挂单触发价"),
                None,
            )
            position_summary = (
                f'当前空仓 · 单笔风险预算 ${action_plan.get("risk_budget")}<br>'
                f'挂单触发价 ${trigger_level} · 建仓后止损 ${action_levels.get("effective_stop")}'
                f' · TP1 ${action_levels.get("tp1")} · TP2 ${action_levels.get("tp2")}'
                if action_levels else
                "未配置 cost_basis：按空仓处理，只输出建仓侧判断。"
            )
        position_badge_html = (
            f'<span class="badge position-badge {side_class.get(action_primary.get("side"), "side-wait")}">'
            f'{"持仓动作" if action_plan.get("has_position") else "空仓动作"}：'
            f'{action_primary.get("side", "观望")}·{action_primary.get("action", "不操作")}'
            f'{f" {action_primary['shares']} 股" if action_primary.get("shares") else ""}</span>'
            if action_plan else ""
        )
        action_banner_html = (
            f"""<div class="action-banner">
                <div class="action-head">
                    <span class="action-side {side_class.get(action_primary.get("side"), "side-wait")}">
                        {action_primary.get("side", "观望")}
                    </span>
                    <strong class="action-name">{action_primary.get("action", "不操作")}</strong>
                    {f'<span class="action-shares">{action_primary["shares"]} 股</span>' if action_primary.get("shares") else ""}
                    <span class="action-urgency">紧急度：{action_primary.get("urgency", "无")}</span>
                </div>
                <div class="action-reason">{action_primary.get("reason", "")}</div>
                <div class="action-position">{position_summary}</div>
                <div class="action-chips">{option_chips}</div>
                <div class="action-chips">{level_chips}</div>
                <div class="action-foot">{action_plan.get("risk_basis", "")}<br>{action_plan.get("add_on_basis", "")}<br>{action_plan.get("note", "")}</div>
            </div>"""
            if action_plan else ""
        )

        def _zone_stat(rows):
            wins = sum(item.get("wins", 0) for item in rows)
            losses = sum(item.get("losses", 0) for item in rows)
            resolved = wins + losses
            candidates = sum(item.get("candidates", 0) for item in rows)
            triggered = sum(item.get("triggered", 0) for item in rows)
            timeouts = sum(item.get("timeouts", 0) for item in rows)
            untriggered = sum(item.get("untriggered", 0) for item in rows)
            return {
                "wins": wins,
                "losses": losses,
                "resolved": resolved,
                "candidates": candidates,
                "triggered": triggered,
                "timeouts": timeouts,
                "untriggered": untriggered,
                "win_rate": wins / resolved * 100 if resolved else None,
            }

        def _display_rate(stat):
            if stat["resolved"] == 0:
                return "无有效样本"
            # 与 quant_engine 的样本纪律对齐：有效样本不足时不给出伪精确胜率。
            if stat["resolved"] < MIN_ZONE_RESOLVED:
                return f"样本不足({stat['resolved']})"
            return f"{stat['win_rate']:.1f}%"

        def _display_avg(rows, resolved):
            if resolved < MIN_ZONE_RESOLVED:
                return "均值 样本不足"
            value = next(
                (item.get("avg_return_pct") for item in rows if item.get("avg_return_pct") is not None),
                None,
            )
            return f"均值 {value}%" if value is not None else "均值 N/A"

        unified_zone_rows = []
        for zone in price_zones:
            rows = zone_rows_by_name.get(zone["name"], [])
            current_rows = [row for row in rows if row.get("market_regime") == current_regime]
            current_stat = _zone_stat(current_rows)
            overall_stat = _zone_stat(rows)
            quality = "可参考" if overall_stat["resolved"] >= 30 else "样本不足"
            unified_zone_rows.append(
                f"""<tr>
                    <td><strong>{zone['name']}</strong><br><small>{zone['rationale']}</small></td>
                    <td>${zone['lower']:.2f} - ${zone['upper']:.2f}<br><small>中心 ${zone['center']:.2f} · {zone.get('distance_atr', 'N/A')} ATR</small></td>
                    <td>{zone.get('touches', 0)}</td>
                    <td><strong>{_display_rate(current_stat)}</strong><br><small>{current_stat['wins']}胜 / {current_stat['losses']}负 · 有效 {current_stat['resolved']} · 候选 {current_stat['candidates']}</small></td>
                    <td>{_display_rate(overall_stat)}<br><small>{overall_stat['wins']}胜 / {overall_stat['losses']}负 · 有效 {overall_stat['resolved']}</small><br><small>{_display_avg(rows, overall_stat['resolved'])}</small></td>
                    <td>{current_stat['triggered']} / {current_stat['candidates']}<br><small>未成交 {current_stat['untriggered']} · 未决 {current_stat['timeouts']}</small></td>
                    <td class="confidence">{quality}</td>
                </tr>"""
            )
        unified_zone_html = "".join(unified_zone_rows)
        financial_state_rows = "".join(
            f"<div class='financial-state'><span>{state['metric']}</span><strong class='{state['state']}'>{state['state']}</strong>"
            f"<small>{state.get('change_pct', 'N/A')}%</small></div>"
            for state in financial_states
        )
        research_periods = r.get("daily_research_periods", {"1年": daily_research})

        def _research_panel(label, research):
            context = research.get("current_context", {})
            method = research.get("method", {})
            ranges = context.get("group_ranges", {})

            def _metric(key, row, suffix=""):
                value = row.get(key)
                return f"{value}{suffix}" if value is not None else "样本不足"

            def _price_range(row):
                low, high = row.get("price_min"), row.get("price_max")
                if low is None or high is None:
                    return "N/A"
                return f"${low:.2f} - ${high:.2f}"

            rows_html = "".join(
                f"""<tr class="{'current-group' if row.get('current_group') else ''}">
                    <td>{str(row.get('price_group', '数据不足'))}{' ← 当前' if row.get('current_group') else ''}</td>
                    <td>{_price_range(row)}</td>
                    <td>{row.get('sample_count', 0)}</td>
                    <td>{_metric('win_rate_5d_pct', row, '%')}</td>
                    <td>{_metric('win_rate_10d_pct', row, '%')}</td>
                    <td>{_metric('win_rate_20d_pct', row, '%')}</td>
                    <td>{_metric('win_rate_40d_pct', row, '%')}</td>
                    <td>{_metric('alpha_win_rate_20d_pct', row, '%')}</td>
                    <td>{_metric('avg_return_20d_pct', row, '%')}</td>
                    <td>{_metric('profit_loss_ratio_20d', row)}</td>
                    <td>{_metric('expected_value_pct_20d', row, '%')}</td>
                    <td>{_metric('profit_factor_20d', row)}</td>
                    <td>{_metric('stop_hit_rate_20d_pct', row, '%')}</td></tr>"""
                for row in research.get("statistics", [])
            )
            requested_rows = method.get("requested_rows", "N/A")
            actual_rows = method.get("actual_rows", "N/A")
            data_status = method.get("data_status")
            if data_status is None:
                data_status = (
                    "完整" if isinstance(requested_rows, int)
                    and isinstance(actual_rows, int)
                    and actual_rows >= requested_rows
                    else "数据不足"
                )
            status_class = "data-complete" if data_status == "完整" else "data-insufficient"
            warning_html = (
                f"<div class=\"research-warning\">⚠️ 当前可用历史不足以代表完整{label}统计，结果仅反映实际可用数据。</div>"
                if data_status != "完整" else ""
            )
            return f"""<div class="research-period">
                <div class="research-period-title">{label}版本
                    <small>{method.get('research_data_start', 'N/A')} 至 {method.get('research_data_end', 'N/A')} · 请求 {requested_rows} 个交易日，实际 {actual_rows} 个交易日 · <span class="{status_class}">{data_status}</span></small>
                </div>
                {warning_html}
                <div class="strategy-note">当前价格 ${context.get('price', 'N/A')} · 所属 <strong>{context.get('price_group', 'N/A')}</strong>（{ranges.get(str(context.get('price_group', '')), {}).get('label', 'N/A')}，按最近 252 日分位边界） · 历史样本 {context.get('history_observations', 'N/A')} 个交易日</div>
                <table><thead><tr><th>价格区域</th><th>样本实际价格区间</th><th>样本数</th><th>5日胜率</th><th>10日胜率</th><th>20日胜率</th><th>40日胜率</th><th>20日超额胜率</th><th>20日收益</th><th>盈亏比</th><th>EV</th><th>利润因子</th><th>止损触及率</th></tr></thead>
                <tbody>{rows_html}</tbody></table>
            </div>"""

        research_panels_html = "".join(
            _research_panel(label, research)
            for label, research in research_periods.items()
        )

        reasons_html = "".join([f"<li>{reason}</li>" for reason in r.get('reasons', [])])
        diagnostic_rows = "".join(
            f"<div class='diagnostic-row'><span>{name}</span><strong class='{'pass' if passed else 'fail'}'>{'通过' if passed else '未通过'}</strong></div>"
            for name, passed in price_action.get("checks", {}).items()
        )
        financial_rating = assessment.get("rating", "数据不足")
        decision_action = action_plan.get("headline") or (
            "等待确认，不建议立即执行"
            if r.get("status") != "ACTIONABLE_SIGNAL"
            else "满足当前策略条件，可进入执行评估"
        )
        decision_reason = (
            f"{action_primary.get('reason', '')}<br>"
            f"{action_plan.get('entry_signal_note', '')}<br>"
            f"行情趋势：{r.get('macro_trend', '数据不足')}；"
            f"市场环境：{current_regime}；"
            f"财报质量：{financial_rating}；"
            f"当前价格区域：{research_context.get('price_group', '数据不足')}。"
        )
        
        cards_html += f"""
        <div class="card">
            <!-- 头部：标的名称与状态 -->
            <div class="card-header">
                <div class="title-area">
                    <span class="ticker">{r['ticker']}</span>
                    <span class="price">${r['current_price']:.2f}</span>
                    <span class="sector-tag">{asset_profile.get('category', f.get('sector', 'Technology'))}</span>
                    <span class="volatility-tag">{asset_profile.get('volatility_level', '波动属性未知')}</span>
                </div>
                <div class="badge-area">
                    {position_badge_html}
                    <span class="badge" style="background: {status_info['bg']}; color: {status_info['text']}; border: 1px solid {status_info['border']};">
                        {status_info['badge']}
                    </span>
                </div>
            </div>
            <nav class="side-nav" aria-label="标的分析导航">
                <div class="side-nav-title">分析导航</div>
                <a href="#decision">决策参考</a>
                <a href="#market">1、行情与量化</a>
                <a href="#trend">行情趋势</a>
                <a href="#zones">价格区间</a>
                <a href="#research">日样本统计</a>
                <a href="#fundamentals">2、公司基本面</a>
                <a href="#quarterly">季度财报</a>
            </nav>
            <div id="decision" class="decision-panel">
                <div class="decision-kicker">🧭 当前决策参考</div>
                <strong class="decision-action">{decision_action}</strong>
                <div class="decision-reason">{decision_reason}</div>
                <small>这是量化研究与结构化财报的综合参考，不是自动下单指令；优先查看下方三大模块的证据。</small>
            </div>

            <!-- 第一排：基本面与财报核心数据 -->
            <div id="fundamentals" class="module-title fundamentals-module-title">2、公司基本面与财报</div>
            <div class="section-title fundamentals-title">🏢 财报快照与报告期</div>
            <div class="metrics-grid fundamentals-grid fundamentals-metrics">
                <div class="metric-box">
                    <div class="label">市值 (Market Cap)</div>
                    <div class="value">{mcap_str}</div>
                </div>
                <div class="metric-box">
                    <div class="label">动态市盈率 (P/E)</div>
                    <div class="value">{pe_str}</div>
                </div>
                <div class="metric-box">
                    <div class="label">总营收 (Revenue)</div>
                    <div class="value">{rev_str}</div>
                </div>
                <div class="metric-box">
                    <div class="label">营收增速 (Growth)</div>
                    <div class="value green">{rev_growth_str}</div>
                </div>
                <div class="metric-box">
                    <div class="label">净利润率 (Margin)</div>
                    <div class="value">{margin_str}</div>
                </div>
                <div class="metric-box">
                    <div class="label">净资产收益率 (ROE)</div>
                    <div class="value">{roe_str}</div>
                </div>
            </div>
            <div id="market" class="module-title market-module-title">1、行情与量化统计</div>
            <div id="trend" class="trend-panel">
                <div class="section-title">📉 行情区间趋势</div>
                <div class="trend-meta">
                    <span>{trend.get('start_date', 'N/A')} 至 {trend.get('end_date', 'N/A')}</span>
                    <span>{trend.get('observations', 0)} 个交易日</span>
                    <strong>{trend.get('trend', 'N/A')} · 区间 {trend.get('change_pct', 'N/A')}%</strong>
                </div>
                {_trend_svg(trend)}
                <div class="trend-stats">起点 ${trend.get('start_price', 'N/A')} · 终点 ${trend.get('end_price', 'N/A')} · 最大回撤 {trend.get('max_drawdown_pct', 'N/A')}% · 年化波动 {trend.get('annualized_volatility_pct', 'N/A')}%</div>
            </div>
            {context_panel_html}
            <div id="zones" class="zones-panel">
                <div class="section-title">🧭 历史价格区间</div>
                <div class="strategy-note">基于最近 {zone_method.get('lookback_days', 'N/A')} 个交易日的局部高低点、价格反复触及和 SMA20 价值区生成；区间宽度按当期 ATR 自适应。</div>
                <div class="strategy-note">当前市场环境：{current_regime} · 当前环境胜率与全部环境胜率分开显示；无有效成交结论时不显示伪造百分比。</div>
                <table><thead><tr><th>区域</th><th>当前范围与位置</th><th>确认触及数</th><th>当前环境结果</th><th>全部环境结果</th><th>事件统计</th><th>样本等级</th></tr></thead>
                <tbody>{unified_zone_html}</tbody></table>
                <div class="backtest-method">
                    <strong>如何阅读</strong>
                    当前环境胜率只聚合 {current_regime} 事件；全部环境胜率聚合该区域的所有市场状态。
                    胜率只使用已成交且已经触发目标或止损的有效样本；“未成交”和“到期未决”不进入胜率分母。
                    有效样本少于 {MIN_ZONE_RESOLVED} 个时只显示样本数，不显示胜率与均值。
                    <br>{zone_method.get('rule', 'N/A')}
                </div>
            </div>
            <div class="market-panel">
                <div class="section-title">🌐 当前市场环境过滤</div>
                <div class="market-metrics"><strong>{market_environment.get('regime', '数据不足')}</strong>
                <span>纳指：{_decimal(market_environment.get('nasdaq_close'))}</span>
                <span>VIX：{_decimal(market_environment.get('vix_close'))}</span>
                <span>10年期美债：{_decimal(market_environment.get('ten_year_yield'))}</span></div>
            </div>
            <div id="research" class="research-panel">
                <div class="section-title">🧪 日样本历史统计（独立研究层）</div>
                <div class="strategy-note">1年看近期交易状态，3年看中期稳定性，5年看长期周期参考。三个版本独立计算，不混用样本。</div>
                {research_panels_html}
                <div class="backtest-method">
                    <strong>研究口径</strong>
                    各版本实际研究区间与交易日数量见对应面板；上市时间不足时按实际可用数据计算，不补造历史数据。
                    每个历史交易日作为一个样本；价格分组只使用此前 {research_method.get('lookback_days', 'N/A')} 个交易日；
                    未来 {', '.join(str(h) for h in research_method.get('horizons', []))} 日只用于生成标签。
                    胜率={research_method.get('win_rule', 'N/A')}；回撤={research_method.get('drawdown_rule', 'N/A')}。
                    <br>默认只按价格区域统计，市场环境交叉结果另存为独立批次；有效样本少于 {research_method.get('minimum_effective_samples', 30)} 时，收益质量指标显示“样本不足”；该表是条件统计，不等同于已执行交易策略的净收益回测。
                    <br>高亮行（← 当前）才是当前价所处的分组，其余行仅作对照，不要把它们的胜率当成当前位置的胜率。
                    <br>{research_method.get('price_range_rule', 'N/A')}。
                    <br>“20日超额胜率”={research_method.get('alpha_rule', 'N/A')}后为正的比例。单看绝对胜率无法区分价位优势与标的整体上涨漂移，趋势股的高位分组样本通常最多、绝对胜率也最高。
                </div>
            </div>
            <div class="financial-summary fundamentals-summary">
                <div><span>财报报告期</span><strong>{f.get('financial_period', 'N/A')}</strong></div>
                <div><span>财报类型</span><strong>{f.get('financial_period_type', 'N/A')}</strong></div>
                <div><span>财报质量评分</span><strong>{assessment.get('score', 'N/A')} / {assessment.get('max_score', 5)} ({assessment.get('rating', 'N/A')})</strong></div>
                <div><span>营业利润</span><strong>{_money(financials.get('operating_income'))}</strong></div>
                <div><span>净利润</span><strong>{_money(financials.get('net_income'))}</strong></div>
                <div><span>经营现金流</span><strong>{_money(financials.get('operating_cash_flow'))}</strong></div>
                <div><span>资本开支</span><strong>{_money(financials.get('capital_expenditure'))}</strong></div>
                <div><span>自由现金流</span><strong>{_money(financials.get('free_cash_flow'))}</strong></div>
                <div><span>净负债</span><strong>{_money(financials.get('net_debt'))}</strong></div>
                <div><span>营业利润率</span><strong>{_percent(financials.get('operating_margin'))}</strong></div>
                <div><span>现金流/净利润</span><strong>{_percent(financials.get('fcf_conversion'))}</strong></div>
            </div>
            <div class="assessment-panel fundamentals-assessment">
                <div class="section-title">🧾 财报质量评分规则</div>
                <table><thead><tr><th>检查项</th><th>结果</th><th>标准</th></tr></thead>
                <tbody>{rule_rows}</tbody></table>
            </div>
            <div id="quarterly" class="quarterly-panel fundamentals-quarterly">
                <div class="section-title">📊 季度财报变化</div>
                <div class="strategy-note">最新报告期：{f.get('financial_period', 'N/A')} · 最多请求 8 期，营收实际 {len(quarterly_history.get('revenue', []))} 期，EPS 实际 {len(quarterly_history.get('eps', []))} 期，自由现金流实际 {len(quarterly_history.get('free_cash_flow', []))} 期 · 金额按原始财报值展示</div>
                {_quarterly_svg(quarterly_history)}
            </div>
            <div class="financial-states-panel fundamentals-states">
                <div class="section-title">🧩 最新季度财报状态变化</div>
                <div class="financial-states">{financial_state_rows}</div>
                <div class="strategy-note">状态仅比较最近两个可用报告期，不代表持续改善趋势，未排除季节性影响。基本面为当前抓取快照，并非历史时点数据，不参与历史回测。</div>
            </div>

            <!-- 第二排：挂单与风控执行计划 -->
            <div class="section-title execution-title">🎯 执行计划与风控管理 (Execution &amp; Risk)</div>
            {action_banner_html}
            <div class="metrics-grid execution-grid execution-metrics">
                <div class="metric-box">
                    <div class="label">挂单触发价 (Entry)</div>
                    <div class="value accent">${p.get('trigger_price', p.get('entry_price', 0)):.2f}</div>
                </div>
                <div class="metric-box">
                    <div class="label">初始止损价 (Stop Loss)</div>
                    <div class="value red">${p.get('stop_loss', 0):.2f}</div>
                </div>
                <div class="metric-box">
                    <div class="label">第一止盈 (TP1)</div>
                    <div class="value">${p.get('tp1_target', p.get('tp1_quick_target', 0)):.2f}</div>
                </div>
                <div class="metric-box">
                    <div class="label">第二止盈 (TP2 - 3.0R)</div>
                    <div class="value">${p.get('tp2_target', 0):.2f}</div>
                </div>
                <div class="metric-box">
                    <div class="label">建议建仓股数</div>
                    <div class="value">{rm.get('suggested_shares', 0)} 股</div>
                </div>
                <div class="metric-box">
                    <div class="label">单笔风险敞口</div>
                    <div class="value">${rm.get('max_risk_amount', 0):.2f}</div>
                </div>
            </div>
            <div class="calculation-note">价格依据：{p.get('calculation', 'N/A')}<br>仓位依据：{rm.get('calculation', 'N/A')}</div>

            <!-- 第三排：指标与诊断逻辑 -->
            <div class="details-split">
                <div class="left-box">
                    <div class="section-title">📊 关键指标波动数据</div>
                    <div class="sub-metric-row"><span>RSI (14):</span> <strong>{r.get('rsi_14', 'N/A')}</strong></div>
                    <div class="sub-metric-row"><span>ATR (14波幅):</span> <strong>${rm.get('atr', 0):.2f}</strong></div>
                    <div class="sub-metric-row"><span>宏观趋势状态:</span> <strong>{r.get('macro_trend', 'N/A')}</strong></div>
                    <div class="sub-metric-row"><span>盈亏比 (R:R):</span> <strong>1 : {p.get('risk_reward_ratio', 1.5)}</strong></div>
                    <div class="sub-metric-row"><span>资产类别:</span> <strong>{asset_profile.get('category', 'N/A')}</strong></div>
                    <div class="sub-metric-row"><span>波动属性:</span> <strong class="volatility-value">{asset_profile.get('volatility_level', 'N/A')}</strong></div>
                    <div class="sub-metric-row"><span>波动依据:</span> <strong>{asset_profile.get('volatility_basis', 'N/A')}</strong></div>
                </div>
                <div class="right-box">
                    <div class="section-title">🔍 多维诊断评估逻辑</div>
                    <ul class="reasons-list">
                        {reasons_html}
                    </ul>
                </div>
            </div>
            <div class="diagnostic-panel">
                <div class="section-title">🧠 右侧止跌多条件诊断</div>
                <div class="strategy-note">当前评分：{price_action.get('score', 'N/A')} / {price_action.get('max_score', 'N/A')} · {price_action.get('rule', '未提供')}</div>
                <div class="diagnostic-grid">{diagnostic_rows}</div>
                <div class="diagnostic-note">成交量/20日均量：{price_action.get('volume_ratio', 'N/A')} · 当前 ATR/历史 ATR 中位数：{price_action.get('atr_median', 'N/A')} · 波动率扩张：{'是' if price_action.get('volatility_expanding') else '否'}</div>
            </div>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>专业美股量化与基本面监控终端</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #0b0f19;
                color: #f1f5f9;
                margin: 0;
            }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #1e293b;
                padding-bottom: 20px;
                margin-bottom: 30px;
            }}
            h1 {{ margin: 0; font-size: 26px; color: #f8fafc; font-weight: 700; }}
            .date-tag {{ color: #64748b; font-size: 14px; font-family: monospace; }}
            
            .card {{
                border-radius: 12px;
                padding: 24px;
                margin-bottom: 25px;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
                display: flex;
                flex-direction: column;
            }}
            .card-header {{ order: 0; }}
            .execution-title {{ order: 2; }}
            .action-banner {{ order: 3; }}
            .execution-metrics {{ order: 4; }}
            .calculation-note {{ order: 5; }}
            .decision-panel {{ order: 6; }}
            .market-module-title {{ order: 10; }}
            .trend-panel {{ order: 11; }}
            .context-panel {{ order: 11; }}
            .details-split {{ order: 12; }}
            .zones-panel {{ order: 13; }}
            .market-panel {{ order: 14; }}
            .research-panel {{ order: 15; }}
            .fundamentals-module-title {{ order: 30; }}
            .fundamentals-title {{ order: 31; }}
            .fundamentals-metrics {{ order: 32; }}
            .fundamentals-summary {{ order: 33; }}
            .fundamentals-assessment {{ order: 34; }}
            .fundamentals-quarterly {{ order: 35; }}
            .fundamentals-states {{ order: 36; }}
            .card-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 18px;
                border-bottom: 1px solid #1f2937;
                padding-bottom: 12px;
            }}
            .title-area {{ display: flex; align-items: baseline; gap: 12px; }}
            .ticker {{ font-size: 24px; font-weight: 800; color: #ffffff; letter-spacing: 0.5px; }}
            .price {{ font-size: 20px; font-weight: 600; color: #93c5fd; }}
            .sector-tag {{ font-size: 12px; color: #64748b; background: #1e293b; padding: 3px 8px; border-radius: 4px; }}
            
            .badge {{ font-size: 12px; font-weight: 700; padding: 6px 12px; border-radius: 20px; }}
            
            .section-title {{
                font-size: 13px;
                font-weight: 700;
                color: #94a3b8;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-top: 16px;
                margin-bottom: 10px;
            }}
            
            .metrics-grid {{
                display: grid;
                gap: 10px;
                background: #0f172a;
                padding: 14px;
                border-radius: 8px;
                border: 1px solid #1e293b;
            }}
            .fundamentals-grid {{ grid-template-columns: repeat(6, 1fr); }}
            .execution-grid {{ grid-template-columns: repeat(6, 1fr); }}
            
            .metric-box {{ text-align: center; }}
            .metric-box .label {{ font-size: 11px; color: #64748b; margin-bottom: 4px; }}
            .metric-box .value {{ font-size: 15px; font-weight: 700; color: #e2e8f0; }}
            .metric-box .value.green {{ color: #34d399; }}
            .metric-box .value.red {{ color: #f87171; }}
            .metric-box .value.accent {{ color: #60a5fa; }}
            
            .details-split {{
                display: grid;
                grid-template-columns: 1fr 2fr;
                gap: 20px;
                margin-top: 15px;
                background: #0f172a;
                padding: 15px;
                border-radius: 8px;
                border: 1px solid #1e293b;
            }}
            .financial-summary {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 8px 18px;
                margin-top: 10px;
                padding: 12px 14px;
                border-left: 3px solid #38bdf8;
                background: #101827;
            }}
            .financial-summary div {{ display: flex; justify-content: space-between; gap: 10px; font-size: 12px; }}
            .financial-summary span {{ color: #64748b; }}
            .financial-summary strong {{ color: #cbd5e1; }}
            .zones-panel {{ margin-top: 15px; background: #0f172a; padding: 15px; border: 1px solid #1e293b; border-radius: 8px; overflow-x: auto; }}
            .module-title {{ margin-top: 28px; padding: 12px 14px; border-left: 4px solid #38bdf8; background: #17243a; color: #f8fafc; font-size: 18px; font-weight: 800; letter-spacing: .3px; }}
            .decision-panel {{ margin-top: 15px; padding: 16px 18px; border: 1px solid #2563eb; border-radius: 8px; background: linear-gradient(110deg, #10213b, #111827); }}
            .side-nav {{ position: sticky; top: 12px; z-index: 5; display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; padding: 10px; background: #0b1424; border: 1px solid #263852; border-radius: 8px; }}
            .side-nav-title {{ color: #f8fafc; font-size: 12px; font-weight: 700; margin-right: 8px; padding: 5px 0; }}
            .side-nav a {{ color: #93c5fd; text-decoration: none; padding: 5px 8px; border-radius: 4px; font-size: 12px; }}
            .side-nav a:hover {{ background: #1e3a5f; color: #fff; }}
            .volatility-tag {{ color: #fbbf24; background: #422006; border: 1px solid #92400e; padding: 3px 8px; border-radius: 4px; font-size: 12px; }}
            .decision-kicker {{ color: #93c5fd; font-size: 12px; font-weight: 700; margin-bottom: 6px; }}
            .decision-action {{ display: block; color: #fbbf24; font-size: 19px; margin-bottom: 6px; }}
            .decision-reason {{ color: #e2e8f0; font-size: 13px; margin-bottom: 6px; }}
            .decision-panel small {{ color: #94a3b8; font-size: 11px; }}
            .action-banner {{ margin-top: 12px; padding: 14px 16px; background: #0b1424; border: 1px solid #334155; border-radius: 8px; }}
            .context-panel {{ margin-top: 15px; background: #0f172a; padding: 15px; border: 1px solid #1e293b; border-radius: 8px; }}
            .context-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
            .context-row {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: baseline; padding: 9px 11px; background: #101827; border: 1px solid #263852; border-radius: 4px; font-size: 12px; }}
            .context-row span {{ color: #94a3b8; }}
            .context-row strong {{ color: #e2e8f0; text-align: right; }}
            .context-row .green, .context-row strong.green {{ color: #34d399; }}
            .context-row .red, .context-row strong.red {{ color: #f87171; }}
            .context-note {{ margin-top: 10px; }}
            .action-head {{ display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }}
            .action-side {{ padding: 4px 12px; border-radius: 4px; font-size: 14px; font-weight: 800; }}
            .side-buy {{ color: #86efac; background: #052e16; border: 1px solid #15803d; }}
            .side-sell {{ color: #fca5a5; background: #2a0f12; border: 1px solid #b91c1c; }}
            .side-hold {{ color: #93c5fd; background: #0c1b34; border: 1px solid #1d4ed8; }}
            .side-wait {{ color: #cbd5e1; background: #16202f; border: 1px solid #475569; }}
            .action-name {{ color: #f8fafc; font-size: 19px; }}
            .action-urgency {{ color: #fbbf24; font-size: 12px; }}
            .action-shares {{ color: #f8fafc; background: #1e293b; border: 1px solid #475569; padding: 3px 10px; border-radius: 4px; font-size: 13px; font-weight: 700; }}
            .badge-area {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
            .position-badge {{ font-weight: 800; }}
            .action-position strong.green {{ color: #34d399; }}
            .action-position strong.red {{ color: #f87171; }}
            .action-reason {{ margin-top: 8px; color: #e2e8f0; font-size: 13px; }}
            .action-position {{ margin-top: 6px; color: #94a3b8; font-size: 12px; font-family: monospace; }}
            .action-foot {{ margin-top: 10px; color: #64748b; font-size: 11px; line-height: 1.7; }}
            .action-chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
            .action-chip, .level-chip {{ display: flex; flex-direction: column; gap: 3px; padding: 7px 10px; border-radius: 4px; font-size: 12px; flex: 1 1 200px; min-width: 180px; max-width: 320px; }}
            .action-chip small, .level-chip small {{ color: #64748b; font-size: 10px; line-height: 1.4; }}
            .chip-on {{ color: #86efac; background: #0b2417; border: 1px solid #15803d; font-weight: 700; }}
            .chip-off {{ color: #64748b; background: #111827; border: 1px solid #263852; }}
            .level-chip {{ color: #cbd5e1; background: #101827; border: 1px solid #263852; }}
            .level-breached {{ color: #fca5a5; background: #2a0f12; border: 1px solid #b91c1c; font-weight: 700; }}
            .level-warning {{ color: #fbbf24; background: #2a2110; border: 1px solid #b45309; font-weight: 700; }}
            .diagnostic-panel {{ order: 12.5; margin-top: 15px; padding: 15px; background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; }}
            .diagnostic-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }}
            .diagnostic-row {{ display: flex; justify-content: space-between; gap: 8px; padding: 8px 10px; background: #101827; border: 1px solid #263852; color: #cbd5e1; font-size: 12px; }}
            .diagnostic-row .pass {{ color: #34d399; }}
            .diagnostic-row .fail {{ color: #f87171; }}
            .diagnostic-note {{ margin-top: 10px; color: #94a3b8; font-size: 11px; }}
            .research-panel {{ margin-top: 15px; background: #0f172a; padding: 15px; border: 1px solid #1e293b; border-radius: 8px; overflow-x: auto; }}
            .research-period {{ margin-top: 12px; padding: 12px; background: #101827; border: 1px solid #263852; border-radius: 6px; overflow-x: auto; }}
            .research-period-title {{ color: #f8fafc; font-size: 15px; font-weight: 800; margin-bottom: 6px; }}
            .research-period-title small {{ margin-left: 12px; color: #94a3b8; font-size: 11px; font-weight: 400; }}
            .data-complete {{ color: #34d399; font-weight: 700; }}
            .data-insufficient {{ color: #fbbf24; font-weight: 700; }}
            .research-warning {{ margin: 8px 0 10px; padding: 8px 10px; color: #fbbf24; background: #2a2110; border-left: 3px solid #f59e0b; font-size: 12px; }}
            .market-panel, .financial-states-panel {{ margin-top: 15px; padding: 15px; background: #101827; border: 1px solid #1e293b; border-radius: 8px; }}
            .market-metrics {{ display: flex; gap: 20px; align-items: center; color: #cbd5e1; font-size: 13px; }}
            .market-metrics strong {{ color: #38bdf8; font-size: 16px; }}
            .financial-states {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }}
            .financial-state {{ display: grid; grid-template-columns: 1fr auto; gap: 4px; padding: 10px; background: #0b1424; border: 1px solid #263852; font-size: 12px; }}
            .financial-state span {{ color: #94a3b8; }}
            .financial-state strong {{ text-align: right; }}
            .financial-state strong.改善 {{ color: #34d399; }}
            .financial-state strong.恶化 {{ color: #f87171; }}
            .financial-state strong.持平, .financial-state strong.数据不足 {{ color: #fbbf24; }}
            .financial-state small {{ grid-column: 1 / -1; color: #cbd5e1; }}
            .assessment-panel, .trend-panel {{ margin-top: 15px; background: #0f172a; padding: 15px; border: 1px solid #1e293b; border-radius: 8px; }}
            .quarterly-panel {{ margin-top: 15px; background: #0f172a; padding: 15px; border: 1px solid #1e293b; border-radius: 8px; }}
            .assessment-panel td:nth-child(2) {{ color: #34d399; }}
            .calculation-note {{ margin-top: 10px; color: #64748b; font-size: 11px; line-height: 1.7; }}
            .strategy-note {{ color: #64748b; font-size: 12px; margin: -4px 0 10px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
            th, td {{ text-align: right; padding: 8px 6px; border-bottom: 1px solid #1e293b; }}
            th:first-child, td:first-child {{ text-align: left; }}
            th {{ color: #64748b; font-weight: 600; }}
            td {{ color: #cbd5e1; }}
            td:nth-child(3) {{ color: #34d399; font-weight: 700; }}
            .confidence {{ color: #fbbf24 !important; }}
            tr.current-group td {{ background: #13263f; color: #e2e8f0; font-weight: 700; }}
            tr.current-group td:first-child {{ box-shadow: inset 3px 0 0 #38bdf8; color: #38bdf8; }}
            .backtest-method {{ margin-top: 14px; padding: 12px 14px; border-left: 3px solid #f59e0b; background: #101827; color: #94a3b8; font-size: 12px; line-height: 1.7; }}
            .backtest-method strong {{ display: block; margin-bottom: 4px; color: #f8fafc; }}
            .trend-meta {{ display: flex; justify-content: space-between; gap: 12px; color: #94a3b8; font-size: 12px; margin-bottom: 8px; }}
            .trend-meta strong {{ color: #38bdf8; }}
            .trend-chart-wrap {{ position: relative; width: 100%; min-height: 300px; }}
            .trend-chart {{ display: block; width: 100%; height: 300px; background: #0b1424; border: 1px solid #263852; }}
            .trend-chart line:not(.axis-line) {{ stroke: #2a3b55; stroke-width: 1; }}
            .trend-chart .axis-line {{ stroke: #8aa4c4; stroke-width: 1.5; }}
            .trend-chart polyline {{ fill: none; stroke: #4cc9ff; stroke-width: 3; vector-effect: non-scaling-stroke; }}
            .trend-chart text {{ fill: #d6e4f5; font-size: 13px; font-weight: 600; }}
            .trend-chart .axis-title {{ fill: #f1f5f9; font-size: 14px; }}
            .trend-point {{ fill: #7dd8ff; opacity: 0; cursor: crosshair; }}
            .trend-point:hover {{ opacity: 1; stroke: #ffffff; stroke-width: 2; vector-effect: non-scaling-stroke; }}
            .trend-tooltip {{ position: absolute; display: none; pointer-events: none; z-index: 2; padding: 7px 9px; border: 1px solid #4cc9ff; background: #07111f; color: #f8fafc; font: 12px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; white-space: nowrap; box-shadow: 0 5px 18px rgba(0,0,0,.35); }}
            .trend-stats {{ color: #64748b; font-size: 11px; margin-top: 8px; }}
            .quarterly-chart {{ display: block; width: 100%; height: 430px; background: #0b1424; border: 1px solid #263852; }}
            .quarterly-chart line {{ stroke: #2a3b55; stroke-width: 1; }}
            .quarterly-chart polyline {{ fill: none; stroke-width: 3; vector-effect: non-scaling-stroke; }}
            .quarterly-chart circle {{ stroke: #0b1424; stroke-width: 2; cursor: help; vector-effect: non-scaling-stroke; }}
            .quarterly-chart circle:hover {{ r: 8px; stroke: #ffffff; }}
            .quarterly-chart text {{ fill: #d6e4f5; font-size: 11px; font-weight: 600; }}
            .quarterly-chart .quarterly-series-label {{ font-size: 14px; font-weight: 700; }}
            .quarterly-chart .axis-title {{ fill: #f1f5f9; font-size: 13px; }}
            .empty-chart {{ color: #64748b; padding: 24px 0; font-size: 12px; }}
            @media (max-width: 800px) {{
                header, .card-header {{ align-items: flex-start; gap: 12px; flex-direction: column; }}
                .title-area {{ flex-wrap: wrap; }}
                .fundamentals-grid, .execution-grid {{ grid-template-columns: repeat(2, 1fr); }}
                .details-split {{ grid-template-columns: 1fr; }}
                .financial-summary {{ grid-template-columns: 1fr; }}
                .trend-meta {{ flex-direction: column; }}
                .market-metrics {{ align-items: flex-start; flex-direction: column; gap: 7px; }}
                .financial-states {{ grid-template-columns: repeat(2, 1fr); }}
                .diagnostic-grid {{ grid-template-columns: repeat(2, 1fr); }}
                .quarterly-panel {{ overflow-x: auto; }}
                .quarterly-chart {{ min-width: 760px; }}
                .context-grid {{ grid-template-columns: repeat(2, 1fr); }}
                /* chip 原本限宽 280px，窄屏下让它占满一行，避免右侧被裁掉 */
                .action-chip, .level-chip {{ max-width: none; width: 100%; flex: 1 1 100%; min-width: 0; }}
                .action-head {{ gap: 8px; }}
                .action-name {{ font-size: 17px; }}
            }}
            @media (max-width: 560px) {{
                .card {{ padding: 14px; }}
                .fundamentals-grid, .execution-grid {{ grid-template-columns: 1fr; }}
                .financial-states, .diagnostic-grid, .context-grid {{ grid-template-columns: 1fr; }}
                .ticker {{ font-size: 20px; }}
                .price {{ font-size: 18px; }}
                .badge-area {{ justify-content: flex-start; }}
                /* 宽表格改为容器内横向滚动，而不是把中文压成竖排 */
                .zones-panel table {{ min-width: 820px; }}
                .research-period table {{ min-width: 960px; }}
                .assessment-panel table {{ min-width: 420px; }}
            }}
            .sub-metric-row {{
                font-size: 13px;
                color: #cbd5e1;
                display: flex;
                justify-content: space-between;
                padding: 6px 0;
                border-bottom: 1px dashed #1e293b;
            }}
            .reasons-list {{ margin: 0; padding-left: 18px; font-size: 13px; color: #94a3b8; line-height: 1.6; }}
            .reasons-list li {{ margin-bottom: 4px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="date-tag">终端更新时间: 2026-09-10</div>
            <div class="grid">
                {cards_html}
            </div>
        </div>
        <script>
            document.querySelectorAll('.trend-chart-wrap').forEach(function (chart) {{
                var tooltip = chart.querySelector('.trend-tooltip');
                chart.querySelectorAll('.trend-point').forEach(function (point) {{
                    point.addEventListener('mouseenter', function (event) {{
                        tooltip.innerHTML = '日期：' + point.dataset.date + '<br>价格：$' + point.dataset.close;
                        tooltip.style.display = 'block';
                    }});
                    point.addEventListener('mousemove', function (event) {{
                        var bounds = chart.getBoundingClientRect();
                        var x = event.clientX - bounds.left + 12;
                        var y = event.clientY - bounds.top - 48;
                        tooltip.style.left = Math.min(x, bounds.width - tooltip.offsetWidth - 8) + 'px';
                        tooltip.style.top = Math.max(y, 4) + 'px';
                    }});
                    point.addEventListener('mouseleave', function () {{
                        tooltip.style.display = 'none';
                    }});
                }});
            }});
        </script>
    </body>
    </html>
    """

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    if open_browser:
        webbrowser.open('file://' + os.path.realpath(output_file))
    print(f"✅ 专业级高密度 HTML 终端看板已生成: {output_file}")
