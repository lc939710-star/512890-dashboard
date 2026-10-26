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
import json
import requests
import akshare as ak
import pandas as pd
from datetime import datetime

HISTORY_FILE = "history.csv"
RECORDS_FILE = "records.json"
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


def fetch_realtime_point():
    """H30269 实时点位（盘中）。失败返回 None。"""
    try:
        s = ak.stock_zh_index_spot_em(symbol="中证系列指数")
        row = s[s["代码"].astype(str) == INDEX_CODE]
        if not row.empty:
            return float(row.iloc[0]["最新价"])
    except Exception:
        pass
    return None


def intraday_dividend_yield(div_date, div_yield):
    """盘中实时股息率近似：以最近已知股息率+对应收盘点位为基准，
    按实时点位缩放（点位涨→股息率降）。返回 (div_yield, point_now)。"""
    point_now = fetch_realtime_point()
    if point_now is None:
        return div_yield, None
    try:
        h = ak.stock_zh_index_hist_csindex(
            symbol=INDEX_CODE, start_date="20240101", end_date="20990101"
        )
        h["date"] = pd.to_datetime(h["日期"])
        h = h[["date", "收盘"]].dropna().sort_values("date")
        base_date = pd.Timestamp(div_date)
        hist_row = h[h["date"] <= base_date].tail(1)
        if hist_row.empty:
            hist_row = h.tail(1)
        base_point = float(hist_row.iloc[0]["收盘"])
        if base_point > 0:
            return div_yield * base_point / point_now, point_now
    except Exception:
        pass
    return div_yield, point_now


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

    # 策略：永远正常定投，只在便宜时慢慢加仓
    if percentile >= 80:
        dot = "🟢"
        signal = "很便宜 —— 可以额外加仓"
    elif percentile >= 50:
        dot = "🟢"
        signal = "偏便宜 —— 可慢慢多买一点"
    elif percentile >= 20:
        dot = "🟡"
        signal = "中性 —— 正常定投即可"
    else:
        dot = "🔴"
        signal = "偏贵 —— 正常定投，先别加仓"

    return percentile, n, signal, dot


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


def load_records():
    if os.path.exists(RECORDS_FILE):
        with open(RECORDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def upsert_record(records, rec):
    records = [r for r in records if r["date"] != rec["date"]]
    records.append(rec)
    records.sort(key=lambda r: r["date"], reverse=True)  # 新的在前
    return records


def build_dashboard_html(records):
    """生成手机端极简仪表盘 index.html。"""
    today = records[0] if records else None

    def fmt_date(d):
        dt = datetime.strptime(d, "%Y-%m-%d")
        week = "一二三四五六日"[dt.weekday()]
        return f"{dt.month}月{dt.day}日 周{week}"

    if today:
        hero = f"""
        <div class="hero">
          <div class="hero-date">{fmt_date(today['date'])}</div>
          <div class="hero-pct">{today['dot']} {today['percentile']:.0f}<span>%</span></div>
          <div class="hero-sub">历史分位 · 上市以来 {today['n']} 个交易日</div>
          <div class="hero-nums">股息率 {today['div']:.2f}%　·　10Y 国债 {today['bond']:.2f}%</div>
          <div class="hero-spread">剪刀差 <b>{today['spread']:.2f}pp</b></div>
          <div class="hero-signal">{today['signal']}</div>
        </div>"""
    else:
        hero = '<div class="hero">暂无数据</div>'

    rows = ""
    for r in records:
        rows += f"""
        <div class="row">
          <div class="row-dot">{r['dot']}</div>
          <div class="row-body">
            <div class="row-date">{fmt_date(r['date'])}</div>
            <div class="row-sig">{r['signal']}</div>
          </div>
          <div class="row-pct">{r['percentile']:.0f}%</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f7f7f8">
<title>512890 红利低波</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
  body {{ font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; background:#f7f7f8; color:#1a1a1a; padding:24px 18px 60px; max-width:560px; margin:0 auto; }}
  .title {{ font-size:15px; color:#888; margin-bottom:18px; }}
  .hero {{ background:#fff; border-radius:20px; padding:28px 22px; text-align:center; box-shadow:0 1px 4px rgba(0,0,0,.04); }}
  .hero-date {{ font-size:14px; color:#999; }}
  .hero-pct {{ font-size:64px; font-weight:700; line-height:1.1; margin:10px 0 4px; letter-spacing:-1px; }}
  .hero-pct span {{ font-size:30px; font-weight:600; color:#555; }}
  .hero-sub {{ font-size:13px; color:#999; margin-bottom:18px; }}
  .hero-nums {{ font-size:15px; color:#444; }}
  .hero-spread {{ font-size:15px; color:#444; margin:6px 0 18px; }}
  .hero-signal {{ font-size:18px; font-weight:600; padding-top:16px; border-top:1px solid #f0f0f0; }}
  .legend {{ font-size:12px; color:#aaa; line-height:1.9; margin:18px 6px; }}
  .hist-title {{ font-size:13px; color:#999; margin:26px 6px 10px; }}
  .row {{ background:#fff; border-radius:14px; padding:14px 16px; display:flex; align-items:center; margin-bottom:8px; }}
  .row-dot {{ font-size:18px; margin-right:12px; }}
  .row-body {{ flex:1; }}
  .row-date {{ font-size:14px; color:#333; }}
  .row-sig {{ font-size:12px; color:#999; margin-top:2px; }}
  .row-pct {{ font-size:20px; font-weight:700; }}
</style>
</head>
<body>
  <div class="title">512890 红利低波 · 每日决策</div>
  {hero}
  <div class="legend">
    🟢 ≥80% 很便宜，额外加仓<br>
    🟢 50–80% 偏便宜，多买一点<br>
    🟡 20–50% 中性，正常定投<br>
    🔴 &lt;20% 偏贵，别加仓
  </div>
  <div class="hist-title">历史记录（新→旧）</div>
  {rows}
</body>
</html>"""


def get_signal_data(write_history=False):
    """组装完整决策面板文本。write_history=False(盘中)只刷新大卡片；True(收盘后)才写入历史记录。"""
    try:
        div_date, div_yield = fetch_dividend_yield()
        # 盘中用实时点位把股息率缩放到当下
        div_yield, point_now = intraday_dividend_yield(div_date, div_yield)
        bond_date, bond_yield = fetch_bond_yield()
        percentile, n, signal, dot = compute_signal(
            div_date, div_yield, bond_date, bond_yield
        )
        premium = fetch_premium()

        date_dt = datetime.strptime(div_date, "%Y-%m-%d")
        week = "一二三四五六日"[date_dt.weekday()]
        spread = div_yield - bond_yield

        point_txt = f"\n指数点位 **{point_now:.0f}**" if point_now else ""
        msg = (
            f"**{date_dt.month}月{date_dt.day}日 周{week}**\n\n"
            f"# {dot} {percentile:.0f}%\n"
            f"历史分位 · 上市以来 {n} 个交易日\n\n"
            f"股息率 {div_yield:.2f}%　·　10Y 国债 {bond_yield:.2f}%\n"
            f"{point_txt}\n\n"
            f"剪刀差 **{spread:.2f}pp**\n\n"
            f"### {signal}\n\n"
            f"———\n"
            f"分位怎么看：\n"
            f"🟢 ≥80% 很便宜，额外加仓\n"
            f"🟢 50–80% 偏便宜，多买一点\n"
            f"🟡 20–50% 中性，正常定投\n"
            f"🔴 <20% 偏贵，别加仓\n\n"
            f"<sub>512890 实时溢价 {premium}</sub>"
        )

        # 当日实时记录（用于大卡片）
        rec = {
            "date": div_date,
            "percentile": round(percentile, 1),
            "div": round(div_yield, 2),
            "bond": round(bond_yield, 2),
            "spread": round(spread, 2),
            "dot": dot,
            "signal": signal,
            "n": n,
        }
        records = load_records()
        if write_history:
            # 收盘后：把今天这条写进历史（一天一条，按日期去重）
            records = upsert_record(records, rec)
            with open(RECORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        else:
            # 盘中：大卡片显示实时值，但历史列表不重复记录今天
            records = [r for r in records if r["date"] != rec["date"]]
        # 大卡片用实时 rec，历史列表用 records
        hero_records = [rec] + records
        with open("index.html", "w", encoding="utf-8") as f:
            f.write(build_dashboard_html(hero_records))
        print(f"已生成 index.html (write_history={write_history})")

        return msg
    except Exception as e:
        return f"⚠️ 获取数据失败\n\n{str(e)}"


def send_wechat(text):
    """通过 Server酱 推送到个人微信。环境变量 SERVER_SENDKEY。"""
    key = os.getenv("SERVER_SENDKEY")
    if not key:
        print("未配置 Server酱 SendKey，跳过发送。")
        return
    title = "512890 红利低波"
    desp = text
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
    from datetime import timezone, timedelta
    now_bj = datetime.now(timezone(timedelta(hours=8)))
    print(f"开始获取今日数据...(北京时间{now_bj.hour}点)")

    # 收盘后(>=15点)才把今天写进历史记录并推微信；盘中只刷新大卡片
    after_close = now_bj.hour >= 15
    report = get_signal_data(write_history=after_close)
    print("生成的面板内容如下：\n" + report)

    if after_close:
        send_wechat(report)
    else:
        print("盘中：只更新网页大卡片，不写历史、不推微信。")
