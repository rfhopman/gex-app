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
from scipy.stats import norm

# --- Setup Page Configuration ---
st.set_page_config(page_title="GEX, VEX & DEX Dashboard", page_icon="📊", layout="wide")

# --- CUSTOM NOTIFICATION LOGIC ---
NTFY_TOPIC = "GEX_Alerts" 

def send_iphone_notification(ticker, exp, spot, call_w, put_w):
    msg = f"🚨 {ticker} ({exp}): Spot ${spot:.2f} | CW ${call_w:.2f} | PW ${put_w:.2f}"
    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}", 
            data=msg.encode('utf-8'),
            timeout=10
        )
        return response.status_code
    except Exception as e:
        return str(e)

# --- AUTO-REFRESH & WEEKDAY LOGIC ---
now_est = datetime.now(ZoneInfo("America/New_York"))
is_weekday = now_est.weekday() <= 4  
start_time = time(14, 0)
end_time = time(16, 15)

if is_weekday and (start_time <= now_est.time() <= end_time):
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=15 * 60 * 1000, key="market_close_refresh")

# --- Helpers ---
def bs_greeks(S, K, T, r, iv, opt_type="Call"):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0: return 0.0, 0.0, 0.0
    d1 = (math.log(S/K) + (r + 0.5*iv*iv)*T) / (iv*math.sqrt(T))
    pdf = (1.0 / math.sqrt(2*math.pi)) * math.exp(-0.5*d1*d1)
    
    gamma = pdf / (S * iv * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) * 0.01 
    
    if opt_type == "Call":
        delta = norm.cdf(d1)
    else:
        delta = norm.cdf(d1) - 1
        
    return gamma, vega, delta

def fmt_val(v):
    a, s = abs(v), ("+" if v >= 0 else "−")
    if a >= 1e9: return f"{s}${a/1e9:.2f}B"
    if a >= 1e6: return f"{s}${a/1e6:.1f}M"
    return f"{s}${a:.0f}"

@st.cache_data(ttl=300)
def get_market_metrics():
    try:
        irx = yf.Ticker("^IRX")
        vix = yf.Ticker("^VIX")
        rate = irx.fast_info.get("last_price") or irx.history(period="1d")["Close"].iloc[-1]
        vix_val = vix.fast_info.get("last_price") or vix.history(period="1d")["Close"].iloc[-1]
        return float(rate) / 100, float(vix_val)
    except: 
        return 0.04, 0.0

# --- TOP ROW CONTROLS ---
st.title("📊 GEX, VEX & DEX DASHBOARD")

with st.sidebar:
    st.write("### Notification Center")
    st.info(f"Topic: {NTFY_TOPIC}")
    if st.button("🔔 Test Notification"):
        send_iphone_notification("TEST", "2026-04-17", 0.00, 0.00, 0.00)

ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1, 1.5, 1, 0.5])

with ctrl_col1:
    ticker_input = st.text_input("Ticker", value="XSP").upper()
with ctrl_col2:
    strike_option = st.radio("Strikes to View", options=[10, 20, 40, 60, "All"], index=2, horizontal=True)
with ctrl_col3:
    min_oi_visual = st.radio("Min Contracts", options=[1, 5], index=0, horizontal=True)
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
    all_exps = tk.options
    if not all_exps:
        st.error("No options found.")
        st.stop()

    selected_exp = st.selectbox("Select Expiration Date", all_exps)

    # --- DATA PROCESSING ---
    risk_free, vix_price = get_market_metrics()
    now_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list, table_rows = [], []
    
    for opt_type, df_raw in [("Call", chain.calls), ("Put", chain.puts)]:
        df = df_raw.copy()
        for col in ["strike", "openInterest", "volume", "impliedVolatility"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        for _, row in df.iterrows():
            K, OI, iv, vol = float(row["strike"]), float(row["openInterest"]), float(row["impliedVolatility"]), float(row["volume"])
            if iv <= 0 or K <= 0: continue
            
            gamma, vega, delta = bs_greeks(spot, K, T_main, risk_free, iv, opt_type)
            gex = gamma * OI * 100 * spot * spot * 0.01
            vex = vega * OI * 100 
            dex = delta * OI * 100 * spot 
            
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({"strike": K, "gex": gex if opt_type == "Call" else -gex, "vex": vex, "dex": dex, "type": opt_type, "oi": OI, "vol": vol})
            
            table_rows.append({"Strike": K, "Type": opt_type, "OI": int(OI), "Volume": int(vol), "IV": f"{iv*100:.2f}%", "GEX": gex if opt_type == "Call" else -gex, "VEX": vex, "DEX": dex})

    df_main = pd.DataFrame(main_list)
    df_table_full = pd.DataFrame(table_rows).sort_values(["Strike", "Type"])
    df_calc = df_main.groupby("strike").agg({'gex': 'sum', 'vex': 'sum', 'dex': 'sum'}).reset_index().sort_values("strike")
    
    net_gex = df_calc["gex"].sum() if not df_calc.empty else 0
    net_vex = df_calc["vex"].sum() if not df_calc.empty else 0
    net_dex = df_calc["dex"].sum() if not df_calc.empty else 0
    call_wall = df_calc.loc[df_calc["gex"].idxmax(), "strike"] if not df_calc.empty else 0
    put_wall = df_calc.loc[df_calc["gex"].idxmin(), "strike"] if not df_calc.empty else 0
    
    # --- AUTO-NOTIFICATION TRIGGER ---
    if is_weekday and (start_time <= now_est.time() <= end_time):
        if "last_notif" not in st.session_state or (pytime.time() - st.session_state.last_notif) > 800:
            send_iphone_notification(ticker_input, selected_exp, spot, call_wall, put_wall)
            st.session_state.last_notif = pytime.time()

    # --- TOP METRICS ROW ---
    regime_val = "POSITIVE" if net_gex >= 0 else "NEGATIVE"
    bg_color = "#d4edda" if net_gex >= 0 else "#f8d7da"
    text_color = "#155724" if net_gex >= 0 else "#721c24"

    st.write("---")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Net GEX", fmt_val(net_gex))
    m3.metric("Call-Wall", f"${call_wall:.2f}")
    with m4:
        st.markdown(f'<div style="background-color: {bg_color}; padding: 10px; border-radius: 5px; text-align: center; border: 1px solid {text_color};"><p style="margin:0; font-size:12px; color: #555;">Regime</p><p style="margin:0; font-size:18px; font-weight:bold; color: {text_color};">{regime_val}</p></div>', unsafe_allow_html=True)
    m5.metric("Put-Wall", f"${put_wall:.2f}")

    # --- GEX CHART ---
    fig_main = go.Figure()
    df_visual = df_main[df_main['oi'] >= min_oi_visual]
    fig_main.add_trace(go.Bar(x=df_visual[df_visual['type'] == 'Call']["strike"], y=df_visual[df_visual['type'] == 'Call']["gex"], marker_color="#4db6ac", name="Call GEX"))
    fig_main.add_trace(go.Bar(x=df_visual[df_visual['type'] == 'Put']["strike"], y=df_visual[df_visual['type'] == 'Put']["gex"], marker_color="#e57373", name="Put GEX"))
    fig_main.add_vline(x=spot, line_width=3, line_color="white", annotation_text="SPOT")
    fig_main.update_layout(title="Gamma Exposure (GEX)", template="plotly_dark", height=400, barmode='relative')
    st.plotly_chart(fig_main, use_container_width=True)

    # --- GAMMA HEAT MAP SECTION ---
    st.write("---")
    st.subheader("Gamma Heat Map")
    heat_filter = st.radio("Heat Map Filter", options=["All", "Call", "Put"], index=0, horizontal=True)

    with st.spinner("Generating Gamma Heat Map..."):
        heatmap_exps = all_exps[:10]
        heatmap_list = []
        for exp in heatmap_exps:
            e_ts = datetime.strptime(exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
            T_heat = max((e_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
            try:
                c = tk.option_chain(exp)
                for o_type, df_h_raw in [("Call", c.calls), ("Put", c.puts)]:
                    if heat_filter != "All" and o_type != heat_filter: continue
                    df_h = df_h_raw.copy()
                    df_h_plot = df_h[(df_h['strike'] >= spot * 0.9) & (df_h['strike'] <= spot * 1.1)]
                    for _, row in df_h_plot.iterrows():
                        K_h, OI_h, iv_h = float(row["strike"]), float(row["openInterest"]), float(row["impliedVolatility"])
                        if iv_h <= 0: continue
                        g = (1.0 / math.sqrt(2*math.pi) * math.exp(-0.5*((math.log(spot/K_h) + (risk_free + 0.5*iv_h*iv_h)*T_heat) / (iv_h*math.sqrt(T_heat)))**2)) / (spot * iv_h * math.sqrt(T_heat))
                        gex_h = g * OI_h * 100 * spot * spot * 0.01
                        heatmap_list.append({"expiry": exp, "strike": K_h, "netGEX": gex_h if o_type == "Call" else -gex_h})
            except: continue
        
        if heatmap_list:
            df_heat_long = pd.DataFrame(heatmap_list)
            df_pivot = df_heat_long.groupby(['expiry', 'strike'])['netGEX'].sum().unstack().fillna(0)
            custom_rdwgn = [[0.0, "rgb(215,48,39)"], [0.45, "rgb(254,224,139)"], [0.5, "rgb(255,255,255)"], [0.55, "rgb(166,217,106)"], [1.0, "rgb(26,152,80)"]]
            fig_heat = go.Figure(data=go.Heatmap(z=df_pivot.values, x=df_pivot.columns, y=df_pivot.index, colorscale=custom_rdwgn, zmid=0))
            fig_heat.add_vline(x=spot, line_width=4, line_color="black")
            fig_heat.update_layout(template="plotly_white", height=500, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_heat, use_container_width=True)

    # --- VEX SECTION ---
    st.write("---")
    st.header("📉 VEX PROFILE (Volatility Exposure)")
    fig_vex = go.Figure()
    fig_vex.add_trace(go.Scatter(x=df_calc["strike"], y=df_calc["vex"], fill='tozeroy', line_color='#bb86fc', name="Net VEX"))
    fig_vex.add_vline(x=spot, line_width=2, line_color="black", annotation_text="SPOT")
    fig_vex.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="CW")
    fig_vex.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="PW")
    fig_vex.update_layout(template="plotly_dark", height=400)
    st.plotly_chart(fig_vex, use_container_width=True)
    st.metric("Total Net VEX", fmt_val(net_vex))

    # --- DEX SECTION ---
    st.write("---")
    st.header("🎯 DEX PROFILE (Delta Exposure)")
    fig_dex = go.Figure()
    fig_dex.add_trace(go.Bar(x=df_calc["strike"], y=df_calc["dex"], marker_color="#ffa726", name="Net DEX"))
    fig_dex.add_vline(x=spot, line_width=2, line_color="black", annotation_text="SPOT")
    fig_dex.add_vline(x=call_wall, line_width=2, line_color="#4db6ac", annotation_text="CW")
    fig_dex.add_vline(x=put_wall, line_width=2, line_color="#e57373", annotation_text="PW")
    fig_dex.update_layout(template="plotly_dark", height=400)
    st.plotly_chart(fig_dex, use_container_width=True)
    
    dsum_col1, dsum_col2 = st.columns(2)
    with dsum_col1:
        st.metric("Total Net DEX", fmt_val(net_dex))
    with dsum_col2:
        dex_regime = "STABLE / STICKY" if net_dex > 0 else "TRENDY / SLIPPERY"
        st.info(f"**Market Structure:** {dex_regime}")

    # --- DATA TABLE ---
    st.write("---")
    st.subheader(f"Raw GEX Data: {ticker_input}")
    st.dataframe(df_table_full.drop(columns=['VEX', 'DEX']), use_container_width=True, hide_index=True)

    # --- FOOTER ---
    st.write("---")
    st.caption(f"Market Time: {market_time} | VIX: {vix_price:.2f} | RF Rate: {risk_free*100:.3f}%")

except Exception as e:
    st.error(f"Error: {e}")
