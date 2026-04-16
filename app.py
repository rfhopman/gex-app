import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone

# --- Setup Page Configuration ---
st.set_page_config(page_title="Total Market GEX", layout="wide")

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
st.sidebar.header("⚙️ Total Market Settings")
ticker_input = st.sidebar.text_input("Ticker", value="XSP").upper()
strike_option = st.sidebar.selectbox("Display Range (Strikes)", options=[10, 20, 40, 60, "All"], index=2)

try:
    tk = yf.Ticker(ticker_input)
    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    all_exps = tk.options
    risk_free = get_risk_free_rate()
    
    # Progress bar for multi-expiration fetch
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_strike_map = {}
    now_ts = datetime.now(timezone.utc).timestamp()

    # --- AGGREGATE ALL EXPIRATIONS ---
    for i, exp in enumerate(all_exps):
        status_text.text(f"Processing {exp}...")
        progress_bar.progress((i + 1) / len(all_exps))
        
        # Calculate Time to Expiry for this specific date
        exp_ts = datetime.strptime(exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
        T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
        
        try:
            chain = tk.option_chain(exp)
            for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
                # Wide filter to capture all meaningful gamma
                df = df[(df['strike'] >= spot * 0.5) & (df['strike'] <= spot * 1.5)]
                for _, row in df.iterrows():
                    K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
                    if OI <= 1 or iv <= 0: continue
                    
                    g = bs_gamma(spot, K, T, risk_free, iv)
                    gex = g * OI * 100 * spot * spot * 0.01
                    
                    if K not in total_strike_map: 
                        total_strike_map[K] = {"strike": K, "netGEX": 0.0}
                    total_strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex
        except: continue

    progress_bar.empty()
    status_text.empty()

    df_plot = pd.DataFrame(total_strike_map.values()).sort_values("strike")
    
    # --- CALCULATE FLIP LEVEL ---
    # Find where GEX crosses zero by checking signs between sorted strikes
    gamma_flip = None
    for j in range(len(df_plot)-1):
        if df_plot.iloc[j]["netGEX"] * df_plot.iloc[j+1]["netGEX"] < 0:
            # Linear interpolation for more accurate flip price
            s1, g1 = df_plot.iloc[j]["strike"], df_plot.iloc[j]["netGEX"]
            s2, g2 = df_plot.iloc[j+1]["strike"], df_plot.iloc[j+1]["netGEX"]
            gamma_flip = s1 - g1 * (s2 - s1) / (g2 - g1)
            break

    # Filtering for display
    if strike_option != "All":
        idx = (df_plot['strike'] - spot).abs().idxmin()
        half = strike_option // 2
        df_plot_display = df_plot.iloc[max(0, idx-half): min(len(df_plot), idx+half)]
    else:
        df_plot_display = df_plot

    # Metrics
    net_total = df_plot["netGEX"].sum()
    call_wall = df_plot.loc[df_plot["netGEX"].idxmax(), "strike"]
    put_wall = df_plot.loc[df_plot["netGEX"].idxmin(), "strike"]
    regime = "POSITIVE (Dampening)" if net_total >= 0 else "NEGATIVE (Explosive)"
    regime_color = "#4db6ac" if net_total >= 0 else "#e57373"
    
    # --- UI Layout ---
    st.title(f"🌍 Total Market GEX: {ticker_input}")
    
    c1, c2 = st.columns(2)
    c1.metric("Spot Price", f"${spot:.2f}")
    c1.metric("Gamma Flip", f"${gamma_flip:.2f}" if gamma_flip else "N/A")
    
    c2.metric("Total Net GEX", fmt_gex(net_total))
    c2.metric("Call Wall", f"${call_wall:.2f}")
    
    # Regime Card
    st.markdown(
        f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; border-left: 8px solid {regime_color}; margin-bottom:20px">
            <span style="color:#888; font-size:12px; font-weight:bold; text-transform:uppercase">Global Market Regime</span><br>
            <span style="color:{regime_color}; font-size:24px; font-weight:bold">{regime}</span>
        </div>""", unsafe_allow_html=True
    )

    # Interactive Chart
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_plot_display["strike"], y=df_plot_display["netGEX"],
        marker_color=np.where(df_plot_display["netGEX"] >= 0, "#4db6ac", "#e57373"),
        name="Net GEX"
    ))

    fig.add_vline(x=spot, line_width=3, line_color="white", annotation_text="SPOT")
    if gamma_flip:
        fig.add_vline(x=gamma_flip, line_width=2, line_dash="dash", line_color="orange", annotation_text="FLIP")
    fig.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="C-WALL")
    fig.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="P-WALL")

    fig.update_layout(template="plotly_dark", height=600, xaxis_title="Strike", margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Aggregated across {len(all_exps)} expirations | RF Rate: {risk_free*100:.3f}%")

except Exception as e:
    st.error(f"Error aggregating market data: {e}")
 
