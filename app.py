import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX Mobile Pro", layout="wide")

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

@st.cache_data(ttl=3600)
def get_risk_free_rate():
    try:
        irx = yf.Ticker("^IRX")
        rate = irx.fast_info.get("last_price") or irx.history(period="1d")["Close"].iloc[-1]
        return float(rate) / 100
    except: return 0.04

# --- Sidebar Controls ---
st.sidebar.header("⚙️ App Settings")
ticker_input = st.sidebar.text_input("Ticker", value="^XSP").upper()

strike_option = st.sidebar.selectbox(
    "Number of Strikes", 
    options=[10, 20, 40, 60, "All"], 
    index=2
)

try:
    tk = yf.Ticker(ticker_input)
    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    exps = tk.options
    selected_exp = st.sidebar.selectbox("Select Expiration", exps)
    risk_free = get_risk_free_rate()
    
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    
    # --- Calculate GEX for this Expiration ---
    strike_map = {}
    for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
        df = df[(df['strike'] >= spot * 0.7) & (df['strike'] <= spot * 1.3)]
        for _, row in df.iterrows():
            K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
            if OI <= 1 or iv <= 0: continue
            g = bs_gamma(spot, K, T, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            if K not in strike_map: strike_map[K] = {"strike": K, "netGEX": 0.0}
            strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex

    df_plot = pd.DataFrame(strike_map.values()).sort_values("strike")
    
    # --- Gamma Flip Logic (Linear Interpolation) ---
    gamma_flip = None
    for i in range(len(df_plot)-1):
        if df_plot.iloc[i]["netGEX"] * df_plot.iloc[i+1]["netGEX"] < 0:
            s1, g1 = df_plot.iloc[i]["strike"], df_plot.iloc[i]["netGEX"]
            s2, g2 = df_plot.iloc[i+1]["strike"], df_plot.iloc[i+1]["netGEX"]
            gamma_flip = s1 - g1 * (s2 - s1) / (g2 - g1)
            break

    # Filtering for display
    if strike_option != "All":
        idx = (df_plot['strike'] - spot).abs().idxmin()
        half = strike_option // 2
        df_plot = df_plot.iloc[max(0, idx-half): min(len(df_plot), idx+half)]

    # Metrics
    net_total = df_plot["netGEX"].sum()
    call_wall = df_plot.loc[df_plot["netGEX"].idxmax(), "strike"]
    put_wall = df_plot.loc[df_plot["netGEX"].idxmin(), "strike"]
    regime = "POSITIVE (Dampening)" if net_total >= 0 else "NEGATIVE (Explosive)"
    regime_color = "#4db6ac" if net_total >= 0 else "#e57373"
    
    # --- UI Layout ---
    st.title(f"📊 {ticker_input} GEX")
    
    # Metrics Panel
    c1, c2 = st.columns(2)
    c1.metric("Spot Price", f"${spot:.2f}")
    c1.metric("Gamma Flip", f"${gamma_flip:.2f}" if gamma_flip else "N/A")
    
    c2.metric("Net GEX", fmt_gex(net_total))
    c2.metric("Call Wall", f"${call_wall:.2f}")
    
    st.metric("Put Wall", f"${put_wall:.2f}")

    # Regime Card
    st.markdown(
        f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; border-left: 8px solid {regime_color}; margin-bottom:20px">
            <span style="color:#888; font-size:12px; font-weight:bold; text-transform:uppercase">Regime</span><br>
            <span style="color:{regime_color}; font-size:24px; font-weight:bold">{regime}</span>
        </div>""", unsafe_allow_html=True
    )

    # Interactive Chart
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_plot["strike"], 
        y=df_plot["netGEX"],
        marker_color=np.where(df_plot["netGEX"] >= 0, "#4db6ac", "#e57373"),
        name="Net GEX"
    ))

    # Thick Solid Lines for Mobile Visibility
    fig.add_vline(x=spot, line_width=3, line_color="white", annotation_text="SPOT")
    if gamma_flip:
        fig.add_vline(x=gamma_flip, line_width=2, line_dash="dash", line_color="orange", annotation_text="FLIP")
    fig.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="C-WALL")
    fig.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="P-WALL")

    fig.update_layout(
        template="plotly_dark", 
        height=600, 
        xaxis_title="Strike",
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"RF Rate (^IRX): {risk_free*100:.3f}% | Data delayed 15m")

except Exception as e:
    st.info("Please enter a ticker to load data.")
 
