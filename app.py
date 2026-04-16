import streamlit as st
import math
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
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

@st.cache_data(ttl=3600)
def get_risk_free_rate():
    try:
        irx = yf.Ticker("^IRX").fast_info
        rate = irx.get("last_price", 4.0)
        return rate / 100
    except: return 0.04

# --- Sidebar Controls ---
st.sidebar.title("⚡ GEX Controls")
ticker_input = st.sidebar.text_input("Ticker", value="^XSP").upper()
strike_range = st.sidebar.slider("± Strikes from Spot", 5, 50, 20)

# --- Data Fetching ---
tk = yf.Ticker(ticker_input)
try:
    # Fetch Spot
    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    
    # Fetch Expirations
    exps = tk.options
    selected_exp = st.sidebar.selectbox("Select Expiration", exps)
    
    if st.sidebar.button("Compute GEX"):
        # Calculate Risk Free & Time
        risk_free = get_risk_free_rate()
        now_ts = datetime.now(timezone.utc).timestamp()
        exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
        T = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
        
        # Get Option Chain
        chain = tk.option_chain(selected_exp)
        
        # GEX Logic
        strike_map = {}
        for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
            # Filter strikes near spot
            df = df[(df['strike'] >= spot * 0.8) & (df['strike'] <= spot * 1.2)]
            for _, row in df.iterrows():
                K, OI, iv = row["strike"], row["openInterest"], row["impliedVolatility"]
                if OI <= 1 or iv <= 0: continue
                
                g = bs_gamma(spot, K, T, risk_free, iv)
                gex = g * OI * 100 * spot * spot * 0.01
                
                if K not in strike_map: strike_map[K] = {"strike": K, "netGEX": 0.0}
                strike_map[K]["netGEX"] += gex if opt_type == "call" else -gex

        strikes_df = pd.DataFrame(strike_map.values()).sort_values("strike")
        # Filter by slider range
        idx = (strikes_df['strike'] - spot).abs().idxmin()
        strikes_df = strikes_df.iloc[max(0, idx-strike_range): min(len(strikes_df), idx+strike_range)]

        # Metrics
        net_total = strikes_df["netGEX"].sum()
        call_wall = strikes_df.loc[strikes_df["netGEX"].idxmax(), "strike"]
        put_wall = strikes_df.loc[strikes_df["netGEX"].idxmin(), "strike"]
        
        # --- UI Layout ---
        st.header(f"{ticker_input} Dashboard")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Spot Price", f"${spot:.2f}")
        col2.metric("Net GEX", fmt_gex(net_total))
        col3.metric("Call Wall", f"${call_wall}")
        col4.metric("Put Wall", f"${put_wall}")

        # --- Interactive Chart ---
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=strikes_df["strike"], y=strikes_df["netGEX"],
            marker_color=np.where(strikes_df["netGEX"] >= 0, "#4db6ac", "#e57373")
        ))
        fig.add_vline(x=spot, line_dash="dash", line_color="yellow", annotation_text="Spot")
        fig.update_layout(template="plotly_dark", height=600, title="Net GEX by Strike")
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"Error: {e}")
