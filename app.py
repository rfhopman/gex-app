import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX Mobile Dashboard", layout="wide")

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

# --- NEW: Robust Risk-Free Rate Fetching ---
def get_risk_free_rate():
    try:
        irx = yf.Ticker("^IRX")
        # Try fast_info first
        rate = irx.fast_info.get("last_price")
        if rate is None:
            # Fallback to current history
            h = irx.history(period="1d")
            rate = h["Close"].iloc[-1] if not h.empty else 4.0
        
        # Yahoo returns IRX as 4.5 meaning 4.5%. We need 0.045 for the formula.
        return float(rate) / 100
    except Exception:
        return 0.04  # Fallback if both methods fail

# --- Sidebar Controls ---
st.sidebar.header("⚙️ App Settings")
ticker_input = st.sidebar.text_input("Ticker", value="XSP").upper()
strike_range = st.sidebar.slider("± Strikes from Spot", 5, 50, 25)

try:
    tk = yf.Ticker(ticker_input)
    # Fetch Spot Price
    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    
    # Fetch Expirations
    exps = tk.options
    selected_exp = st.sidebar.selectbox("Select Expiration", exps)
    
    # Get Live RF Rate
    risk_free = get_risk_free_rate()
    
    # Time Logic (0DTE Safe)
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    # Get Option Chain
    chain = tk.option_chain(selected_exp)
    
    # GEX Calculation Logic
    strike_map = {}
    for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
        # Filter raw data to a reasonable range to save processing time
        df = df[(df['strike'] >= spot * 0.7) & (df['strike'] <= spot * 1.3)]
        for _, row in df.iterrows():
            K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
            if OI <= 1 or iv <= 0: continue
            
            # BS Gamma * OI * Contract Multiplier * Spot (approx. dollar gamma)
            g = bs_gamma(spot, K, T, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            
            if K not in strike_map: strike_map[K] = {"strike": K, "netGEX": 0.0}
            strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex

    df_plot = pd.DataFrame(strike_map.values()).sort_values("strike")
    
    # Center view on Spot based on slider
    idx = (df_plot['strike'] - spot).abs().idxmin()
    df_plot = df_plot.iloc[max(0, idx-strike_range): min(len(df_plot), idx+strike_range)]

    # Metrics
    net_total = df_plot["netGEX"].sum()
    call_wall = df_plot.loc[df_plot["netGEX"].idxmax(), "strike"]
    put_wall = df_plot.loc[df_plot["netGEX"].idxmin(), "strike"]
    
    # --- UI Layout ---
    st.title(f"📊 {ticker_input} GEX")
    # Display the actual rate used for transparency
    st.caption(f"Expiration: {selected_exp} | Dynamic RF Rate (^IRX): {risk_free*100:.3f}%")
    
    # KPI Grid
    m1, m2 = st.columns(2)
    m1.metric("Spot Price", f"${spot:.2f}")
    m2.metric("Net GEX", fmt_gex(net_total))
    
    m3, m4 = st.columns(2)
    m3.metric("Call Wall", f"${call_wall}")
    m4.metric("Put Wall", f"${put_wall}")

    # Interactive Chart
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_plot["strike"], 
        y=df_plot["netGEX"],
        marker_color=np.where(df_plot["netGEX"] >= 0, "#4db6ac", "#e57373"),
        name="Net GEX"
    ))

    # Add Level Lines
    fig.add_vline(x=spot, line_dash="dash", line_color="yellow", annotation_text="SPOT")
    fig.add_vline(x=call_wall, line_dash="dot", line_color="#4db6ac", annotation_text="C-WALL")
    fig.add_vline(x=put_wall, line_dash="dot", line_color="#e57373", annotation_text="P-WALL")

    fig.update_layout(
        template="plotly_dark", 
        height=600, 
        xaxis_title="Strike", 
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.warning("Enter a valid ticker in the sidebar or check your internet connection.")
    st.error(f"Error: {e}")
