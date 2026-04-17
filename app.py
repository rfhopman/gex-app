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
            if iv <= 0: continue
            
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({"strike": K, "gex": gex if opt_type == "Call" else -gex, "type": opt_type})
            
            table_rows.append({
                "Strike": K,
                "Type": opt_type,
                "OI": int(OI),
                "IV": round(iv, 4),
                "GEX": round(gex if opt_type == "Call" else -gex, 2)
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

    # 2. Heatmap Data (Next 10)
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
                    for _, row in df_h.iterrows():
                        K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
                        if iv <= 0: continue
                        g = bs_gamma(spot, K, T_heat, risk_free, iv)
                        gex = g * OI * 100 * spot * spot * 0.01
                        heatmap_list.append({"expiry": exp, "strike": K, "netGEX": gex if opt_type == "Call" else -gex})
            except: continue
        
        df_heat_long = pd.DataFrame(heatmap_list)
        df_pivot = df_heat_long.groupby(['expiry', 'strike'])['netGEX'].sum().unstack().fillna(0)

    # Filter for Graph Display
    if strike_option != "All":
        idx = (df_agg['strike'] - spot).abs().idxmin()
        half = strike_option // 2
        visible_strikes = df_agg.iloc[max(0, idx-half): min(len(df_agg), idx+half)]['strike'].unique()
        df_main_view = df_main[df_main['strike'].isin(visible_strikes)]
        df_pivot = df_pivot.loc[:, df_pivot.columns.isin(visible_strikes)]
    else:
        df_main_view = df_main

    # Metrics
    net_total = df_agg["gex"].sum()
    call_wall = df_agg.loc[df_agg["gex"].idxmax(), "strike"]
    put_wall = df_agg.loc[df_agg["gex"].idxmin(), "strike"]
    regime = "POS" if net_total >= 0 else "NEG"

    # --- UI Layout ---
    st.write("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Flip", f"${gamma_flip:.2f}" if gamma_flip else "N/A")
    m3.metric("Net GEX", fmt_gex(net_total))
    m4.metric("Call-Wall", f"${call_wall:.2f}")
    m5.metric("Regime", regime) 
    m6.metric("Put-Wall", f"${put_wall:.2f}")

    chart_config = {
        'toImageButtonOptions': {'format': 'png', 'scale': 2},
        'displaylogo': False,
        'modeBarButtonsToAdd': ['downloadImage']
    }

    # Top Chart
    fig_main = go.Figure()
    df_calls = df_main_view[df_main_view['type'] == 'Call']
    fig_main.add_trace(go.Bar(x=df_calls["strike"], y=df_calls["gex"], marker_color="#4db6ac", name="Call Gamma"))
    df_puts = df_main_view[df_main_view['type'] == 'Put']
    fig_main.add_trace(go.Bar(x=df_puts["strike"], y=df_puts["gex"], marker_color="#e57373", name="Put Gamma"))
    fig_main.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    if gamma_flip:
        fig_main.add_vline(x=gamma_flip, line_width=2, line_dash="dash", line_color="orange", annotation_text="FLIP")
    fig_main.update_layout(template="plotly_dark", height=450, margin=dict(l=10, r=10, t=30, b=10), barmode='relative')
    st.plotly_chart(fig_main, use_container_width=True, config=chart_config)

    # Heatmap
    st.subheader("Gamma Heat Map")
    custom_rdwgn = [[0.0, "rgb(215,48,39)"], [0.45, "rgb(254,224,139)"], [0.5, "rgb(255,255,255)"], [0.55, "rgb(166,217,106)"], [1.0, "rgb(26,152,80)"]]
    fig_heat = go.Figure(data=go.Heatmap(z=df_pivot.values, x=df_pivot.columns, y=df_pivot.index, colorscale=custom_rdwgn, zmid=0))
    fig_heat.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    fig_heat.update_layout(template="plotly_white", height=500, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_heat, use_container_width=True, config=chart_config)

    # --- Data Table Section ---
    st.write("---")
    st.subheader(f"Raw Data: {ticker_input} - {selected_exp}")
    
    # Table Filter Radio Buttons
    table_filter = st.radio("Filter Table By Type", options=["All", "Call", "Put"], index=0, horizontal=True)
    
    if table_filter == "All":
        df_to_show = df_table_full
    else:
        df_to_show = df_table_full[df_table_full["Type"] == table_filter]
        
    st.dataframe(df_to_show, use_container_width=True, hide_index=True)

    st.caption(f"Data delayed 15 min | Yahoo Market Time: {market_time} | RF Rate: {risk_free*100:.3f}%")

except Exception as e:
    st.info("Gathering market data... (This can take 10-15 seconds for indices)")
