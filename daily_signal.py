# -*- coding: utf-8 -*-
"""
512890 红利低波 · 每日决策面板
数据源（全部为官方/免费、可每日自动获取）：
  - 红利低波(中证 H30269)股息率 : 中证指数官方估值文件  ak.stock_zh_index_value_csindex
  - 10年期国债收益率           : 中国债券信息网/宏观数据      ak.bond_zh_us_rate
  - 512890 实时溢价率          : 东方财富 ETF 实时行情         ak.fund_etf_spot_em (可选,失败自动降级)

历史数据说明：
  history.csv 已用【理杏仁】导出的上市以来(2013-12-19 至今)日频股息率 + 国债收益率
  合并生成（股息率近期段统一为中证官方口径）。脚本每天把当日数据追加回写进 history.csv，
  序列持续累积。
"""
import os
import requests
import akshare as ak
import pandas as pd
from datetime import datetime

HISTORY_FILE = "history.csv"
INDEX_CODE = "H30269"          # 中证红利低波动指数
ETF_CODE = "512890"


def fetch_dividend_yield():
    """获取红利低波(中证官方)最新股息率。返回 (date_str, div_yield)。"""
    df = ak.stock_zh_index_value_csindex(symbol=INDEX_CODE)
    # 该接口返回顺序不固定，需按日期排序后取最新
    df["date"] = pd.to_datetime(df["日期"])
    df = df[["date", "股息率1"]].dropna().drop_duplicates("date").sort_values("date")
    if df.empty:
        raise ValueError("中证红利低波估值接口返回为空")
    latest = df.iloc[-1]
    return latest["date"].strftime("%Y-%m-%d"), float(latest["股息率1"])


def fetch_bond_yield():
    """获取10年期国债收益率最新值。返回 (date_str, yield)。"""
    df = ak.bond_zh_us_rate()
    col = "中国国债收益率10年" if "中国国债收益率10年" in df.columns else None
    if col is None:
        # 兼容旧列名
        for c in ("中国10年期国债收益率", "中国10年期国债"):
            if c in df.columns:
                col = c
                break
    if col is None:
        raise ValueError("国债收益率接口列名发生变化，请检查 ak.bond_zh_us_rate")
    df["date"] = pd.to_datetime(df["日期"])
    df = df[["date", col]].dropna().drop_duplicates("date").sort_values("date")
    if df.empty:
        raise ValueError("国债收益率接口返回为空")
    latest = df.iloc[-1]
    return latest["date"].strftime("%Y-%m-%d"), float(latest[col])


def load_history():
    """加载本地历史数据；文件不存在则返回空表。"""
    if os.path.exists(HISTORY_FILE):
        df = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig")
        df["date"] = pd.to_datetime(df["date"])
        return df
    return pd.DataFrame(columns=["date", "div_yield", "bond10y", "spread"])


def save_history(df):
    """回写历史数据到文件。"""
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")


def compute_signal(div_date, div_yield, bond_date, bond_yield):
    """合并/追加当日数据并计算分位值与操作建议。"""
    hist = load_history()
    today = pd.Timestamp(div_date)

    # 若当日数据尚未入库则追加
    if hist.empty or not (hist["date"] == today).any():
        new_row = pd.DataFrame(
            [{
                "date": today,
                "div_yield": div_yield,
                "bond10y": bond_yield,
                "spread": div_yield - bond_yield,
            }]
        )
        hist = pd.concat([hist, new_row], ignore_index=True)
        hist = hist.drop_duplicates("date").sort_values("date")
        save_history(hist)

    spread = div_yield - bond_yield
    n = len(hist)
    percentile = float((hist["spread"] <= spread).mean() * 100)

    if percentile >= 80:
        signal = "🟢【加倍买入区】\n性价比极高，开启加倍定投或手动加仓。"
    elif percentile >= 20:
        signal = "🟡【正常定投区】\n性价比中性，按原计划正常定投即可。"
    else:
        signal = "🔴【停止买入/分批卖出区】\n性价比变差，停止定投，视仓位分批止盈。"

    return percentile, n, signal


def fetch_premium():
    """获取 512890 实时溢价率，失败返回'获取失败'。"""
    try:
        df = ak.fund_etf_spot_em()
        row = df[df["代码"] == ETF_CODE]
        if not row.empty:
            r = row.iloc[0]
            price = float(r["最新价"])
            iopv = float(r["IOPV实时估值"])
            if iopv > 0:
                premium = (price - iopv) / iopv * 100
                return f"{premium:+.2f}%"
        return "获取失败"
    except Exception:
        return "获取失败"


def get_signal_data():
    """组装完整决策面板文本。"""
    try:
        div_date, div_yield = fetch_dividend_yield()
        bond_date, bond_yield = fetch_bond_yield()
        percentile, n, signal = compute_signal(
            div_date, div_yield, bond_date, bond_yield
        )
        premium = fetch_premium()

        msg = (
            "📊 512890 红利低波 · 每日决策面板\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📅 数据日期：{div_date}\n"
            f"💰 当前股息率：{div_yield:.2f}%\n"
            f"🏦 10年期国债：{bond_yield:.2f}%\n"
            f"✂️ 当前剪刀差：{div_yield - bond_yield:.2f} 个百分点\n"
            f"📈 历史分位值：{percentile:.1f}% (上市以来{n}个交易日)\n"
            f"💹 实时溢价率：{premium}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💡 今日操作建议：\n{signal}\n"
        )
        return msg
    except Exception as e:
        return f"⚠️ 获取数据失败，请检查接口状态。\n错误信息：{str(e)}"


def send_wechat(text):
    """通过 Server酱 推送到个人微信。环境变量 SERVER_SENDKEY。"""
    key = os.getenv("SERVER_SENDKEY")
    if not key:
        print("未配置 Server酱 SendKey，跳过发送。")
        return
    title = "📊 512890 红利低波每日面板"
    # 用代码块包裹，保留等宽排版与换行
    desp = "```\n" + text + "\n```"
    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": desp},
            timeout=10,
        )
        print(f"Server酱推送状态: {resp.json()}")
    except Exception as e:
        print(f"推送失败: {e}")


if __name__ == "__main__":
    print("开始获取今日数据...")
    report = get_signal_data()
    print("生成的面板内容如下：\n" + report)
    send_wechat(report)
