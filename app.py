import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
import requests
import time as pytime

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX Dashboard Pro", page_icon="📊", layout="wide")

# --- CUSTOM NOTIFICATION LOGIC ---
# Change this to something unique so only YOU get the alerts
NTFY_TOPIC = "GEX_alerts" 

def send_iphone_notification(ticker, exp, spot, call_w, put_w):
    msg = f"Spot: ${spot:.2f} | CallWall: ${call_w:.2f} | PutWall: ${put_w:.2f}"
    title = f"🚨 {ticker} Update ({exp})"
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", 
                      data=msg.encode('utf-8'),
                      headers={"Title": title, "Priority": "high", "Tags": "chart_with_upwards_trend"})
    except: pass

# --- AUTO-REFRESH LOGIC (2 PM - 4:15 PM EST) ---
now_est = datetime.now(ZoneInfo("America/New_York"))
start_time = time(14, 0)
end_time = time(16, 15)

# If we are in the window, trigger a refresh every 15 mins (900 seconds)
if start_time <= now_est.time() <= end_time:
    # This component pings the server to rerun the script
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=15 * 60 * 1000, key="market_close_refresh")

# --- Helpers ---
def bs_gamma(S, K, T, r, iv):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0: return 0.0
    d1 = (math.log(S/K) + (r + 0.5*iv*iv)*T) / (iv*math.sqrt(T))
    return ((1.0 / math.sqrt(2*math.pi)) * math.exp(-0.5*d1*d1)) / (S * iv * math.sqrt(T))

def fmt_gex(v):
    a, s = abs(v), ("+" if v >= 0 else "−")
    if a >= 1e9: return f"{s}${a/1e9:.2f}B"
    if a >= 1e6: return f"{s}${a/1e6:.1f}M"
    return f"{s}${a:.0f}"

@st.cache_data(ttl=300)
def get_risk_free_rate():
    try:
        irx = yf.Ticker("^IRX")
        rate = irx.fast_info.get("last_price") or irx.history(period="1d")["Close"].iloc[-1]
        return float(rate) / 100
    except: return 0.04

# --- TOP ROW CONTROLS ---
st.title("📊 GEX DASHBOARD")

ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1, 1.5, 1, 0.5])

with ctrl_col1:
    ticker_input = st.text_input("Ticker", value="XSP").upper()

with ctrl_col2:
    strike_option = st.radio("Strikes to View", options=[10, 20, 40, 60, "All"], index=2, horizontal=True)

with ctrl_col3:
    min_oi_visual = st.radio("Min Contracts (Visual Only)", options=[1, 5], index=0, horizontal=True)

with ctrl_col4:
    st.write("") 
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

try:
    search_ticker = ticker_input
    if ticker_input == "XSP": search_ticker = "^XSP"
    tk = yf.Ticker(search_ticker)
    
    try:
        raw_ts = tk.info.get('regularMarketTime') or tk.fast_info.get("last_price_timestamp")
        market_time = datetime.fromtimestamp(raw_ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%I:%M:%S %p %Z")
    except: market_time = "N/A"

    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    all_exps = tk.options
    
    if not all_exps:
        st.error("No options found.")
        st.stop()

    selected_exp = st.selectbox("Select Expiration Date", all_exps)

    # --- DATA PROCESSING ---
    risk_free = get_risk_free_rate()
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list = []
    
    for opt_type, df_raw in [("Call", chain.calls), ("Put", chain.puts)]:
        df = df_raw.copy()
        for col in ["strike", "openInterest", "volume", "impliedVolatility"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        for _, row in df.iterrows():
            K, OI, iv, vol = float(row["strike"]), float(row["openInterest"]), float(row["impliedVolatility"]), float(row["volume"])
            if iv <= 0 or K <= 0: continue
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({"strike": K, "gex": gex if opt_type == "Call" else -gex, "type": opt_type, "oi": OI, "vol": vol})

    df_main = pd.DataFrame(main_list)
    df_calc = df_main.groupby("strike")["gex"].sum().reset_index().sort_values("strike")
    
    gamma_flip = None
    if not df_calc.empty:
        for i in range(len(df_calc)-1):
            g1, g2 = df_calc.iloc[i]["gex"], df_calc.iloc[i+1]["gex"]
            if g1 * g2 < 0:
                s1, s2 = df_calc.iloc[i]["strike"], df_calc.iloc[i+1]["strike"]
                gamma_flip = s1 - g1 * (s2 - s1) / (g2 - g1)
                break

    net_total = df_calc["gex"].sum() if not df_calc.empty else 0
    call_wall = df_calc.loc[df_calc["gex"].idxmax(), "strike"] if not df_calc.empty else 0
    put_wall = df_calc.loc[df_calc["gex"].idxmin(), "strike"] if not df_calc.empty else 0
    
    # --- AUTO-NOTIFICATION TRIGGER ---
    # We trigger a notification if we are in the time window
    if start_time <= now_est.time() <= end_time:
        if "last_notif" not in st.session_state or (pytime.time() - st.session_state.last_notif) > 800:
            send_iphone_notification(ticker_input, selected_exp, spot, call_wall, put_wall)
            st.session_state.last_notif = pytime.time()

    # (Rest of visualization logic remains the same...)
    regime_val = "POSITIVE" if net_total >= 0 else "NEGATIVE"
    bg_color = "#d4edda" if net_total >= 0 else "#f8d7da"
    text_color = "#155724" if net_total >= 0 else "#721c24"

    st.write("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Flip", f"${gamma_flip:.2f}" if gamma_flip else "N/A")
    m3.metric("Net GEX", fmt_gex(net_total))
    m4.metric("Call-Wall", f"${call_wall:.2f}")
    with m5:
        st.markdown(f'<div style="background-color: {bg_color}; padding: 10px; border-radius: 5px; text-align: center; border: 1px solid {text_color};"><p style="margin:0; font-size:14px; color: #555;">Regime</p><p style="margin:0; font-size:20px; font-weight:bold; color: {text_color};">{regime_val}</p></div>', unsafe_allow_html=True)
    m6.metric("Put-Wall", f"${put_wall:.2f}")

    # (Chart plotting code continues here...)
    fig_main = go.Figure()
    df_visual = df_main[df_main['oi'] >= min_oi_visual]
    fig_main.add_trace(go.Scatter(x=df_visual[df_visual['type'] == 'Call']["strike"], y=df_visual[df_visual['type'] == 'Call']["vol"], fill='tozeroy', mode='none', fillcolor='rgba(173, 216, 230, 0.25)', name="Call Vol", yaxis="y2"))
    fig_main.add_trace(go.Scatter(x=df_visual[df_visual['type'] == 'Put']["strike"], y=df_visual[df_visual['type'] == 'Put']["vol"], fill='tozeroy', mode='none', fillcolor='rgba(255, 182, 193, 0.25)', name="Put Vol", yaxis="y2"))
    fig_main.add_trace(go.Bar(x=df_visual[df_visual['type'] == 'Call']["strike"], y=df_visual[df_visual['type'] == 'Call']["gex"], marker_color="#4db6ac", name="Call GEX", hovertemplate="Strike: %{x}<br>GEX: %{y:,.0f}<extra></extra>"))
    fig_main.add_trace(go.Bar(x=df_visual[df_visual['type'] == 'Put']["strike"], y=df_visual[df_visual['type'] == 'Put']["gex"], marker_color="#e57373", name="Put GEX", hovertemplate="Strike: %{x}<br>GEX: %{y:,.0f}<extra></extra>"))
    fig_main.update_layout(template="plotly_dark", height=450, yaxis2=dict(overlaying="y", side="right", showgrid=False))
    st.plotly_chart(fig_main, use_container_width=True)

except Exception as e:
    st.error(f"Error: {e}")
