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
    msg = f"🚨 {ticker} ({exp}): Spot ${spot:.2f} | CW ${call_w:.2f} | PW ${put_w:.2f}"
    try:
        response = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode('utf-8'), timeout=10)
        return response.status_code
    except: return "Error"

# --- AUTO-REFRESH LOGIC (2 PM - 4:15 PM EST) ---
now_est = datetime.now(ZoneInfo("America/New_York"))
start_time = time(14, 0)
end_time = time(16, 15)

if start_time <= now_est.time() <= end_time:
    from streamlit_autorefresh import st_autorefresh
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
        r_rate = (irx.fast_info.get("last_price") or irx.history(period="1d")["Close"].iloc[-1]) / 100
        vix_val = vix.fast_info.get("last_price") or vix.history(period="1d")["Close"].iloc[-1]
        return r_rate, vix_val
    except: return 0.04, 0.0

# --- TOP ROW CONTROLS ---
st.title("📊 GEX DASHBOARD")

with st.sidebar:
    st.write("### Notification Center")
    if st.button("🔔 Send Test Notification"):
        send_iphone_notification("TEST", "2026-04-17", 0.0, 0.0, 0.0)

ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1, 1.5, 1, 0.5])
with ctrl_col1: ticker_input = st.text_input("Ticker", value="XSP").upper()
with ctrl_col2: strike_option = st.radio("Strikes", options=[10, 20, 40, 60, "All"], index=2, horizontal=True)
with ctrl_col3: min_oi_visual = st.radio("Min OI", options=[1, 5], index=0, horizontal=True)
with ctrl_col4: 
    st.write("")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

try:
    search_ticker = "^XSP" if ticker_input == "XSP" else ticker_input
    tk = yf.Ticker(search_ticker)
    
    try:
        raw_ts = tk.info.get('regularMarketTime') or tk.fast_info.get("last_price_timestamp")
        market_time = datetime.fromtimestamp(raw_ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%I:%M:%S %p %Z")
    except: market_time = "N/A"

    spot = tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1]
    risk_free, vix_price = get_market_data()
    
    all_exps = tk.options
    selected_exp = st.selectbox("Select Expiration", all_exps)

    # --- DATA PROCESSING ---
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list, table_rows = [], []
    
    for opt_type, df_raw in [("Call", chain.calls), ("Put", chain.puts)]:
        df = df_raw.copy()
        for col in ["strike", "openInterest", "volume", "impliedVolatility"]:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        for _, row in df.iterrows():
            K, OI, iv, vol = row["strike"], row["openInterest"], row["impliedVolatility"], row["volume"]
            if iv <= 0: continue
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({"strike": K, "gex": gex if opt_type == "Call" else -gex, "type": opt_type, "oi": OI, "vol": vol})
            table_rows.append({"Strike": K, "Type": opt_type, "OI": int(OI), "Volume": int(vol), "GEX": int(round(gex if opt_type == "Call" else -gex, 0))})

    df_main = pd.DataFrame(main_list)
    df_calc = df_main.groupby("strike")["gex"].sum().reset_index().sort_values("strike")
    call_wall = df_calc.loc[df_calc["gex"].idxmax(), "strike"]
    put_wall = df_calc.loc[df_calc["gex"].idxmin(), "strike"]

    # --- NOTIFICATION ---
    if start_time <= now_est.time() <= end_time:
        if "last_notif" not in st.session_state or (pytime.time() - st.session_state.last_notif) > 800:
            send_iphone_notification(ticker_input, selected_exp, spot, call_wall, put_wall)
            st.session_state.last_notif = pytime.time()

    # --- UI DASHBOARD ---
    st.write("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Net GEX", fmt_gex(df_calc["gex"].sum()))
    m3.metric("Call-Wall", f"${call_wall:.2f}")
    m4.metric("Put-Wall", f"${put_wall:.2f}")
    m5.metric("VIX", f"{vix_price:.2f}")
    m6.metric("RF Rate", f"{risk_free*100:.2f}%")

    # --- CHART 1: GEX BARS ---
    fig = go.Figure()
    df_v = df_main[df_main['oi'] >= min_oi_visual]
    fig.add_trace(go.Scatter(x=df_v[df_v['type']=='Call']["strike"], y=df_v[df_v['type']=='Call']["vol"], fill='tozeroy', fillcolor='rgba(173,216,230,0.2)', yaxis="y2", name="Call Vol"))
    fig.add_trace(go.Scatter(x=df_v[df_v['type']=='Put']["strike"], y=df_v[df_v['type']=='Put']["vol"], fill='tozeroy', fillcolor='rgba(255,182,193,0.2)', yaxis="y2", name="Put Vol"))
    fig.add_trace(go.Bar(x=df_v[df_v['type']=='Call']["strike"], y=df_v[df_v['type']=='Call']["gex"], marker_color="#4db6ac", name="Call GEX"))
    fig.add_trace(go.Bar(x=df_v[df_v['type']=='Put']["strike"], y=df_v[df_v['type']=='Put']["gex"], marker_color="#e57373", name="Put GEX"))
    fig.update_layout(template="plotly_dark", height=450, yaxis2=dict(overlaying="y", side="right", showgrid=False))
    st.plotly_chart(fig, use_container_width=True)

    # --- CHART 2: HEAT MAP ---
    st.write("---")
    st.subheader("Gamma Heat Map")
    with st.spinner("Loading Heat Map..."):
        heatmap_exps = all_exps[:10]
        heatmap_list = []
        for exp in heatmap_exps:
            try:
                e_ts = datetime.strptime(exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
                T_h = max((e_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
                c = tk.option_chain(exp)
                for o_type, df_h_raw in [("Call", c.calls), ("Put", c.puts)]:
                    df_h = df_h_raw.copy()
                    df_h = df_h[(df_h['strike'] >= spot * 0.9) & (df_h['strike'] <= spot * 1.1)]
                    for _, row in df_h.iterrows():
                        iv_h = float(row["impliedVolatility"])
                        if iv_h <= 0: continue
                        g = bs_gamma(spot, float(row["strike"]), T_h, risk_free, iv_h)
                        gex = g * float(row["openInterest"]) * 100 * spot * spot * 0.01
                        heatmap_list.append({"expiry": exp, "strike": float(row["strike"]), "netGEX": gex if o_type == "Call" else -gex})
            except: continue
        
        if heatmap_list:
            df_heat = pd.DataFrame(heatmap_list).groupby(['expiry', 'strike'])['netGEX'].sum().unstack().fillna(0)
            fig_h = go.Figure(data=go.Heatmap(z=df_heat.values, x=df_heat.columns, y=df_heat.index, colorscale='RdYlGn', zmid=0))
            fig_h.update_layout(template="plotly_white", height=500)
            st.plotly_chart(fig_h, use_container_width=True)

    # --- DATA TABLE ---
    st.write("---")
    st.subheader("Raw Data Table")
    df_table = pd.DataFrame(table_rows).sort_values("Strike")
    st.dataframe(df_table, use_container_width=True, hide_index=True)
    st.caption(f"Market Time: {market_time}")

    # --- AD BANNER ---
    st.markdown("---")
    st.markdown('<div style="text-align: center; padding: 20px; background-color: #222; border: 1px dashed #555; color: #888; border-radius: 10px;">ADVERTISING SPACE (BOTTOM BANNER)</div>', unsafe_allow_html=True)

except Exception as e:
    st.error(f"Error: {e}")
