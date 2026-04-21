import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
import requests
from scipy.stats import norm

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX, VEX, DEX & CEX Dashboard", page_icon="📊", layout="wide")

# --- CUSTOM NOTIFICATION LOGIC ---
NTFY_TOPIC = "GEX_Alerts" 

def send_iphone_notification(ticker, exp, spot, call_w, put_w):
    msg = f"🚨 {ticker} ({exp}): Spot ${spot:.2f} | CW ${call_w:.2f} | PW ${put_w:.2f}"
    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}", 
            data=msg.encode('utf-8'),
            headers={"Title": f"Market Update: {ticker}", "Priority": "high"},
            timeout=10
        )
        return response.status_code
    except: return "Error"

# --- AUTO-REFRESH & WEEKDAY LOGIC ---
now_est = datetime.now(ZoneInfo("America/New_York"))
is_weekday = now_est.weekday() <= 4  
start_time = time(14, 0)
end_time = time(16, 15)
is_market_active = is_weekday and (start_time <= now_est.time() <= end_time)

if is_market_active:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=15 * 60 * 1000, key="market_close_refresh")

# --- HELPERS ---
def bs_greeks(S, K, T, r, iv, opt_type="Call"):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0: return 0.0, 0.0, 0.0, 0.0
    d1 = (math.log(S/K) + (r + 0.5*iv*iv)*T) / (iv*math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    pdf = (1.0 / math.sqrt(2*math.pi)) * math.exp(-0.5*d1*d1)
    gamma = pdf / (S * iv * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) * 0.01 
    delta = norm.cdf(d1) if opt_type == "Call" else norm.cdf(d1) - 1
    charm = -pdf * ( (r / (iv * math.sqrt(T))) - (d2 / (2 * T)) )
    if opt_type == "Put": charm = charm + (r * norm.cdf(-d1))
    return gamma, vega, delta, charm

def fmt_val(v):
    a, s = abs(v), ("+" if v >= 0 else "−")
    if a >= 1e9: return f"{s}$ {a/1e9:.2f}B"
    if a >= 1e6: return f"{s}$ {a/1e6:.1f}M"
    return f"{s}$ {a:.0f}"

@st.cache_data(ttl=300)
def get_market_metrics():
    try:
        irx, vix = yf.Ticker("^IRX"), yf.Ticker("^VIX")
        rate = irx.fast_info.get("last_price") or 4.5
        vix_val = vix.fast_info.get("last_price") or 15.0
        return float(rate) / 100, float(vix_val)
    except: return 0.045, 15.0

# Aggressive cache for Intraday to prevent 429
@st.cache_data(ttl=3600) 
def get_vix_intraday_cached():
    try:
        return yf.download("^VIX", period="1d", interval="15m", progress=False)
    except: return pd.DataFrame()

# --- UI CONTROLS ---
st.title("📊 GEX, VEX, DEX & CEX DASHBOARD")

with st.sidebar:
    st.write("### Notification Center")
    st.write(f"Status: {'🟢 Live' if is_market_active else '🔴 Silenced'}")
    if st.button("🔔 Send Test Alert"):
        send_iphone_notification("TEST", "2026-04-20", 0.0, 0.0, 0.0)

ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1, 1.5, 1, 0.5])
with ctrl_col1: ticker_input = st.text_input("Ticker", value="XSP").upper()
with ctrl_col2: strike_option = st.radio("Strikes", options=[10, 20, 40, 60, "All"], index=2, horizontal=True)
with ctrl_col3: min_oi_visual = st.radio("Min OI", options=[1, 5], index=0, horizontal=True)
with ctrl_col4: 
    st.write("")
    if st.button("🔄"): st.cache_data.clear(); st.rerun()

try:
    search_tk = "^XSP" if ticker_input == "XSP" else ticker_input
    tk = yf.Ticker(search_tk)
    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    all_exps = tk.options
    selected_exp = st.selectbox("Expiration", all_exps)

    # --- PROCESSING ---
    rf, vix_price = get_market_metrics()
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list, table_rows = [], []
    
    for opt_type, df_raw in [("Call", chain.calls), ("Put", chain.puts)]:
        df = df_raw.copy()
        for _, row in df.iterrows():
            K, OI, iv = float(row["strike"]), float(row["openInterest"]), float(row["impliedVolatility"])
            if iv <= 0 or K <= 0: continue
            g, v, d, c = bs_greeks(spot, K, T, rf, iv, opt_type)
            gex = g * OI * 100 * spot * spot * 0.01
            vex, dex, cex = v * OI * 100, d * OI * 100 * spot, c * OI * 100 * spot
            
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({"strike": K, "gex": gex if opt_type=="Call" else -gex, "vex": vex, "dex": dex, "cex": cex, "type": opt_type, "oi": OI})
            table_rows.append({"Strike": K, "Type": opt_type, "OI": int(OI), "GEX": gex if opt_type=="Call" else -gex, "VEX": vex, "DEX": dex, "CEX": cex})

    df_main = pd.DataFrame(main_list)
    df_calc = df_main.groupby("strike").sum(numeric_only=True).reset_index()
    net_gex = df_calc["gex"].sum()
    call_wall = df_calc.loc[df_calc["gex"].idxmax(), "strike"]
    put_wall = df_calc.loc[df_calc["gex"].idxmin(), "strike"]

    # --- TOP METRICS ---
    st.write("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Net GEX", fmt_val(net_gex))
    m3.metric("CW", f"${call_wall:.2f}")
    m4.metric("PW", f"${put_wall:.2f}")

    # --- CHARTS (RESTORED TEXT) ---
    st.subheader("Gamma Exposure (GEX)")
    fig_gex = go.Figure()
    fig_gex.add_trace(go.Bar(x=df_main[df_main['type']=='Call']["strike"], y=df_main[df_main['type']=='Call']["gex"], name="Call", marker_color="#4db6ac"))
    fig_gex.add_trace(go.Bar(x=df_main[df_main['type']=='Put']["strike"], y=df_main[df_main['type']=='Put']["gex"], name="Put", marker_color="#e57373"))
    fig_gex.update_layout(template="plotly_dark", height=400, barmode='relative')
    st.plotly_chart(fig_gex, use_container_width=True)
    with st.expander("📝 GEX Outcome & Usage"):
        st.write("**Outcome:** Identifies supply/demand zones. **Usage:** Positive GEX = Range-bound; Negative GEX = Trending.")

    # VEX
    st.subheader("VEX Profile")
    fig_vex = go.Figure(go.Bar(x=df_calc["strike"], y=df_calc["vex"], marker_color='#bb86fc'))
    fig_vex.update_layout(template="plotly_dark", height=300)
    st.plotly_chart(fig_vex, use_container_width=True)
    st.metric("Total VEX", fmt_val(df_calc["vex"].sum()))
    with st.expander("📝 VEX Outcome & Usage"):
        st.write("**Outcome:** Volatility sensitivity. **Usage:** High VEX spikes help identify strikes that decay rapidly if IV drops.")

    # DEX
    st.subheader("DEX Profile")
    fig_dex = go.Figure(go.Bar(x=df_calc["strike"], y=df_calc["dex"], marker_color="#ffa726"))
    fig_dex.update_layout(template="plotly_dark", height=300)
    st.plotly_chart(fig_dex, use_container_width=True)
    st.metric("Total DEX", fmt_val(df_calc["dex"].sum()))
    with st.expander("📝 DEX Outcome & Usage"):
        st.write("**Outcome:** Directional market pressure. **Usage:** Positive DEX = 'Sticky'; Negative DEX = 'Slippery'.")

    # CEX
    st.subheader("CEX Profile")
    fig_cex = go.Figure(go.Bar(x=df_calc["strike"], y=df_calc["cex"], marker_color='#03dac6'))
    fig_cex.update_layout(template="plotly_dark", height=300)
    st.plotly_chart(fig_cex, use_container_width=True)
    st.metric("Total CEX", fmt_val(df_calc["cex"].sum()))
    with st.expander("📝 CEX Outcome & Usage"):
        st.write("**Outcome:** Shows where Delta is bleeding off due to time. **Usage:** High CEX near strikes accelerates OTM decay.")

    # --- VIX INTRADAY (THROTTLED) ---
    st.write("---")
    st.subheader("📉 VIX Intraday")
    vix_data = get_vix_intraday_cached()
    if not vix_data.empty:
        # Check for multi-index columns common in yfinance 1.3+
        if isinstance(vix_data.columns, pd.MultiIndex): vix_data.columns = vix_data.columns.get_level_values(0)
        vix_data.index = vix_data.index.tz_convert(ZoneInfo("America/New_York"))
        st.line_chart(vix_data['Close'])
    else:
        st.warning("VIX data limited. API cooling down.")

except Exception as e:
    st.error(f"Throttled or Error: {e}")
  
