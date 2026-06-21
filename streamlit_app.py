import calendar
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

API_BASE = os.environ.get("VELOCITYIQ_API", "http://localhost:8000")

# Brand palette — keep charts consistent with the red/white UI theme.
BRAND_RED = "#E05555"
BRAND_RED_DARK = "#CC3333"
BRAND_RED_LIGHT = "#FFDDDD"
BRAND_GREY = "#888888"
# Qualitative sequence for multi-series charts (year/category breakdowns).
BRAND_SEQUENCE = ["#E05555", "#666666", "#E8884A", "#4A90E8", "#5AA469", "#9B59B6"]


def _fetch_report(path: str, params: dict | None = None) -> dict | None:
    """GET a /reports/* endpoint, surfacing errors in the UI (mirrors Insights tab)."""
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params or {}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        st.error(f"Error loading {path}: {detail or str(e)}")
    except requests.exceptions.RequestException as e:
        st.error(f"Error loading {path}: {e}")
    return None

EXAMPLE_QUESTIONS = [
    "Top 10 products by revenue",
    "Sales by region this quarter",
    "How does rain affect sales?",
    "Monthly revenue trend for 2024",
]

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="VelocityIQ",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS — white background, red accents ─────────────────────────────────
st.markdown(
    """
<style>
/* Global white background */
.stApp { background-color: #FFFFFF; }
.stMain { background-color: #FFFFFF; }

/* Primary button — red fill */
.stButton > button[kind="primary"] {
    background-color: #CC0000 !important;
    color: #FFFFFF !important;
    border: none !important;
    font-weight: 600;
    padding: 0.45rem 1.6rem;
}
.stButton > button[kind="primary"]:hover {
    background-color: #AA0000 !important;
    color: #FFFFFF !important;
}

/* Secondary buttons — used for example chips */
.stButton > button[kind="secondary"] {
    background-color: #FFF5F5 !important;
    color: #CC0000 !important;
    border: 1px solid #CC0000 !important;
    font-size: 0.82rem;
    padding: 0.3rem 0.6rem;
}
.stButton > button[kind="secondary"]:hover {
    background-color: #CC0000 !important;
    color: #FFFFFF !important;
}

/* Active tab indicator */
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: #CC0000 !important;
    border-bottom: 3px solid #CC0000 !important;
    font-weight: 600;
}
.stTabs [data-baseweb="tab"] {
    color: #CC0000 !important;
}

/* Expander header */
details summary p {
    color: #CC0000 !important;
    font-weight: 600;
}

/* Text area focus ring */
textarea:focus { border-color: #CC0000 !important; box-shadow: 0 0 0 1px #CC0000; }

/* Divider */
hr { border-top: 1px solid #FFCCCC; }

/* Section labels (Summary, Results) */
.viq-section-label {
    font-size: 0.85rem;
    color: #CC0000;
    font-weight: 600;
    margin-bottom: 4px;
}

/* Loading card */
@keyframes viq-bounce {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
    40%            { transform: scale(1.0); opacity: 1.0; }
}
.viq-loading {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 20px 24px;
    margin-top: 16px;
    border: 1px solid #FFCCCC;
    border-radius: 10px;
    background: #FFF5F5;
}
.viq-dots { display: flex; gap: 6px; }
.viq-dots span {
    width: 10px; height: 10px;
    background: #CC0000;
    border-radius: 50%;
    display: inline-block;
    animation: viq-bounce 1.2s infinite ease-in-out;
}
.viq-dots span:nth-child(2) { animation-delay: 0.2s; }
.viq-dots span:nth-child(3) { animation-delay: 0.4s; }
.viq-loading-text p { margin: 0; }
.viq-loading-title { font-weight: 600; color: #CC0000; font-size: 0.95rem; }
.viq-loading-sub   { font-size: 0.82rem; color: #999; margin-top: 2px !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#1A1A1A; margin-bottom:0'>Velocity<span style='color:#CC0000'>IQ</span></h1>",
    unsafe_allow_html=True,
)
st.caption("AI-powered sales intelligence")

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_insights, tab_dashboard, tab_reports = st.tabs(["Insights", "Dashboard", "Reports"])

# ── Insights tab ───────────────────────────────────────────────────────────────
with tab_insights:
    # Session state initialisation
    if "question" not in st.session_state:
        st.session_state.question = ""
    if "result" not in st.session_state:
        st.session_state.result = None
    if "api_error" not in st.session_state:
        st.session_state.api_error = None

    # ── Example chips ──────────────────────────────────────────────────────────
    st.markdown(
        "<p style='color:#888; font-size:0.83rem; margin-bottom:6px'>Try an example:</p>",
        unsafe_allow_html=True,
    )
    chip_cols = st.columns(len(EXAMPLE_QUESTIONS))
    for i, example in enumerate(EXAMPLE_QUESTIONS):
        if chip_cols[i].button(example, key=f"chip_{i}", use_container_width=True):
            st.session_state.question = example
            st.rerun()

    # ── Text area ──────────────────────────────────────────────────────────────
    question = st.text_area(
        "Ask a question about your sales data",
        key="question",
        placeholder="e.g. What were the top 5 products by net revenue in Q4 2024?",
        height=80,
        label_visibility="visible",
    )

    # ── Submit ─────────────────────────────────────────────────────────────────
    submitted = st.button("Get Insight", type="primary")
    loading_slot = st.empty()

    if submitted:
        q = st.session_state.question.strip()
        if not q:
            st.warning("Please enter a question first.")
        else:
            st.session_state.api_error = None
            st.session_state.result = None
            loading_slot.markdown(
                """
                <div class="viq-loading">
                    <div class="viq-dots">
                        <span></span><span></span><span></span>
                    </div>
                    <div class="viq-loading-text">
                        <p class="viq-loading-title">Generating insight…</p>
                        <p class="viq-loading-sub">Translating your question to SQL and summarising results — this can take up to 60 s</p>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            try:
                resp = requests.post(
                    f"{API_BASE}/insight",
                    json={"question": q},
                    timeout=200,
                )
                resp.raise_for_status()
                st.session_state.result = resp.json()
            except requests.exceptions.HTTPError as e:
                detail = ""
                try:
                    detail = e.response.json().get("detail", "")
                except Exception:
                    pass
                st.session_state.api_error = detail or str(e)
            except requests.exceptions.RequestException as e:
                st.session_state.api_error = str(e)
            finally:
                loading_slot.empty()

    # ── Error ──────────────────────────────────────────────────────────────────
    if st.session_state.api_error:
        st.error(f"Error: {st.session_state.api_error}")

    # ── Results ────────────────────────────────────────────────────────────────
    if st.session_state.result:
        data = st.session_state.result
        st.markdown("---")

        # Data table
        if data.get("data_points"):
            st.markdown(
                f"<p class='viq-section-label'>"
                f"Results &mdash; {data['row_count']} row{'s' if data['row_count'] != 1 else ''}</p>",
                unsafe_allow_html=True,
            )
            df = pd.DataFrame(data["data_points"])
            static_view = st.toggle("Static table", value=False)
            if static_view:
                st.table(df)
            else:
                st.dataframe(df, use_container_width=True)
        else:
            st.info(
                "No rows returned. Make sure sample data is loaded: "
                "`python scripts/load_sample_data.py`"
            )

        # Generated SQL — collapsible
        with st.expander("View generated SQL"):
            st.code(data["sql"], language="sql")

        # Summary narrative
        st.markdown(
            f"<p style='margin-top:16px; color:#333333; font-size:0.95rem'>{data['summary']}</p>",
            unsafe_allow_html=True,
        )

        st.caption(f"Generated at {data.get('generated_at', '')} · {data['row_count']} rows")

# ── Dashboard tab ──────────────────────────────────────────────────────────────
with tab_dashboard:
    st.info("Dashboard — coming soon")

# ── Reports tab ───────────────────────────────────────────────────────────────
def _section_label(text: str) -> None:
    st.markdown(f"<p class='viq-section-label'>{text}</p>", unsafe_allow_html=True)


def _style_fig(fig: go.Figure, height: int = 380) -> go.Figure:
    """Apply the white/red brand styling shared by every report chart."""
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=60, b=10),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        font=dict(color="#000000", size=13),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.04,
            xanchor="left", x=0,
            font=dict(color="#000000", size=12),
            bgcolor="rgba(255,255,255,0)",
            bordercolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_xaxes(showgrid=False, linecolor=BRAND_RED_LIGHT,
                     tickfont=dict(color="#000000", size=12),
                     title_font=dict(color="#000000", size=13))
    fig.update_yaxes(gridcolor="#F0F0F0", linecolor=BRAND_RED_LIGHT,
                     tickfont=dict(color="#000000", size=12),
                     title_font=dict(color="#000000", size=13))
    return fig


def _raw_table(data_points: list[dict]) -> None:
    with st.expander("View data"):
        st.dataframe(pd.DataFrame(data_points), use_container_width=True)


_MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_BAND_ORDER = ["0%", "0-10%", "10-20%", "20%+"]


def _render_seasonal_section() -> None:
    """Seasonal Revenue — trend bars + SARIMA forecast under one shared header."""
    # Single header row: label on the left, horizon dropdown pinned to the right (5 %)
    hdr_col, drop_col = st.columns([19, 1])
    with hdr_col:
        _section_label("Seasonal Revenue")
    with drop_col:
        horizon = st.selectbox(
            "Horizon",
            options=list(range(1, 11)),
            index=2,
            key="sf_horizon",
            label_visibility="collapsed",
        )

    trend_data = _fetch_report("/reports/seasonal-trend")
    fc_data = _fetch_report("/reports/seasonal-forecast", {"horizon": horizon})

    trend_col, fc_col, _ = st.columns([4, 4, 2])

    # ── Trend chart ────────────────────────────────────────────────────────────
    with trend_col:
        st.caption("Monthly net revenue distribution across years.")
        if trend_data:
            points = trend_data.get("data_points", [])
            if points:
                df = pd.DataFrame(points)
                monthly = (
                    df.groupby(["year", "month"], as_index=False)["net_revenue"].sum()
                    .sort_values(["year", "month"])
                )
                fig = go.Figure()
                for i, (yr, grp) in enumerate(monthly.groupby("year")):
                    fig.add_trace(go.Bar(
                        x=[_MONTH_NAMES[m] for m in grp["month"]],
                        y=grp["net_revenue"],
                        name=str(yr),
                        marker_color=BRAND_SEQUENCE[i % len(BRAND_SEQUENCE)],
                    ))
                fig.update_layout(barmode="group", yaxis_title="Net revenue", xaxis_title="Month")
                st.plotly_chart(_style_fig(fig), use_container_width=True)
                _raw_table(points)
            else:
                st.info("No trend data available.")

    # ── Forecast chart ─────────────────────────────────────────────────────────
    with fc_col:
        st.caption("SARIMA forward forecast with 95% confidence interval.")
        if fc_data:
            points = fc_data.get("forecast", [])
            if points:
                df = pd.DataFrame(points)
                df["label"] = df["month"].apply(lambda m: calendar.month_abbr[m]) + " " + df["year"].astype(str)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df["label"], y=df["upper"],
                    mode="lines", line=dict(width=0),
                    showlegend=False, name="Upper CI",
                ))
                fig.add_trace(go.Scatter(
                    x=df["label"], y=df["lower"],
                    mode="lines", line=dict(width=0),
                    fill="tonexty",
                    fillcolor="rgba(224,85,85,0.15)",
                    showlegend=True, name="95% CI",
                ))
                fig.add_trace(go.Scatter(
                    x=df["label"], y=df["predicted_net_revenue"],
                    mode="lines+markers",
                    line=dict(color=BRAND_RED, width=2),
                    marker=dict(size=7, color=BRAND_RED),
                    name="Forecast",
                ))
                fig.update_layout(
                    xaxis_title="Month",
                    yaxis_title="Net Revenue ($)",
                    yaxis_tickformat="$,.0f",
                )
                _style_fig(fig)
                st.plotly_chart(fig, use_container_width=True)
                trained_at = fc_data.get("model_trained_at", "unknown")
                st.caption(f"Model trained at: {trained_at}")
                _raw_table(points)
            else:
                st.info("No forecast data — run POST /train_seasonal_forecast first.")


def _render_category_leakage() -> None:
    """Report 4 — stacked net+discount (= gross) bars with discount_rate overlay."""
    _section_label("Category Revenue Leakage")
    st.caption("Compares gross vs net revenue per category — the stacked segment shows how much revenue is lost to discounts, with discount rate overlaid on the right axis.")
    data = _fetch_report("/reports/category-leakage")
    if not data:
        return
    points = data.get("data_points", [])
    if not points:
        st.info("No data available.")
        return

    df = pd.DataFrame(points)
    fig = go.Figure()
    # Stack height = gross_revenue; the discount segment is the visible "leakage".
    fig.add_trace(go.Bar(
        x=df["category"], y=df["net_revenue"], name="Net revenue",
        marker_color=BRAND_RED,
    ))
    fig.add_trace(go.Bar(
        x=df["category"], y=df["total_discount"], name="Discount (leakage)",
        marker_color=BRAND_RED_LIGHT,
    ))
    fig.add_trace(go.Scatter(
        x=df["category"], y=df["discount_rate"], name="Discount rate",
        mode="lines+markers", yaxis="y2",
        line=dict(color=BRAND_RED_DARK, width=2, dash="dot"),
    ))
    fig.update_layout(
        barmode="stack",
        yaxis_title="Revenue",
        yaxis2=dict(title="Discount rate", overlaying="y", side="right",
                    tickformat=".1%", showgrid=False),
    )
    st.plotly_chart(_style_fig(fig), use_container_width=True)

    summary = data.get("summary", {})
    if summary.get("highest_leakage_category"):
        st.caption(
            f"Highest leakage: **{summary['highest_leakage_category']}** "
            f"(discount rate {summary.get('highest_discount_rate', 0):.1%})"
        )
    _raw_table(points)


def _render_discount_effectiveness() -> None:
    """Report 2 — pie chart: net revenue share by discount band."""
    _section_label("Discount Effectiveness")
    st.caption("Shows what share of net revenue comes from each discount band — helping identify whether heavy discounting drives meaningful volume.")
    data = _fetch_report("/reports/discount-effectiveness")
    if not data:
        return
    points = data.get("data_points", [])
    if not points:
        st.info("No data available.")
        return

    df = pd.DataFrame(points)
    categories = ["All categories"] + sorted(df["category"].unique().tolist())
    choice = st.selectbox("Category", categories, key="de_category")
    plot_df = df if choice == "All categories" else df[df["category"] == choice]

    band_totals = (
        plot_df.groupby("discount_band", as_index=False)["net_revenue"].sum()
    )
    band_totals["discount_band"] = pd.Categorical(
        band_totals["discount_band"], categories=_BAND_ORDER, ordered=True
    )
    band_totals = band_totals.sort_values("discount_band")

    fig = go.Figure(go.Pie(
        labels=band_totals["discount_band"],
        values=band_totals["net_revenue"],
        hole=0.45,
        marker=dict(colors=BRAND_SEQUENCE[:len(band_totals)]),
        textinfo="label+percent",
        textfont=dict(color="#000000", size=13),
        insidetextorientation="radial",
    ))
    st.plotly_chart(_style_fig(fig), use_container_width=True)

    lifts = data.get("summary", {}).get("higher_discount_lifts_quantity")
    if lifts is not None:
        verdict = "do" if lifts else "do not"
        st.caption(f"Across all data, higher discount bands **{verdict}** show higher avg quantity.")
    _raw_table(points)


def _render_concentration_risk() -> None:
    """Report 1 — Pareto: descending revenue bars + cumulative % line with 80% marker."""
    _section_label("Revenue Concentration Risk (Pareto)")
    st.caption("Ranks SKUs by net revenue and overlays cumulative share — the 80% line reveals how few products drive the majority of revenue.")
    data = _fetch_report("/reports/concentration-risk")
    if not data:
        return
    points = data.get("data_points", [])
    if not points:
        st.info("No data available.")
        return

    df = pd.DataFrame(points).sort_values("revenue_rank")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["product_name"], y=df["net_revenue"], name="Net revenue",
        marker_color=BRAND_RED,
    ))
    fig.add_trace(go.Scatter(
        x=df["product_name"], y=df["cumulative_revenue_pct"], name="Cumulative %",
        mode="lines+markers", yaxis="y2",
        line=dict(color=BRAND_RED_DARK, width=2.5),
    ))
    # 80% Pareto reference line on the cumulative axis.
    fig.add_hline(y=0.80, line_dash="dash", line_color=BRAND_GREY, yref="y2",
                  annotation_text="80%", annotation_position="right")
    fig.update_layout(
        yaxis_title="Net revenue",
        yaxis2=dict(title="Cumulative %", overlaying="y", side="right",
                    tickformat=".0%", range=[0, 1.05], showgrid=False),
        xaxis_title="SKU (ranked)",
    )
    st.plotly_chart(_style_fig(fig), use_container_width=True)

    summary = data.get("summary", {})
    skus = summary.get("skus_covering_80pct_revenue")
    if skus:
        st.caption(
            f"**{skus}** SKU{'s' if skus != 1 else ''} account for ≥80% of net revenue · "
            f"top SKU: **{summary.get('top_sku')}** ({summary.get('top_sku_revenue_pct', 0):.1%})"
        )
    _raw_table(points)


with tab_reports:
    col, _ = st.columns([3, 2])
    with col:
        _render_concentration_risk()
    st.markdown("---")
    col, _ = st.columns([3, 2])
    with col:
        _render_discount_effectiveness()
    st.markdown("---")
    _render_seasonal_section()
    st.markdown("---")
    col, _ = st.columns([3, 2])
    with col:
        _render_category_leakage()
