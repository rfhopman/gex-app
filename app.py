import streamlit as st
import math
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- Setup Page Configuration ---
# FIXED: Added page_icon="📊"
st.set_page_config(page_title="GEX Dashboard Pro", page_icon="📊", layout="wide")

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
    min_oi_visual = st.radio("Min Contracts (Visual Only)", options=[1, 5], index=0, horizontal=True)

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
    
    exp_ts = datetime.strptime(selected_exp, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc).timestamp()
    T_main = max((exp_ts - now_ts) / (365.25 * 24 * 3600), 0.5/365.25)
    
    chain = tk.option_chain(selected_exp)
    main_list = []
    table_rows = []
    
    for opt_type, df_raw in [("Call", chain.calls), ("Put", chain.puts)]:
        if df_raw.empty: continue
        
        df = df_raw.copy()
        for col in ["strike", "openInterest", "volume", "impliedVolatility"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        for _, row in df.iterrows():
            K = float(row["strike"])
            OI = float(row["openInterest"])
            iv = float(row["impliedVolatility"])
            vol = float(row["volume"])
            
            if iv <= 0 or K <= 0: continue
            
            g = bs_gamma(spot, K, T_main, risk_free, iv)
            gex = g * OI * 100 * spot * spot * 0.01
            
            if spot * 0.8 <= K <= spot * 1.2:
                main_list.append({
                    "strike": K, 
                    "gex": gex if opt_type == "Call" else -gex, 
                    "type": opt_type,
                    "oi": OI,
                    "vol": vol
                })
            
            table_rows.append({
                "Strike": K, "Type": opt_type, "OI": int(OI),
                "Volume": int(vol),
                "IV": f"{iv*100:.2f}%",
                "GEX": int(round(gex if opt_type == "Call" else -gex, 0))
            })

    df_main = pd.DataFrame(main_list)
    df_table_full = pd.DataFrame(table_rows).sort_values(["Strike", "Type"])
    
    df_calc = df_main.groupby("strike")["gex"].sum().reset_index().sort_values("strike")
    
    gamma_flip = None
    if not df_calc.empty:
        for i in range(len(df_calc)-1):
            g1, g2 = df_calc.iloc[i]["gex"], df_calc.iloc[i+1]["gex"]
            if g1 * g2 < 0:
                s1, s2 = df_calc.iloc[i]["strike"], df_calc.iloc[i+1]["strike"]
                gamma_flip = s1 - g1 * (s2 - s1) / (g2 - g1)
                break

    net_total = df_calc["gex"].sum() if not df_calc.empty else 0
    call_wall = df_calc.loc[df_calc["gex"].idxmax(), "strike"] if not df_calc.empty else 0
    put_wall = df_calc.loc[df_calc["gex"].idxmin(), "strike"] if not df_calc.empty else 0
    
    regime_val = "POSITIVE" if net_total >= 0 else "NEGATIVE"
    bg_color = "#d4edda" if net_total >= 0 else "#f8d7da"
    text_color = "#155724" if net_total >= 0 else "#721c24"

    # --- UI Layout ---
    st.write("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"${spot:.2f}")
    m2.metric("Flip", f"${gamma_flip:.2f}" if gamma_flip else "N/A")
    m3.metric("Net GEX", fmt_gex(net_total))
    m4.metric("Call-Wall", f"${call_wall:.2f}")
    
    with m5:
        st.markdown(f"""
            <div style="background-color: {bg_color}; padding: 10px; border-radius: 5px; text-align: center; border: 1px solid {text_color};">
                <p style="margin:0; font-size:14px; color: #555;">Regime</p>
                <p style="margin:0; font-size:20px; font-weight:bold; color: {text_color};">{regime_val}</p>
            </div>
        """, unsafe_allow_html=True)

    m6.metric("Put-Wall", f"${put_wall:.2f}")

    chart_config = {'toImageButtonOptions': {'format': 'png', 'scale': 2}, 'displaylogo': False, 'modeBarButtonsToAdd': ['downloadImage']}

    # --- Top Chart with Volume Area ---
    fig_main = go.Figure()
    if not df_main.empty:
        df_visual = df_main[df_main['oi'] >= min_oi_visual]
        if strike_option != "All":
            idx = (df_calc['strike'] - spot).abs().idxmin()
            half = strike_option // 2
            visible_strikes = df_calc.iloc[max(0, idx-half): min(len(df_calc), idx+half)]['strike'].unique()
            df_plot = df_visual[df_visual['strike'].isin(visible_strikes)]
        else:
            df_plot = df_visual

        # Background Volume Area (Calls - Light Blue)
        fig_main.add_trace(go.Scatter(
            x=df_plot[df_plot['type'] == 'Call']["strike"], 
            y=df_plot[df_plot['type'] == 'Call']["vol"], 
            fill='tozeroy', mode='none', fillcolor='rgba(173, 216, 230, 0.25)', 
            name="Call Volume", yaxis="y2", hoverinfo="skip"
        ))
        
        # Background Volume Area (Puts - Light Red)
        fig_main.add_trace(go.Scatter(
            x=df_plot[df_plot['type'] == 'Put']["strike"], 
            y=df_plot[df_plot['type'] == 'Put']["vol"], 
            fill='tozeroy', mode='none', fillcolor='rgba(255, 182, 193, 0.25)', 
            name="Put Volume", yaxis="y2", hoverinfo="skip"
        ))

        # Primary GEX Bars
        fig_main.add_trace(go.Bar(
            x=df_plot[df_plot['type'] == 'Call']["strike"], 
            y=df_plot[df_plot['type'] == 'Call']["gex"], 
            marker_color="#4db6ac", name="Call GEX",
            hovertemplate="Strike: %{x}<br>GEX: %{y:,.0f}<extra></extra>"
        ))
        fig_main.add_trace(go.Bar(
            x=df_plot[df_plot['type'] == 'Put']["strike"], 
            y=df_plot[df_plot['type'] == 'Put']["gex"], 
            marker_color="#e57373", name="Put GEX",
            hovertemplate="Strike: %{x}<br>GEX: %{y:,.0f}<extra></extra>"
        ))
    
    fig_main.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
    if gamma_flip: fig_main.add_vline(x=gamma_flip, line_width=2, line_dash="dash", line_color="orange", annotation_text="FLIP")
    
    fig_main.update_layout(
        template="plotly_dark", height=450, margin=dict(l=10, r=10, t=30, b=10),
        barmode='relative',
        yaxis2=dict(title="Volume", overlaying="y", side="right", showgrid=False, rangemode="tozero")
    )
    st.plotly_chart(fig_main, use_container_width=True, config=chart_config)

    # --- Heat Map Section ---
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
                for opt_type, df_h_raw in [("Call", c.calls), ("Put", c.puts)]:
                    if heat_filter != "All" and opt_type != heat_filter:
                        continue
                    df_h = df_h_raw.copy()
                    df_h["openInterest"] = pd.to_numeric(df_h["openInterest"], errors='coerce').fillna(0)
                    df_h["impliedVolatility"] = pd.to_numeric(df_h["impliedVolatility"], errors='coerce').fillna(0)
                    df_h_plot = df_h[df_h['openInterest'] >= min_oi_visual]
                    df_h_plot = df_h_plot[(df_h_plot['strike'] >= spot * 0.9) & (df_h_plot['strike'] <= spot * 1.1)]
                    for _, row in df_h_plot.iterrows():
                        K_h, OI_h, iv_h = float(row["strike"]), float(row["openInterest"]), float(row["impliedVolatility"])
                        if iv_h <= 0: continue
                        g = bs_gamma(spot, K_h, T_heat, risk_free, iv_h)
                        gex = g * OI_h * 100 * spot * spot * 0.01
                        heatmap_list.append({"expiry": exp, "strike": K_h, "netGEX": gex if opt_type == "Call" else -gex})
            except: continue
        
        if heatmap_list:
            df_heat_long = pd.DataFrame(heatmap_list)
            df_pivot = df_heat_long.groupby(['expiry', 'strike'])['netGEX'].sum().unstack().fillna(0)
            custom_rdwgn = [[0.0, "rgb(215,48,39)"], [0.45, "rgb(254,224,139)"], [0.5, "rgb(255,255,255)"], [0.55, "rgb(166,217,106)"], [1.0, "rgb(26,152,80)"]]
            fig_heat = go.Figure(data=go.Heatmap(
                z=df_pivot.values, x=df_pivot.columns, y=df_pivot.index, colorscale=custom_rdwgn, zmid=0,
                hovertemplate="<b>Expiry</b>: %{y}<br><b>Strike</b>: %{x}<br><b>Net GEX</b>: %{z:,.0f}<extra></extra>"
            ))
            fig_heat.add_vline(x=spot, line_width=4, line_color="black", annotation_text="SPOT")
            fig_heat.update_layout(template="plotly_white", height=500, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_heat, use_container_width=True, config=chart_config)

    # Data Table
    st.write("---")
    st.subheader(f"Raw Data: {ticker_input} - {selected_exp}")
    table_filter = st.radio("Filter Table By Type", options=["All", "Call", "Put"], index=0, horizontal=True)
    if table_filter == "All": df_to_show = df_table_full
    else: df_to_show = df_table_full[df_table_full["Type"] == table_filter]
    st.dataframe(df_to_show, use_container_width=True, hide_index=True)
    st.caption(f"Data delayed 15 min | Yahoo Market Time: {market_time} | RF Rate: {risk_free*100:.3f}%")

except Exception as e:
    st.error(f"Error: {e}")
