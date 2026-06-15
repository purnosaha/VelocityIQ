import os

import pandas as pd
import requests
import streamlit as st

API_BASE = os.environ.get("VELOCITYIQ_API", "http://localhost:8000")

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
with tab_reports:
    st.info("Reports — coming soon")
