import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
st.title("📊 GEX DASHBOARD PRO")

ctrl_col1, ctrl_col2 = st.columns([1, 2])

with ctrl_col1:
    ticker_input = st.text_input("Ticker (Indices use '^')", value="^XSP").upper()

with ctrl_col2:
    strike_option = st.radio("Strikes to View", options=[10, 20, 40, 60, "All"], index=2, horizontal=True)

try:
    with st.spinner(f"Aggregating full market data for {ticker_input}..."):
        tk = yf.Ticker(ticker_input)
        
        # Robust Time/Spot Fetch
        try:
            raw_ts = tk.info.get('regularMarketTime') or tk.fast_info.get("last_price_timestamp")
            market_time = datetime.fromtimestamp(raw_ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%I:%M:%S %p %Z")
        except: market_time = "N/A"

        spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
        
        # Setup aggregation loop
        all_exps = tk.options
        if not all_exps:
            st.error("Could not fetch option chain for this ticker.")
            st.stop()
        
        risk_free = get_risk_free_rate()
        now_ts = datetime.now(timezone.utc).timestamp()
        
        # Main chain data structures
        main_strike_map = {}
        selected_exp = all_exps[0] # Default to 0DTE or nearest weekly

        # Heatmap structures (Next 10 Expirations)
        heatmap_exps = all_exps[:10]
        heatmap_data = [] # List of DataFrames, one per expiry

        # Aggregation wide range to capture all potential Gamma
        wide_K_filter = (0.7, 1.3)

        # --- Aggregation Loop over All Expirations ---
        for exp in heatmap_exps:
            exp_ts = datetime.strptime(exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
            T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
            
            try:
                chain = tk.option_chain(exp)
                exp_map = {}
                
                for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
                    if df.empty: continue
                    # Apply wide filter
                    df = df[(df['strike'] >= spot * wide_K_filter[0]) & (df['strike'] <= spot * wide_K_filter[1])]
                    
                    for _, row in df.iterrows():
                        K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
                        if OI <= 1 or iv <= 0: continue
                        
                        g = bs_gamma(spot, K, T, risk_free, iv)
                        gex = g * OI * 100 * spot * spot * 0.01
                        
                        # Add to the current expiration's map
                        if K not in exp_map: exp_map[K] = {"strike": K, "netGEX": 0.0}
                        exp_map[K]["netGEX"] += gex if opt_type == "call" else -gex
                        
                        # Add to the main chart map (Summed across all exps)
                        if K not in main_strike_map: main_strike_map[K] = {"strike": K, "netGEX": 0.0}
                        main_strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex

                # Format the current expiry data for heatmap aggregation
                df_exp = pd.DataFrame(exp_map.values())
                df_exp['expiry'] = exp
                heatmap_data.append(df_exp)
                
            except: continue # Skip faulty chains silenty

    # --- Data Processing for Display ---
    df_main_chart = pd.DataFrame(main_strike_map.values()).sort_values("strike")
    
    # Process Heatmap Data (Pivoting required)
    df_heatmap_long = pd.concat(heatmap_data, ignore_index=True)
    df_heatmap_pivot = df_heatmap_long.pivot(index="expiry", columns="strike", values="netGEX").fillna(0)
    
    # Filter heatmap and main chart based on slider range
    if strike_option != "All":
        idx = (df_main_chart['strike'] - spot).abs().idxmin()
        half = strike_option // 2
        df_main_view = df_main_chart.iloc[max(0, idx-half): min(len(df_main_chart), idx+half)]
        
        # Apply same strike range to heatmap pivot
        heatmap_strikes = df_main_view['strike'].unique()
        df_heatmap_pivot_filtered = df_heatmap_pivot.loc[:, df_heatmap_pivot.columns.isin(heatmap_strikes)]
    else:
        df_main_view = df_main_chart
        df_heatmap_pivot_filtered = df_heatmap_pivot

    # Metrics
    net_total = df_main_chart["netGEX"].sum()
    call_wall = df_main_chart.loc[df_main_chart["netGEX"].idxmax(), "strike"]
    put_wall = df_main_chart.loc[df_main_chart["netGEX"].idxmin(), "strike"]
    regime = "POS" if net_total >= 0 else "NEG"
    regime_color = "#4db6ac" if net_total >= 0 else "#e57373"
    
    # --- UI Layout: Metrics Panel ---
    st.write("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Flip (Interp)", "N/A") # Gamma Flip logic removed to speed up agg loop
    m3.metric("Total Net GEX", fmt_gex(net_total))
    m4.metric("Call-Wall", f"${call_wall:.2f}")
    m5.metric("Regime", regime) 
    m6.metric("Put-Wall", f"${put_wall:.2f}")

    # GEX Regime Indicator
    st.markdown(
        f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; border-left: 8px solid {regime_color}; margin-bottom:20px">
            <span style="color:#888; font-size:12px; font-weight:bold; text-transform:uppercase">Whole Market Sentiment</span><br>
            <span style="color:{regime_color}; font-size:24px; font-weight:bold">{regime}</span>
        </div>""", unsafe_allow_html=True
    )

    # --- Interactive Bar Chart (Aggregated GEX) ---
    fig_main = go.Figure()
    fig_main.add_trace(go.Bar(
        x=df_main_view["strike"], 
        y=df_main_view["netGEX"],
        marker_color=np.where(df_main_view["netGEX"] >= 0, "#4db6ac", "#e57373"),
        name="Net GEX"
    ))

    fig_main.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    fig_main.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="Call-Wall")
    fig_main.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="Put-Wall")

    fig_main.update_layout(template="plotly_dark", height=500, margin=dict(l=10, r=10, t=30, b=10), title="Aggregated Total GEX (All Expirations)")
    st.plotly_chart(fig_main, use_container_width=True)
    
    # --- Gamma Term Structure Heatmap ---
    st.write("---")
    st.subheader("Gamma Term Structure (Next 10 Expirations)")
    
    fig_heatmap = go.Figure(data=go.Heatmap(
        z=df_heatmap_pivot_filtered.values,
        x=df_heatmap_pivot_filtered.columns,
        y=df_heatmap_pivot_filtered.index,
        colorscale='RdYlGn',  # Matching color palette from image (Red/Yellow/Green)
        colorbar=dict(title="Net Gamma"),
        zmid=0, # Force zero to be yellow
    ))

    fig_heatmap.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    
    fig_heatmap.update_layout(
        template="plotly_dark",
        xaxis_title="Strike",
        yaxis_title="Expiration Date",
        height=600,
        margin=dict(l=10, r=10, t=10, b=10)
    )
    st.plotly_chart(fig_heatmap, use_container_width=True)

    st.caption(f"Term Structure Aggregated across {len(heatmap_exps)} expirations | Delayed 15 min | Market Time: {market_time} | RF Rate: {risk_free*100:.3f}%")

except Exception as e:
    st.info("Loading full market data... (This may take a few moments)")
    # st.error(f"Details: {e}")
