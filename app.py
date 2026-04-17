import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX Dashboard Pro", layout="wide")

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
    strike_option = st.radio(
        "Strikes to View", 
        options=[10, 20, 40, 60, "All"], 
        index=2,
        horizontal=True
    )

with ctrl_col3:
    min_oi = st.radio("Min Contracts", options=[1, 5], index=0, horizontal=True)

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
    except:
        market_time = "N/A"

    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    all_exps = tk.options
    
    if not all_exps:
        st.error("No options found.")
        st.stop()

    selected_exp = st.selectbox("Select Expiration Date", all_exps)

    # --- DATA PROCESSING ---
    risk_free = get_risk_free_rate()
    now_ts = datetime.now(timezone.utc).timestamp()
    
    # 1. Main Chart Data & Table Data
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list = []
    table_rows = []
    
    for opt_type, df in [("Call", chain.calls), ("Put", chain.puts)]:
        if df.empty: continue
        df_filtered = df[df['openInterest'] > min_oi]
        
        for _, row in df_filtered.iterrows():
            K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
            vol = row.get("volume", 0) # Fetch volume if available
            if iv <= 0: continue
            
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({"strike": K, "gex": gex if opt_type == "Call" else -gex, "type": opt_type})
            
            # Formatting Data for Table
            table_rows.append({
                "Strike": K,
                "Type": opt_type,
                "OI": int(OI),
                "Volume": int(vol) if not np.isnan(vol) else 0,
                "IV": f"{iv*100:.2f}%", # Format IV as %
                "GEX": int(round(gex if opt_type == "Call" else -gex, 0)) # No decimals
            })

    df_main = pd.DataFrame(main_list)
    df_table_full = pd.DataFrame(table_rows).sort_values(["Strike", "Type"])
    
    if df_main.empty:
        st.warning(f"No strikes found with more than {min_oi} contracts.")
        st.stop()
        
    df_agg = df_main.groupby("strike")["gex"].sum().reset_index().sort_values("strike")

    # Gamma Flip
    gamma_flip = None
    for i in range(len(df_agg)-1):
        g1, g2 = df_agg.iloc[i]["gex"], df_agg.iloc[i+1]["gex"]
        if g1 * g2 < 0:
            s1, s2 = df_agg.iloc[i]["strike"], df_agg.iloc[i+1]["strike"]
            gamma_flip = s1 - g1 * (s2 - s1) / (g2 - g1)
            break

    # 2. Heatmap Data
    with st.spinner("Generating Gamma Heat Map..."):
        heatmap_exps = all_exps[:10]
        heatmap_list = []
        for exp in heatmap_exps:
            e_ts = datetime.strptime(exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
            T_heat = max((e_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
            try:
                c = tk.option_chain(exp)
                for opt_type, df_h in [("Call", c.calls), ("Put", c.puts)]:
                    df_h = df_h[df_h['openInterest'] > min_oi]
                    df_h = df_h[(df_h['strike'] >= spot * 0.9) & (df_h['strike'] <= spot * 1.1)]
                    for _, row in
