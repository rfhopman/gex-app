import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX Dashboard", layout="wide")

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

# --- Custom Header Style (From Image) ---
st.markdown(
    """
    <style>
    .main-header {
        background-color: #000000;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        margin-bottom: 25px;
    }
    .main-header h1 {
        color: #FFFFFF;
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        margin: 0;
        font-weight: 700;
        letter-spacing: 1px;
    }
    </style>
    <div class="main-header">
        <h1>GAMMA EXPOSURE (GEX) DASHBOARD</h1>
    </div>
    """, unsafe_allow_html=True
)

# --- Sidebar Controls ---
st.sidebar.header("⚙️ App Settings")
ticker_input = st.sidebar.text_input("Ticker", value="XSP").upper()

# Default changed to 40
strike_option = st.sidebar.selectbox(
    "Number of Strikes", 
    options=[10, 20, 40, 60, "All"], 
    index=2 
)

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
    
    try:
        exps = tk.options
        if not exps:
            tk = yf.Ticker(ticker_input.replace("^", ""))
            exps = tk.options
    except:
        st.error("Could not fetch option chain.")
        st.stop()

    selected_exp = st.sidebar.selectbox("Select Expiration", exps)
    risk_free = get_risk_free_rate()
    
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    
    strike_map = {}
    for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
        if df.empty: continue
        df = df[(df['strike'] >= spot * 0.8) & (df['strike'] <= spot * 1.2)]
        for _, row in df.iterrows():
            K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
            if OI <= 1 or iv <= 0: continue
            g = bs_gamma(spot, K, T, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            if K not in strike_map: strike_map[K] = {"strike": K, "netGEX": 0.0}
            strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex

    if not strike_map:
        st.warning("No valid Option data found.")
        st.stop()

    df_plot = pd.DataFrame(strike_map.values()).sort_values("strike")
    
    # --- Gamma Flip ---
    gamma_flip = None
    for i in range(len(df_plot)-1):
        if df_plot.iloc[i]["netGEX"] * df_plot.iloc[i+1]["netGEX"] < 0:
            s1, g1 = df_plot.iloc[i]["strike"], df_plot.iloc[i]["netGEX"]
            s2, g2 = df_plot.iloc[i+1]["strike"], df_plot.iloc[i+1]["netGEX"]
            gamma_flip = s1 - g1 * (s2 - s1) / (g2 - g1)
            break

    # Filtering range
    if strike_option != "All":
        idx = (df_plot['strike'] - spot).abs().idxmin()
        half = strike_option // 2
        df_plot_view = df_plot.iloc[max(0, idx-half): min(len(df_plot), idx+half)]
    else:
        df_plot_view = df_plot

    # Metrics
    net_total = df_plot["netGEX"].sum()
    call_wall = df_plot.loc[df_plot["netGEX"].idxmax(), "strike"]
    put_wall = df_plot.loc[df_plot["netGEX"].idxmin(), "strike"]
    regime = "POSITIVE (Dampening)" if net_total >= 0 else "NEGATIVE (Explosive)"
    regime_color = "#4db6ac" if net_total >= 0 else "#e57373"
    
    # --- Top Metrics Panel ---
    c1, c2 = st.columns(2)
    c1.metric("Spot Price", f"${spot:.2f}")
    c1.metric("Gamma Flip", f"${gamma_flip:.2f}" if gamma_flip else "N/A")
    c2.metric("Net GEX", fmt_gex(net_total))
    c2.metric("Call-Wall", f"${call_wall:.2f}") # Renamed
    st.metric("Put-Wall", f"${put_wall:.2f}")   # Renamed

    # Regime Indicator
    st.markdown(
        f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; border-left: 8px solid {regime_color}; margin-bottom:20px">
            <span style="color:#888; font-size:12px; font-weight:bold; text-transform:uppercase">Regime Strategy</span><br>
            <span style="color:{regime_color}; font-size:24px; font-weight:bold">{regime}</span>
        </div>""", unsafe_allow_html=True
    )

    # Charting
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_plot_view["strike"], 
        y=df_plot_view["netGEX"],
        marker_color=np.where(df_plot_view["netGEX"] >= 0, "#4db6ac", "#e57373"),
        name="Net GEX"
    ))

    # SPOT LINE: SOLID BLACK
    fig.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    
    if gamma_flip:
        fig.add_vline(x=gamma_flip, line_width=2, line_dash="dash", line_color="orange", annotation_text="FLIP")
    fig.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="Call-Wall")
    fig.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="Put-Wall")

    fig.update_layout(template="plotly_dark", height=600, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    
    st.caption(f"Data delayed 15 min | Yahoo Market Time: {market_time} | RF Rate: {risk_free*100:.3f}%")

except Exception as e:
    st.warning(f"Searching for {ticker_input} option chain...")
