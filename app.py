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

ctrl_col1, ctrl_col2 = st.columns([1, 2])

with ctrl_col1:
    ticker_input = st.text_input("Ticker", value="XSP").upper()

with ctrl_col2:
    strike_option = st.radio(
        "Strikes to View", 
        options=[10, 20, 40, 60, "All"], 
        index=2,
        horizontal=True
    )

try:
    search_ticker = ticker_input
    if ticker_input == "XSP": search_ticker = "^XSP"
    tk = yf.Ticker(search_ticker)
    
    # Precise Market Time Fetch
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

    # Expiration for the TOP Chart
    selected_exp = st.selectbox("Select Top Chart Expiration", all_exps)

    # --- DATA PROCESSING ---
    risk_free = get_risk_free_rate()
    now_ts = datetime.now(timezone.utc).timestamp()
    
    # 1. Process Main Chart Data (Single Expiry)
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_strike_map = {}
    for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
        if df.empty: continue
        df = df[(df['strike'] >= spot * 0.8) & (df['strike'] <= spot * 1.2)]
        for _, row in df.iterrows():
            K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
            if OI <= 1 or iv <= 0: continue
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            if K not in main_strike_map: main_strike_map[K] = {"strike": K, "netGEX": 0.0}
            main_strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex

    df_main = pd.DataFrame(main_strike_map.values()).sort_values("strike")

    # 2. Process Heatmap Data (Next 10 Expirations)
    with st.spinner("Generating Heatmap..."):
        heatmap_exps = all_exps[:10]
        heatmap_list = []
        for exp in heatmap_exps:
            e_ts = datetime.strptime(exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
            T_heat = max((e_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
            c = tk.option_chain(exp)
            for opt_type, df_h in [("call", c.calls), ("put", c.puts)]:
                if df_h.empty: continue
                # Filter to match visible strikes
                df_h = df_h[(df_h['strike'] >= spot * 0.9) & (df_h['strike'] <= spot * 1.1)]
                for _, row in df_h.iterrows():
                    K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
                    if OI <= 1 or iv <= 0: continue
                    g = bs_gamma(spot, K, T_heat, risk_free, iv)
                    gex = g * OI * 100 * spot * spot * 0.01
                    heatmap_list.append({"expiry": exp, "strike": K, "netGEX": gex if opt_type == "call" else -gex})
        
        df_heat_long = pd.DataFrame(heatmap_list)
        df_pivot = df_heat_long.groupby(['expiry', 'strike'])['netGEX'].sum().unstack().fillna(0)

    # --- Filtering Logic ---
    if strike_option != "All":
        idx = (df_main['strike'] - spot).abs().idxmin()
        half = strike_option // 2
        df_main_view = df_main.iloc[max(0, idx-half): min(len(df_main), idx+half)]
        # Filter pivot columns to match
        visible_strikes = df_main_view['strike'].unique()
        df_pivot = df_pivot.loc[:, df_pivot.columns.isin(visible_strikes)]
    else:
        df_main_view = df_main

    # Metrics
    net_total = df_main["netGEX"].sum()
    call_wall = df_main.loc[df_main["netGEX"].idxmax(), "strike"]
    put_wall = df_main.loc[df_main["netGEX"].idxmin(), "strike"]
    regime = "POS" if net_total >= 0 else "NEG"

    # --- UI Layout ---
    st.write("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Expiry", selected_exp)
    m3.metric("Net GEX", fmt_gex(net_total))
    m4.metric("Call-Wall", f"${call_wall:.2f}")
    m5.metric("Regime", regime) 
    m6.metric("Put-Wall", f"${put_wall:.2f}")

    # --- Top Chart ---
    fig_main = go.Figure()
    fig_main.add_trace(go.Bar(
        x=df_main_view["strike"], y=df_main_view["netGEX"],
        marker_color=np.where(df_main_view["netGEX"] >= 0, "#4db6ac", "#e57373")
    ))
    fig_main.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    fig_main.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="Call-Wall")
    fig_main.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="Put-Wall")
    fig_main.update_layout(template="plotly_dark", height=450, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig_main, use_container_width=True)

    # --- Heatmap (White Background) ---
    st.subheader("Gamma Term Structure (Next 10 Expirations)")
    fig_heat = go.Figure(data=go.Heatmap(
        z=df_pivot.values, x=df_pivot.columns, y=df_pivot.index,
        colorscale='RdYlGn', zmid=0, colorbar=dict(title="Net GEX")
    ))
    fig_heat.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    fig_heat.update_layout(
        template="plotly_white", # WHITE BACKGROUND
        height=500,
        xaxis_title="Strike",
        yaxis_title="Expiration",
        margin=dict(l=10, r=10, t=10, b=10)
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    st.caption(f"Delayed 15m | Time: {market_time}")

except Exception as e:
    st.info("Loading ticker data...")
