import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import requests, re, json, time
import concurrent.futures
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from google import genai
import os

st.set_page_config(page_title="我的基金监控", page_icon="📈", layout="centered")

HISTORY_FILE  = Path("history.json")
HOLDINGS_FILE = Path("holdings.json")
OIL_HISTORY_FILE = Path("oil_history.json")
FUND_HISTORY_FILE = Path("fund_history.json")
REFRESH_INTERVAL = 300  # 5分钟自动刷新

# ════════════════════════════════════════════════════════
#  数据持久化
# ════════════════════════════════════════════════════════
def load_json(path, default):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            # 文件损坏时自动备份并重置
            import shutil
            shutil.copy(path, str(path) + ".bak")
            path.unlink()
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

load_holdings = lambda: load_json(HOLDINGS_FILE, {})
save_holdings = lambda h: save_json(HOLDINGS_FILE, h)
load_history  = lambda: load_json(HISTORY_FILE, {})

def append_record(code, name, pnl_pct, gz, jzrq="", dwjz=0):
    history = load_history()
    if code not in history:
        history[code] = {"name": name, "records": []}
    now_str = datetime.now().strftime("%Y-%m-%d %H:") + str(datetime.now().minute // 10 * 10).zfill(2)
    records = history[code]["records"]
    if records and records[-1]["time"] == now_str:
        records[-1].update({"pnl": pnl_pct, "gz": gz, "jzrq": jzrq, "dwjz": dwjz})
    else:
        records.append({
            "time": now_str,
            "pnl": round(pnl_pct, 4),
            "gz": round(gz, 4),
            "jzrq": jzrq,
            "dwjz": round(dwjz, 4) if dwjz else 0
        })
    history[code]["records"] = records[-200:]

    # 同步每日收盘点（用于日线趋势）
    day = jzrq or now_str[:10]
    daily = history[code].get("daily", [])
    updated = False
    for i in range(len(daily) - 1, -1, -1):
        if daily[i].get("day") == day:
            daily[i].update({"pnl": round(pnl_pct, 4), "gz": round(gz, 4), "dwjz": round(dwjz, 4) if dwjz else 0})
            updated = True
            break
    if not updated:
        daily.append({"day": day, "pnl": round(pnl_pct, 4), "gz": round(gz, 4), "dwjz": round(dwjz, 4) if dwjz else 0})
    history[code]["daily"] = daily[-730:]
    save_json(HISTORY_FILE, history)

def backfill_history_last_month():
    history = load_history()
    cutoff = datetime.now() - timedelta(days=30)
    updated = False
    for code, info in history.items():
        records = info.get("records", [])
        daily_map = {}
        for r in records:
            try:
                t = datetime.strptime(r.get("time", ""), "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if t >= cutoff:
                if not r.get("jzrq"):
                    r["jzrq"] = t.strftime("%Y-%m-%d")
                    updated = True
                day = r.get("jzrq") or t.strftime("%Y-%m-%d")
                daily_map[day] = {
                    "day": day,
                    "pnl": r.get("pnl", 0),
                    "gz": r.get("gz", 0),
                    "dwjz": r.get("dwjz", 0),
                }
        if daily_map:
            info["daily"] = [daily_map[k] for k in sorted(daily_map.keys())]
            updated = True
    if updated:
        save_json(HISTORY_FILE, history)
    return history

# ════════════════════════════════════════════════════════
#  数据抓取
# ════════════════════════════════════════════════════════
def search_fund(keyword):
    """输入中文名或代码，返回匹配基金列表"""
    try:
        url = (f"https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
               f"?callback=jQuery&m=10&key={requests.utils.quote(keyword)}")
        r   = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        raw = re.findall(r"jQuery\((.*)\)", r.text)[0]
        data = json.loads(raw)
        return [{"code": i.get("CODE",""), "name": i.get("NAME",""),
                 "type": i.get("FundType","")} for i in data.get("Datas", [])]
    except Exception:
        return []

def fetch_fund_valuation(code):
    def _get(url, timeout=3, retries=1):
        last_err = None
        for _ in range(retries + 1):
            try:
                return requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err

    result = None
    # ── 接口1：天天基金 ──────────────────────────────────
    try:
        url  = f"http://fundgz.1234567.com.cn/js/{code}.js"
        r    = _get(url, timeout=3, retries=1)
        raw  = re.findall(r"\((.*)\)", r.text)[0]
        raw  = raw.replace("NULL", "null").replace("undefined", "null")
        data = json.loads(raw)
        name = data["name"]
        gz   = float(data["gsz"])
        rate = float(data["gszzl"])
        dwjz = float(data.get("dwjz") or 0)
        jzrq = data.get("jzrq") or ""
        if gz == 0 and dwjz > 0:
            gz = dwjz
        result = {"name": name, "gz": gz, "rate": rate, "dwjz": dwjz, "jzrq": jzrq}
    except Exception:
        pass
    # ── 接口2：东方财富（补充涨跌幅）────────────────────────
    try:
        url2 = (f"https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo"
                f"?plat=Android&appType=ttjj&product=EFund&Version=1&deviceid=x&Fcodes={code}")
        r2   = _get(url2, timeout=3, retries=1)
        d2   = r2.json()
        if d2.get("Datas"):
            item  = d2["Datas"][0]
            gz2   = float(item.get("GSZ") or item.get("DWJZ") or 0)
            rate2 = float(item.get("GSZZL") or item.get("RZDF") or 0)
            name2 = item.get("SHORTNAME") or f"基金{code}"
            dwjz2 = float(item.get("DWJZ") or 0)
            jzrq2 = item.get("PDATE") or ""
            if result is None:
                result = {"name": name2, "gz": gz2, "rate": rate2, "dwjz": dwjz2, "jzrq": jzrq2}
            else:
                if result["rate"] == 0 and rate2 != 0:
                    result["rate"] = rate2
                if result["gz"] == 0 and gz2 > 0:
                    result["gz"] = gz2
                if result.get("dwjz", 0) == 0 and dwjz2 > 0:
                    result["dwjz"] = dwjz2
                if not result.get("jzrq") and jzrq2:
                    result["jzrq"] = jzrq2
    except Exception:
        pass
    return result

def get_fund_valuation_cached(code, ttl=55):
    cache = st.session_state.get("fund_cache", {})
    item = cache.get(code)
    if item and (time.time() - item.get("ts", 0) < ttl):
        return item.get("data")
    data = fetch_fund_valuation(code)
    cache[code] = {"ts": time.time(), "data": data}
    st.session_state["fund_cache"] = cache
    return data

@st.cache_data(ttl=55)
def get_oil_price():
    try:
        hist = yf.Ticker("BZ=F").history(period="2d")
        if not hist.empty:
            # 同步写入本地缓存
            data = [{"date": i.strftime("%Y-%m-%d"), "close": float(v)} for i, v in hist["Close"].items()]
            save_json(OIL_HISTORY_FILE, {"data": data})
            return float(hist["Close"].iloc[-1])
        return None
    except:
        cached = load_json(OIL_HISTORY_FILE, {}).get("data", [])
        return float(cached[-1]["close"]) if cached else None

def fetch_oil_history_yahoo(period="1mo"):
    period_map = {"1mo": "1mo", "3mo": "3mo", "1y": "1y"}
    y_period = period_map.get(period, "1mo")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?range={y_period}&interval=1d"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
    r.raise_for_status()
    data = r.json()
    result = data.get("chart", {}).get("result", [])
    if not result:
        return None
    ts = result[0].get("timestamp", [])
    close = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
    rows = [(datetime.fromtimestamp(t), c) for t, c in zip(ts, close) if c is not None]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "close"]).set_index("date")
    df = df.sort_index()
    return df

@st.cache_data(ttl=3600)
def get_oil_history(period="1mo"):
    """获取原油历史价格，默认近1个月"""
    try:
        hist = yf.Ticker("BZ=F").history(period=period)
        if hist.empty:
            raise RuntimeError("empty history")
        df = hist[["Close"]].copy()
        df.index = df.index.tz_localize(None)   # 去掉时区
        df = df.sort_index()                     # 确保按时间正序
        # 1个月只显示月-日，超过1个月显示年-月-日
        if period == "1mo":
            df.index = df.index.strftime("%m-%d")
        else:
            df.index = df.index.strftime("%Y-%m-%d")
        df.columns = ["布伦特原油（$）"]
        # 写入本地缓存
        data = [{"date": i.strftime("%Y-%m-%d"), "close": float(v)} for i, v in hist["Close"].items()]
        save_json(OIL_HISTORY_FILE, {"data": data})
        return df
    except:
        # 回退：直接请求 Yahoo Chart 接口
        try:
            df = fetch_oil_history_yahoo(period)
            if df is not None:
                data = [{"date": i.strftime("%Y-%m-%d"), "close": float(v)} for i, v in df["close"].items()]
                save_json(OIL_HISTORY_FILE, {"data": data})
                cdf = df.copy()
                if period == "1mo":
                    cdf.index = cdf.index.strftime("%m-%d")
                else:
                    cdf.index = cdf.index.strftime("%Y-%m-%d")
                cdf.columns = ["布伦特原油（$）"]
                return cdf
        except Exception:
            pass
        cached = load_json(OIL_HISTORY_FILE, {}).get("data", [])
        if not cached:
            return None
        cdf = pd.DataFrame(cached)
        cdf["date"] = pd.to_datetime(cdf["date"], errors="coerce")
        cdf = cdf.dropna(subset=["date"]).set_index("date").sort_index()
        if period == "1mo":
            cdf.index = cdf.index.strftime("%m-%d")
        else:
            cdf.index = cdf.index.strftime("%Y-%m-%d")
        cdf.columns = ["布伦特原油（$）"]
        return cdf

def load_fund_history():
    return load_json(FUND_HISTORY_FILE, {})

def save_fund_history(data):
    save_json(FUND_HISTORY_FILE, data)

def fetch_fund_history_eastmoney(code, max_days=365):
    per = 40
    page = 1
    rows = []
    pages = 1
    while page <= pages and len(rows) < max_days:
        url = ("https://fundf10.eastmoney.com/F10DataApi.aspx"
               f"?type=lsjz&code={code}&page={page}&per={per}")
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        r.raise_for_status()
        m = re.search(r'content:"(.*)",records:(\d+),pages:(\d+),curpage:(\d+)', r.text, re.S)
        if not m:
            break
        html = m.group(1)
        pages = int(m.group(3))
        html = html.replace("\\r", "").replace("\\n", "").replace("\\t", "")
        html = html.replace("\\/", "/").replace('\\"', '"')
        try:
            table = pd.read_html(html)[0]
        except Exception:
            break
        if "净值日期" not in table.columns or "单位净值" not in table.columns:
            break
        for _, row in table.iterrows():
            date = str(row.get("净值日期", "")).strip()
            value = str(row.get("单位净值", "")).strip()
            if not date or value in {"--", ""}:
                continue
            try:
                rows.append({"date": date, "close": float(value)})
            except Exception:
                continue
        page += 1
    return rows

def get_fund_price_history(code, period_label):
    days_map = {"1个月": 30, "3个月": 90, "1年": 365}
    max_days = days_map.get(period_label, 30)
    cache = load_fund_history()
    item = cache.get(code, {})
    updated = item.get("updated", 0)
    now_ts = time.time()

    if not item or now_ts - updated > 6 * 3600:
        rows = fetch_fund_history_eastmoney(code, max_days=365)
        if rows:
            cache[code] = {"updated": now_ts, "data": rows}
            save_fund_history(cache)
            item = cache[code]

    data = item.get("data", []) if item else []
    if not data:
        return None
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    cutoff = datetime.now() - timedelta(days=max_days)
    df = df[df.index >= cutoff]
    if df.empty:
        return None
    if period_label == "1个月":
        df.index = df.index.strftime("%m-%d")
    else:
        df.index = df.index.strftime("%Y-%m-%d")
    df.columns = ["基金净值"]
    return df

# ════════════════════════════════════════════════════════
#  AI 决策模块
# ════════════════════════════════════════════════════════
def generate_ai_brief(holdings, fund_data, oil_price, total_amount, total_pct, cash=0):
    """将实时持仓数据打包成 AI 分析 Prompt"""
    portfolio_lines = ""
    for code, info in holdings.items():
        res  = fund_data.get(code)
        rate = res["rate"] if res else 0
        pnl  = info.get("pnl_pct", 0)
        amt  = info.get("amount", 0)
        portfolio_lines += (
            f"  - {info['name']}（{code}）: "
            f"持有市值 ¥{amt:,.2f}，今日涨跌 {rate:+.2f}%，累计盈亏 {pnl:+.2f}%"
        )

    oil_text = f"布伦特原油 ${oil_price:.2f}" if oil_price else "原油数据暂不可用"
    now_str  = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    prompt = f"""你是一位资深基金分析师，请用中文回答，风格简洁专业，不要废话。

【当前时间】{now_str}
【宏观环境】{oil_text}
【组合概况】总资产 ¥{total_amount:,.2f}，整体收益率 {total_pct:+.2f}%
【现金储备】¥{cash:,.2f}
【持仓明细】
{portfolio_lines}
请给出以下三点分析：

1. 🎯 风险评估（0-100分）：当前组合最大风险点是什么？打多少分？
2. 📋 操作建议：收盘前针对各持仓，是补仓、减仓还是观望？给出具体理由。
3. 🧘 心理提醒：一句话，帮我保持冷静。"""
    return prompt


def call_gemini(prompt):
    """调用 Gemini API 获取分析建议"""
    api_key = (
        st.session_state.get("gemini_api_key_value")
        or st.session_state.get("gemini_api_key")
        or os.environ.get("GEMINI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("缺少 GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    result = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt
    )
    return result.text

def call_qwen(prompt, model="qwen-turbo"):
    """调用通义千问（DashScope）API 获取分析建议"""
    api_key = (
        st.session_state.get("dashscope_api_key_value")
        or st.session_state.get("dashscope_api_key")
        or os.environ.get("DASHSCOPE_API_KEY")
    )
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {"result_format": "text", "temperature": 0.7},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data.get("output", {}).get("text", "")

def call_ai(prompt, provider):
    if provider == "Qwen (DashScope)":
        return call_qwen(prompt)
    return call_gemini(prompt)

def clear_fund_cache():
    st.session_state.pop("fund_cache", None)


# ════════════════════════════════════════════════════════
#  颜色 & 格式工具
# ════════════════════════════════════════════════════════
def pnl_color(val):
    if val > 0: return "#E84040"
    if val < 0: return "#2EAD6A"
    return "#888888"

def fmt_signed(val, decimals=2, prefix="¥"):
    sign = "+" if val >= 0 else ""
    return f"{sign}{prefix}{val:,.{decimals}f}"

def get_daily_series(code, records):
    cache_key = f"daily_series_{code}"
    last_time = records[-1]["time"] if records else None
    history = load_history()
    daily_list = history.get(code, {}).get("daily", [])
    last_day = daily_list[-1].get("day") if daily_list else None
    last_jzrq = records[-1].get("jzrq") if records else None
    sig = (len(records), last_time, last_jzrq, len(daily_list), last_day)
    cached = st.session_state.get(cache_key)
    if cached and cached.get("sig") == sig:
        return cached.get("df")
    if daily_list:
        df = pd.DataFrame(daily_list)
        df["day"] = pd.to_datetime(df["day"], errors="coerce")
        df = df.dropna(subset=["day"]).sort_values("day").set_index("day")
        st.session_state[cache_key] = {"sig": sig, "df": df}
        return df

    df = pd.DataFrame(records)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time")

    # 优先使用净值日期作为当天收盘点
    use_jzrq = "jzrq" in df.columns and df["jzrq"].astype(str).str.len().gt(0).any()
    if use_jzrq:
        df["jzrq"] = pd.to_datetime(df["jzrq"], errors="coerce")
        daily = df.dropna(subset=["jzrq"]).groupby(df["jzrq"].dt.date).tail(1)
        daily = daily.set_index("jzrq")
        # 如果净值日期为空或全为空，退回时间维度
        if daily.empty:
            use_jzrq = False

    if not use_jzrq:
        # 退化：按时间每天最后一条记录
        daily = df.groupby(df["time"].dt.date).tail(1).set_index("time")
    st.session_state[cache_key] = {"sig": sig, "df": daily}
    return daily

def compute_nav_return(daily):
    if daily is None or daily.empty:
        return None
    if "dwjz" in daily.columns and (daily["dwjz"] > 0).any():
        series = daily["dwjz"].replace(0, pd.NA).dropna()
    else:
        series = daily["gz"].replace(0, pd.NA).dropna() if "gz" in daily.columns else None
    if series is None or series.empty:
        return None
    base = series.iloc[0]
    if base == 0:
        return None
    return (series / base - 1) * 100

def format_daily_index(series):
    if series is None or series.empty:
        return series
    s = series.copy()
    s.index = pd.to_datetime(s.index).strftime("%m-%d")
    return s

def is_market_closed(now=None):
    now = now or datetime.now()
    return (now.hour > 15) or (now.hour == 15 and now.minute >= 0)

def apply_close_update(holdings, fund_data, force=False):
    if not holdings or not fund_data:
        return False
    today_str = datetime.now().strftime("%Y-%m-%d")
    updated = False
    for code, info in holdings.items():
        res = fund_data.get(code)
        if not res:
            continue
        settle_date = res.get("jzrq") or today_str
        if info.get("last_close_date") == settle_date:
            continue
        rate = res.get("rate")
        if rate is None:
            continue
        if not force and not is_market_closed():
            continue
        amount = float(info.get("amount", 0))
        if amount <= 0:
            continue
        # 收盘后把今日涨跌幅计入累计收益与持仓市值（只更新一次）
        delta = amount * rate / 100
        info["amount"] = round(amount + delta, 2)
        info["profit"] = round(float(info.get("profit", 0)) + delta, 2)
        info["cost_amount"] = float(info.get("cost_amount", 0))
        info["pnl_pct"] = (info["profit"] / info["cost_amount"] * 100) if info["cost_amount"] != 0 else 0
        info["last_close_date"] = settle_date
        updated = True
    if updated:
        save_holdings(holdings)
    return updated

# ════════════════════════════════════════════════════════
#  CSS
# ════════════════════════════════════════════════════════
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] {
    background: #F5F6FA !important;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
}
[data-testid="stSidebar"] { background: #FFFFFF !important; }
.block-container { padding-top: 1rem !important; max-width: 680px !important; }

.asset-card {
    background: #FFFFFF; border-radius: 16px; padding: 20px 24px 16px;
    margin-bottom: 12px; box-shadow: 0 1px 6px rgba(0,0,0,0.06);
}
.asset-label { color: #999; font-size: 0.78rem; margin-bottom: 4px; }
.asset-total { font-size: 1.9rem; font-weight: 700; color: #1a1a1a; letter-spacing: -0.5px; }
.asset-sub   { font-size: 0.82rem; color: #999; margin-top: 2px; }

.list-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 16px; color: #aaa; font-size: 0.75rem;
}
.fund-row {
    background: #FFFFFF; border-radius: 14px; padding: 14px 18px;
    margin-bottom: 2px; box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    display: flex; justify-content: space-between; align-items: center;
}
.fund-name  { font-size: 1.0rem; font-weight: 600; color: #1a1a1a; margin-bottom: 4px;
              max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fund-value { font-size: 0.82rem; color: #888; }
.fund-mid   { text-align: center; min-width: 70px; }
.today-amount { font-size: 0.92rem; font-weight: 600; }
.today-rate   { font-size: 0.75rem; margin-top: 2px; }
.fund-right { text-align: right; min-width: 90px; }
.profit-amount { font-size: 1.05rem; font-weight: 700; }
.profit-rate   { font-size: 0.78rem; margin-top: 2px; }
.alert-badge {
    background: #FFF0F0; color: #E84040; border-radius: 6px;
    font-size: 0.72rem; padding: 2px 8px; margin-top: 6px; display: inline-block;
}
.oil-card {
    background: #FFFFFF; border-radius: 12px; padding: 12px 18px; margin-bottom: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05); display: flex; align-items: center;
    gap: 12px; font-size: 0.85rem; color: #555;
}
.oil-price { font-weight: 700; font-size: 1.0rem; color: #1a1a1a; }
.refresh-tip { text-align:center; color:#bbb; font-size:0.75rem; padding: 8px 0 4px; }

/* 历史走势箭头按钮：无边框，融入卡片 */
div[data-testid="stHorizontalBlock"] div[data-testid="column"]:last-child button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #aaa !important;
    font-size: 0.85rem !important;
    padding: 0 4px !important;
    min-height: unset !important;
    height: auto !important;
    line-height: 1 !important;
}
div[data-testid="stHorizontalBlock"] div[data-testid="column"]:last-child button:hover {
    color: #666 !important;
    background: transparent !important;
}
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
#  侧边栏：持仓管理
# ════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ 持仓管理")
    holdings = load_holdings()

    st.subheader("🤖 AI 设置")
    if "ai_provider" not in st.session_state:
        st.session_state.ai_provider = "Qwen (DashScope)"
    ai_provider = st.selectbox(
        "AI Provider",
        ["Qwen (DashScope)", "Gemini"],
        index=["Qwen (DashScope)", "Gemini"].index(st.session_state.ai_provider),
        key="ai_provider",
        label_visibility="collapsed",
    )
    if "edit_ai_key" not in st.session_state:
        st.session_state.edit_ai_key = True

    if ai_provider == "Qwen (DashScope)":
        key_value = st.session_state.get("dashscope_api_key_value", "")
        if st.session_state.edit_ai_key or not key_value:
            st.text_input(
                "DASHSCOPE_API_KEY",
                type="password",
                key="dashscope_api_key",
                placeholder="在这里输入 DashScope Key",
            )
            if st.session_state.get("dashscope_api_key"):
                st.session_state.dashscope_api_key_value = st.session_state.dashscope_api_key
                st.session_state.edit_ai_key = False
        else:
            st.caption("已设置 DashScope Key")
            if st.button("修改 Key", key="edit_dashscope_key"):
                st.session_state.edit_ai_key = True
                st.rerun()
    else:
        key_value = st.session_state.get("gemini_api_key_value", "")
        if st.session_state.edit_ai_key or not key_value:
            st.text_input(
                "GEMINI_API_KEY",
                type="password",
                key="gemini_api_key",
                placeholder="在这里输入 Gemini Key",
            )
            if st.session_state.get("gemini_api_key"):
                st.session_state.gemini_api_key_value = st.session_state.gemini_api_key
                st.session_state.edit_ai_key = False
        else:
            st.caption("已设置 Gemini Key")
            if st.button("修改 Key", key="edit_gemini_key"):
                st.session_state.edit_ai_key = True
                st.rerun()


    st.subheader("➕ 添加 / 更新持仓")

    # session_state 控制表单重置
    if "search_kw"      not in st.session_state: st.session_state.search_kw      = ""
    if "selected_code"  not in st.session_state: st.session_state.selected_code  = None
    if "selected_name"  not in st.session_state: st.session_state.selected_name  = None
    if "search_results" not in st.session_state: st.session_state.search_results = []

    # ── 搜索框 ────────────────────────────────────────
    st.markdown("**基金名称**")
    kw = st.text_input("search_input", placeholder="输入基金名称或代码搜索…",
                       label_visibility="collapsed",
                       value=st.session_state.search_kw)

    if kw != st.session_state.search_kw:
        st.session_state.search_kw      = kw
        st.session_state.selected_code  = None
        st.session_state.selected_name  = None
        if kw.strip():
            with st.spinner("搜索中…"):
                st.session_state.search_results = search_fund(kw.strip())
        else:
            st.session_state.search_results = []

    # ── 搜索结果列表 ──────────────────────────────────
    if st.session_state.search_results:
        options = {f"{r['name']}（{r['code']}）": r["code"]
                   for r in st.session_state.search_results}
        chosen = st.radio("选择基金", list(options.keys()),
                          label_visibility="collapsed", key="fund_radio")
        st.session_state.selected_code = options[chosen]
        st.session_state.selected_name = chosen.split("（")[0]
    elif kw.strip() and not st.session_state.search_results:
        st.caption("未找到匹配基金")

    # ── 金额填写（选中基金后显示）─────────────────────
    if st.session_state.selected_code:
        st.markdown(f"✅ **{st.session_state.selected_name}** `{st.session_state.selected_code}`")
        st.markdown("**持有金额**")
        new_amount = st.number_input("持有金额", min_value=0.0, value=0.0,
                                     step=100.0, format="%.2f",
                                     label_visibility="collapsed", key="amt_input")
        st.markdown("**持有收益**")
        new_profit = st.number_input("持有收益", value=0.0, step=10.0,
                                     format="%.2f", label_visibility="collapsed",
                                     key="profit_input")
        with st.expander("🔧 高级设置"):
            new_threshold = st.slider("止损阈值（%）", -30, -1, -10, key="thresh_input")

        if st.button("💾 保存", use_container_width=True, key="save_btn"):
            if new_amount <= 0:
                st.error("持有金额必须大于 0")
            else:
                code = st.session_state.selected_code
                name = st.session_state.selected_name
                cost_amount = new_amount - new_profit
                pnl_pct     = (new_profit / cost_amount * 100) if cost_amount != 0 else 0
                holdings[code] = {
                    "name": name, "amount": new_amount,
                    "profit": new_profit, "cost_amount": cost_amount,
                    "pnl_pct": pnl_pct, "threshold": new_threshold / 100,
                }
                save_holdings(holdings)
                st.session_state.search_kw      = ""
                st.session_state.selected_code  = None
                st.session_state.selected_name  = None
                st.session_state.search_results = []
                st.success(f"✅ {name} 已保存")
                st.rerun()

    # ── 当前持仓管理 ──────────────────────────────────
    st.divider()
    st.subheader("📋 当前持仓管理")
    if not holdings:
        st.info("暂无持仓，请在上方添加基金")
    else:
        for code, info in list(holdings.items()):
            # 使用 expander 包装，方便展开进行加减仓和修改金额操作
            with st.expander(f"{info['name']} ({code})"):
                profit = info.get("profit", 0)
                amount = info.get("amount", 0)
                profit_str = f"+¥{profit:.2f}" if profit >= 0 else f"-¥{abs(profit):.2f}"
                st.markdown(f"<div style='font-size:0.85rem;color:#555;'>当前金额: <b>¥{amount:.2f}</b> | 当前收益: <b>{profit_str}</b></div>", unsafe_allow_html=True)
                
                # 修改表单
                new_amount = st.number_input("修改总持有金额（减仓/加仓直接修改金额）", 
                                             value=float(amount), step=100.0, format="%.2f", 
                                             key=f"edit_amt_{code}")
                new_profit = st.number_input("修改总持有收益", 
                                             value=float(profit), step=10.0, format="%.2f", 
                                             key=f"edit_prof_{code}")
                
                c1, c2 = st.columns(2)
                if c1.button("💾 保存", key=f"save_{code}", use_container_width=True):
                    if new_amount <= 0:
                        st.error("持有金额需大于0")
                    else:
                        cost_amount = new_amount - new_profit
                        pnl_pct = (new_profit / cost_amount * 100) if cost_amount != 0 else 0
                        holdings[code].update({
                            "amount": new_amount,
                            "profit": new_profit,
                            "cost_amount": cost_amount,
                            "pnl_pct": pnl_pct
                        })
                        save_holdings(holdings)
                        st.rerun()
                if c2.button("🗑️ 删除", key=f"del_{code}", use_container_width=True):
                    del holdings[code]
                    save_holdings(holdings)
                    st.rerun()

    st.divider()
    st.caption(f"自动刷新间隔：{REFRESH_INTERVAL // 60} 分钟")

# ════════════════════════════════════════════════════════
#  主页面
# ════════════════════════════════════════════════════════
holdings = load_holdings()

# 倒计时占位符放最顶部，避免每秒更新时页面滚到底部
refresh_ph = st.empty()

# 并行抓取基金估值，减少首次加载等待
oil_price = get_oil_price()
fund_data = {}
if holdings:
    cache = st.session_state.get("fund_cache", {})
    now_ts = time.time()
    missing = []
    for code in holdings:
        item = cache.get(code)
        if item and (now_ts - item.get("ts", 0) < 55):
            fund_data[code] = item.get("data")
        else:
            missing.append(code)

    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(missing))) as executor:
            futures = {executor.submit(fetch_fund_valuation, code): code for code in missing}
            for future in concurrent.futures.as_completed(futures):
                code = futures[future]
                try:
                    data = future.result()
                except Exception:
                    data = None
                fund_data[code] = data
                cache[code] = {"ts": time.time(), "data": data}

    st.session_state["fund_cache"] = cache

# 收盘后自动结算今日收益
if is_market_closed():
    if apply_close_update(holdings, fund_data):
        holdings = load_holdings()
# 原油历史数据按需加载，避免首次加载阻塞

# ── 顶部总资产卡片 ────────────────────────────────────
total_amount = sum(h.get("amount", 0) for h in holdings.values())
total_profit = sum(h.get("profit", 0) for h in holdings.values())
total_cost   = sum(h.get("cost_amount", 0) for h in holdings.values())
total_pct    = (total_profit / total_cost * 100) if total_cost > 0 else 0
today_total  = sum(
    h.get("amount", 0) * (fund_data[c]["rate"] / 100)
    for c, h in holdings.items() if fund_data.get(c) and "rate" in fund_data[c]
)
profit_color = pnl_color(total_profit)
today_color  = pnl_color(today_total)

st.markdown(f"""
<div class="asset-card">
  <div style="display:flex; justify-content:space-between; align-items:flex-start">
    <div>
      <div class="asset-label">账户资产</div>
      <div class="asset-total">¥{total_amount:,.2f}</div>
      <div class="asset-sub">持仓成本 ¥{total_cost:,.2f}</div>
    </div>
    <div style="text-align:right">
      <div class="asset-label">持有收益</div>
      <div style="font-size:1.5rem;font-weight:700;color:{profit_color}">{fmt_signed(total_profit)}</div>
      <div style="font-size:0.82rem;color:{profit_color}">{fmt_signed(total_pct, prefix='', decimals=2)}%</div>
    </div>
  </div>
  {"" if not any(fund_data.values()) else f'''
  <div style="margin-top:12px;padding-top:12px;border-top:1px solid #F0F0F0;
              display:flex;justify-content:space-between;align-items:center">
    <span style="color:#999;font-size:0.78rem">今日估算收益</span>
    <span style="color:{today_color};font-size:1.0rem;font-weight:600">{fmt_signed(today_total)}</span>
  </div>'''}
</div>
""", unsafe_allow_html=True)

# ── 原油小条 ──────────────────────────────────────────
if oil_price:
    if oil_price < 95:
        oil_tip, oil_c = "💡 原油跌破 $95，利好芯片 / AI", "#2EAD6A"
    elif oil_price > 115:
        oil_tip, oil_c = "⚠️ 原油冲破 $115，警惕科技杀跌", "#E84040"
    else:
        oil_tip, oil_c = "原油价格正常区间", "#888"
    st.markdown(f"""
    <div class="oil-card">
      🛢️ <span>布伦特原油</span>
      <span class="oil-price">${oil_price:.2f}</span>
      <span style="color:{oil_c};font-size:0.82rem">{oil_tip}</span>
    </div>""", unsafe_allow_html=True)

# ── 原油历史折线图 ───────────────────────────────────
with st.expander("📉 原油历史走势", expanded=False):
    if "oil_period" not in st.session_state:
        st.session_state.oil_period = "1个月"
    period_map = {"1个月": "1mo", "3个月": "3mo", "1年": "1y"}
    selected = st.radio("时间范围", list(period_map.keys()),
                        index=list(period_map.keys()).index(st.session_state.oil_period),
                        horizontal=True, key="oil_range",
                        label_visibility="collapsed")
    st.session_state.oil_period = selected
    oil_hist_df = get_oil_history(period_map[selected])
    if oil_hist_df is not None:
        hi = float(oil_hist_df["布伦特原油（$）"].max())
        lo = float(oil_hist_df["布伦特原油（$）"].min())
        c1, c2, c3 = st.columns(3)
        c1.metric("当前价",  f"${oil_price:.2f}" if oil_price else "--")
        c2.metric(f"{selected}最高", f"${hi:.2f}")
        c3.metric(f"{selected}最低", f"${lo:.2f}")
        st.line_chart(oil_hist_df, use_container_width=True, height=200)
    else:
        st.caption("暂无历史数据")

# ── AI 决策区 ────────────────────────────────────────
st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

# 现金储备（存在 session_state 里，不用每次重填）
if "cash_reserve" not in st.session_state:
    st.session_state.cash_reserve = 0.0

with st.expander("🤖 AI 盘中决策建议", expanded=False):
    cash_col, btn_col = st.columns([2, 1])
    with cash_col:
        cash_input = st.number_input(
            "现金储备（元）", min_value=0.0,
            value=st.session_state.cash_reserve,
            step=1000.0, format="%.0f",
            key="cash_input", label_visibility="visible"
        )
        st.session_state.cash_reserve = cash_input
    with btn_col:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("🧹 清空对话", use_container_width=True, key="ai_clear"):
            st.session_state["ai_chat"] = []
            st.rerun()

    if "ai_chat" not in st.session_state:
        st.session_state.ai_chat = []

    for msg in st.session_state.ai_chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_msg = st.chat_input("输入你的问题，比如：今天要不要减仓？")
    if user_msg:
        st.session_state.ai_chat.append({"role": "user", "content": user_msg})
        with st.chat_message("assistant"):
            with st.spinner("AI 正在分析持仓与市场环境…"):
                try:
                    context = generate_ai_brief(
                        holdings, fund_data, oil_price,
                        total_amount, total_pct, cash_input
                    )
                    prompt = f"""{context}

用户问题：{user_msg}
请只回答用户问题，并结合当前持仓与市场环境给出建议。"""
                    try:
                        ai_reply = call_ai(prompt, ai_provider)
                    except Exception as e:
                        if ai_provider == "Gemini":
                            try:
                                ai_reply = call_qwen(prompt)
                            except Exception as e2:
                                ai_reply = f"AI 调用失败：{e}；备用失败：{e2}"
                        else:
                            try:
                                ai_reply = call_gemini(prompt)
                            except Exception as e2:
                                ai_reply = f"AI 调用失败：{e}；备用失败：{e2}"
                except Exception as e:
                    ai_reply = f"AI 调用失败：{e}"
            st.markdown(ai_reply)
        st.session_state.ai_chat.append({"role": "assistant", "content": ai_reply})

# ── 列头 ─────────────────────────────────────────────
if holdings:
    today_str = datetime.now().strftime("%m-%d")
    st.markdown(f"""
    <div class="list-header">
      <span>基金</span>
      <span>今日涨跌&nbsp;&nbsp;{today_str}</span>
      <span>持有收益&nbsp;&nbsp;{today_str}</span>
    </div>""", unsafe_allow_html=True)

# ── 基金列表 ─────────────────────────────────────────
today_key = datetime.now().strftime("%Y-%m-%d")
if st.session_state.get("daily_rebuilt_at") != today_key:
    backfill_history_last_month()
    st.session_state["daily_rebuilt_at"] = today_key

if not holdings:
    st.info("👈 请在左侧添加持仓")
else:
    sort_mode = st.radio(
        "排序方式",
        ["默认", "按金额", "按收益率"],
        horizontal=True,
        key="holdings_sort_mode",
        label_visibility="collapsed",
    )

    history = None
    for code in holdings:
        if f"chart_{code}" not in st.session_state:
            st.session_state[f"chart_{code}"] = False
        if f"range_{code}" not in st.session_state:
            st.session_state[f"range_{code}"] = "1个月"

    if any(st.session_state.get(f"chart_{code}", False) for code in holdings):
        history = backfill_history_last_month()
    else:
        history = {}

    holding_items = list(holdings.items())
    if sort_mode == "按金额":
        holding_items.sort(key=lambda x: x[1].get("amount", 0), reverse=True)
    elif sort_mode == "按收益率":
        holding_items.sort(key=lambda x: x[1].get("pnl_pct", 0), reverse=True)

    for code, info in holding_items:
        res      = fund_data.get(code)
        amount   = info.get("amount", 0)
        profit   = info.get("profit", 0)
        pnl_pct  = info.get("pnl_pct", 0)
        is_alert = pnl_pct / 100 < info["threshold"]
        pc       = pnl_color(profit)
        alert_html = f'<div class="alert-badge">⚠️ 触发止损线 {pnl_pct:.2f}%</div>' if is_alert else ""

        if res and "rate" in res:
            today_rate = res["rate"]
            today_amt  = amount * today_rate / 100
            tc         = pnl_color(today_rate)
            today_html = f"""
              <div class="today-amount" style="color:{tc}">{fmt_signed(today_amt)}</div>
              <div class="today-rate"   style="color:{tc}">{fmt_signed(today_rate, prefix='', decimals=2)}%</div>"""
        else:
            today_html = '<div class="today-amount" style="color:#ccc">--</div>'

        is_open   = st.session_state[f"chart_{code}"]
        arrow     = "∧" if is_open else "∨"
        # ✅ 修复：有1条记录就显示箭头（之前要求>1条）
        has_chart = code in history and len(history[code]["records"]) >= 1

        # 卡片 + 箭头按钮同行
        card_col, btn_col = st.columns([20, 1])
        with card_col:
            st.markdown(f"""
            <div class="fund-row">
              <div class="fund-left">
                <div class="fund-name">{info['name']}</div>
                <div class="fund-value">¥{amount:,.2f}</div>
                {alert_html}
              </div>
              <div class="fund-mid">{today_html}</div>
              <div class="fund-right">
                <div class="profit-amount" style="color:{pc}">{fmt_signed(profit)}</div>
                <div class="profit-rate"   style="color:{pc}">{fmt_signed(pnl_pct, prefix='', decimals=2)}%</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

        with btn_col:
            st.markdown("<div style='margin-top:18px'></div>", unsafe_allow_html=True)
            if st.button(arrow, key=f"toggle_{code}"):
                st.session_state[f"chart_{code}"] = not is_open
                st.rerun()

        # ── 图表区（展开时显示）──────────────────────────
        if is_open and has_chart:
            st.markdown("""<div style="background:#fff;border-radius:0 0 14px 14px;
                padding:8px 16px 16px;margin-top:-2px;
                box-shadow:0 2px 6px rgba(0,0,0,0.06)">""",
                unsafe_allow_html=True)

            # 时间范围选择（与原油一致）
            ranges = ["1个月", "3个月", "1年"]
            days_map = {"1个月": 30, "3个月": 90, "1年": 365}
            selected_range = st.radio(
                "时间范围", ranges,
                index=ranges.index(st.session_state[f"range_{code}"]),
                horizontal=True, key=f"radio_{code}",
                label_visibility="collapsed"
            )
            st.session_state[f"range_{code}"] = selected_range

            price_df = get_fund_price_history(code, selected_range)
            if price_df is not None and not price_df.empty:
                st.line_chart(price_df, use_container_width=True)
            else:
                cutoff = datetime.now() - timedelta(days=days_map[selected_range])
                daily = get_daily_series(code, history[code]["records"])
                daily = daily[daily.index >= cutoff]

                if len(daily) >= 1:
                    price_series = None
                    if "dwjz" in daily.columns and (daily["dwjz"] > 0).any():
                        price_series = daily["dwjz"].replace(0, pd.NA).dropna()
                    elif "gz" in daily.columns:
                        price_series = daily["gz"].replace(0, pd.NA).dropna()

                    if price_series is not None and not price_series.empty:
                        st.line_chart(format_daily_index(price_series), use_container_width=True)
                    else:
                        st.caption("暂无价格数据")
                else:
                    st.caption("暂无价格数据")

            st.markdown("</div>", unsafe_allow_html=True)

        if res and res.get("gz", 0) > 0:
            append_record(
                code,
                info["name"],
                pnl_pct,
                res.get("gz", 0),
                jzrq=res.get("jzrq", ""),
                dwjz=res.get("dwjz", 0),
            )

# ── 底部：手动刷新 + 自动刷新倒计时（非阻塞）──────────────
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
col_btn, col_settle, col_tip = st.columns([1, 1, 2])

if "refresh_started_at" not in st.session_state:
    st.session_state.refresh_started_at = time.time()

elapsed = time.time() - st.session_state.refresh_started_at
if elapsed >= REFRESH_INTERVAL:
    clear_fund_cache()
    st.session_state.refresh_started_at = time.time()
    st.rerun()

with col_btn:
    if st.button("🔄 手动刷新", use_container_width=True):
        clear_fund_cache()
        st.session_state.refresh_started_at = time.time()
        st.rerun()

with col_settle:
    if st.button("✅ 结算收益", use_container_width=True):
        if apply_close_update(holdings, fund_data, force=True):
            st.success("已将今日收益并入本金，并更新收益率")
        else:
            st.info("暂无可结算数据（可能今日已结算或未获取到涨跌幅）")
        st.rerun()

remaining = REFRESH_INTERVAL - int(time.time() - st.session_state.refresh_started_at)
remaining = max(0, remaining)
mins = remaining // 60
secs = remaining % 60
refresh_ph.markdown(
    f'<div class="refresh-tip">⏱ {mins}:{secs:02d} 后自动刷新　｜　'
    f'{datetime.now().strftime("%H:%M:%S")}</div>',
    unsafe_allow_html=True)

# 用 JS 触发自动刷新，避免 time.sleep 阻塞导致页面卡顿
components.html(
    f"""
    <script>
      setTimeout(() => {{ window.parent.location.reload(); }}, {REFRESH_INTERVAL * 1000});
    </script>
    """,
    height=0,
    width=0,
)