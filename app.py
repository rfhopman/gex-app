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
NTFY_TOPIC = "GEX_Alerts" 

def send_iphone_notification(ticker, exp, spot, call_w, put_w):
    # Compact one-line format for iPhone lock screen
    msg = f"🚨 {ticker} ({exp}): Spot ${spot:.2f} | CW ${call_w:.2f} | PW ${put_w:.2f}"
    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}", 
            data=msg.encode('utf-8'),
            timeout=10
        )
        return response.status_code
    except:
        return "Error"

# --- AUTO-REFRESH LOGIC (2 PM - 4:15 PM EST) ---
now_est = datetime.now(ZoneInfo("America/New_York"))
start_time = time(14, 0)
end_time = time(16, 15)

if start_time <= now_est.time() <= end_time:
    from streamlit_autorefresh import st_autorefresh
    # Refresh every 15 minutes (900,000 milliseconds)
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
def get_market_data():
    try:
        irx = yf.Ticker("^IRX")
        vix = yf.Ticker("^VIX")
        # Fetch risk-free rate
        r_rate = (irx.fast_info.get("last_price") or irx.history(period="1d")["Close"].iloc[-1]) / 100
        # Fetch VIX price
        vix_val = vix.fast_info.get("last_price") or vix.history(period="1d")["Close"].iloc[-1]
        return float(r_rate), float(vix_val)
    except: 
        return 0.04, 0.0

# --- TOP ROW CONTROLS ---
st.title("📊 GEX DASHBOARD")

with st.sidebar:
    st.write("### Notification Center")
    st.info(f"Topic: {NTFY_TOPIC}")
    if st.button("🔔 Send Test Notification"):
        res = send_iphone_notification("TEST", "2026-04-17", 0.00, 0.00, 0.00)
        if res == 200: st.success("Sent successfully!")
        else: st.error("Failed to send.")

ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1, 1.5, 1, 0.5])

with ctrl_col1:
    ticker_input = st.text_input("Ticker", value="XSP").upper()
with ctrl_col2:
    strike_option = st.radio("Strikes to View", options=[10, 20, 40, 60, "All"], index=2, horizontal=True)
with ctrl_col3:
    min_oi_visual = st.radio("Min Contracts", options=[1, 5], index=0, horizontal=True)
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
    
    # Fetch Market Data (Risk Free and VIX)
    risk_free, vix_price = get_market_data()
    
    all_exps = tk.options
    if not all_exps:
        st.error("No options found.")
        st.stop()

    selected_exp = st.selectbox("Select Expiration Date", all_exps)

    # --- DATA PROCESSING ---
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list = []
    table_rows = []
    
    for opt_type, df_raw in [("Call", chain.calls), ("Put", chain.puts)]:
        df = df_raw.copy()
        for col in ["strike", "openInterest", "volume", "impliedVolatility"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        for _, row in df.iterrows():
            K, OI, iv, vol = float(row["strike"]), float(row["openInterest"]), float(row["impliedVolatility"]), float(row["volume"])
            if iv <= 0 or K <= 0: continue
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 10
