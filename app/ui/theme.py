"""Shared visual theme helpers for the Streamlit UI.

Keeps all styling in one place so the screens stay clean and focused on
content. Everything here is presentation-only — no business logic, no
API details. The CSS is intentionally small and version-tolerant: it
leans on Streamlit's native bordered containers for cards and only adds
light polish (typography, spacing, rounded buttons, status chips).
"""

from __future__ import annotations

import streamlit as st


# Status-chip styles, keyed by a short "kind" string.
_CHIP_KINDS = {
    "ready": "ups-chip-ready",
    "info": "ups-chip-info",
    "missing": "ups-chip-missing",
    "neutral": "ups-chip-neutral",
}


_GLOBAL_CSS = """
<style>
/* ---- Layout ---------------------------------------------------------- */
.block-container {
    padding-top: 2.2rem;
    padding-bottom: 3rem;
    max-width: 1080px;
}

/* ---- Cards (native bordered containers) ------------------------------ */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 14px;
    border: 1px solid #E6EAF0 !important;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    background: #FFFFFF;
}
[data-testid="stVerticalBlockBorderWrapper"] > div {
    padding: 0.25rem 0.25rem;
}

/* ---- Buttons --------------------------------------------------------- */
.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    padding: 0.5rem 1.05rem;
    border: 1px solid #D5DBE3;
    transition: all 0.15s ease;
}
.stButton > button:hover {
    border-color: #2563EB;
    color: #2563EB;
}
.stButton > button[kind="primary"] {
    border: none;
    box-shadow: 0 1px 2px rgba(37, 99, 235, 0.25);
}
.stButton > button[kind="primary"]:hover {
    color: #FFFFFF;
    filter: brightness(1.05);
}
.stDownloadButton > button {
    border-radius: 10px;
    font-weight: 600;
}

/* ---- App header ------------------------------------------------------ */
.ups-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 0.6rem;
}
.ups-title {
    font-size: 1.95rem;
    font-weight: 700;
    color: #101828;
    margin: 0;
    letter-spacing: -0.01em;
}
.ups-subtitle {
    color: #667085;
    font-size: 1.0rem;
    line-height: 1.45;
    margin: 0.3rem 0 0;
    max-width: 720px;
}

/* ---- Status chips ---------------------------------------------------- */
.ups-chip {
    display: inline-block;
    padding: 5px 13px;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    white-space: nowrap;
}
.ups-chip-ready   { background: #E7F6EC; color: #137333; border: 1px solid #B7E3C4; }
.ups-chip-info    { background: #EAF1FE; color: #1D4ED8; border: 1px solid #C7D9FB; }
.ups-chip-missing { background: #FCECEC; color: #B42318; border: 1px solid #F3C2BE; }
.ups-chip-neutral { background: #F2F4F7; color: #475467; border: 1px solid #E4E7EC; }

/* ---- Section labels inside cards ------------------------------------- */
.ups-section {
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #98A2B3;
    margin: 0 0 0.5rem;
}

/* ---- Sidebar steps --------------------------------------------------- */
[data-testid="stSidebar"] .stButton > button {
    text-align: left;
    justify-content: flex-start;
    font-weight: 500;
}
.ups-side-title {
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #98A2B3;
    margin: 0.2rem 0 0.6rem;
}

/* ---- Misc ------------------------------------------------------------ */
[data-testid="stMetricValue"] { font-size: 1.5rem; }
hr { margin: 0.9rem 0; }

/* ---- Animated ambient background ------------------------------------- */
/* A clean, professional "tech dot-grid": a fine dot lattice that slowly
   pans and breathes, lit from behind by soft, drifting colour glows. The
   Streamlit shells are made transparent so the layer shows through in the
   page gutters and between cards; the white cards stay fully opaque, so the
   motion never sits behind any text. Motion is disabled for users who
   request reduced motion. */
html, body {
    background-color: #EEF3FB !important;
    background-image: linear-gradient(180deg, #F4F7FD 0%, #E9EFFA 100%);
    background-attachment: fixed;
}
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stHeader"] {
    background: transparent !important;
}

/* Frosted-glass sidebar so navigation stays crisp over the moving backdrop. */
[data-testid="stSidebar"] {
    background: rgba(255, 255, 255, 0.72) !important;
    backdrop-filter: blur(12px) saturate(1.1);
    -webkit-backdrop-filter: blur(12px) saturate(1.1);
    border-right: 1px solid #E6EAF0;
}

.ups-animated-bg {
    position: fixed;
    inset: 0;
    z-index: -1;            /* behind all Streamlit content */
    overflow: hidden;
    pointer-events: none;   /* never intercepts clicks/scroll */
}
/* Soft colour glows that drift slowly behind the dot lattice. */
.ups-animated-bg .ups-glow {
    position: absolute;
    border-radius: 50%;
    filter: blur(72px);
    opacity: 0.5;
    will-change: transform;
}
.ups-animated-bg .g1 {
    width: 40vmax; height: 40vmax; top: -10vmax; left: -8vmax;
    background: radial-gradient(circle, #6AA6FF 0%, rgba(106,166,255,0) 70%);
    animation: ups-glow-1 30s ease-in-out infinite alternate;
}
.ups-animated-bg .g2 {
    width: 36vmax; height: 36vmax; bottom: -12vmax; right: -8vmax;
    background: radial-gradient(circle, #9B7CFA 0%, rgba(155,124,250,0) 70%);
    animation: ups-glow-2 36s ease-in-out infinite alternate;
}
.ups-animated-bg .g3 {
    width: 30vmax; height: 30vmax; top: 28%; right: 6%;
    background: radial-gradient(circle, #5EE7E0 0%, rgba(94,231,224,0) 70%);
    opacity: 0.32;
    animation: ups-glow-3 32s ease-in-out infinite alternate;
}
/* The dot lattice itself — sits on top of the glows. Densest through the
   central content band and gently fading to the edges (a soft vignette). */
.ups-animated-bg .ups-dotgrid {
    position: absolute;
    inset: -2vmax;
    background-image: radial-gradient(circle, rgba(37, 99, 235, 0.28) 1.6px, transparent 2px);
    background-size: 34px 34px;
    -webkit-mask-image: radial-gradient(135% 105% at 50% 32%, #000 52%, transparent 100%);
    mask-image: radial-gradient(135% 105% at 50% 32%, #000 52%, transparent 100%);
    animation: ups-grid-pan 38s linear infinite, ups-grid-fade 14s ease-in-out infinite;
}
@keyframes ups-grid-pan {
    from { background-position: 0 0; }
    to   { background-position: 68px 68px; }   /* two cells → seamless loop */
}
@keyframes ups-grid-fade {
    0%, 100% { opacity: 0.92; }
    50%      { opacity: 0.62; }
}
@keyframes ups-glow-1 {
    from { transform: translate(0, 0) scale(1); }
    to   { transform: translate(8vw, 6vh) scale(1.12); }
}
@keyframes ups-glow-2 {
    from { transform: translate(0, 0) scale(1.05); }
    to   { transform: translate(-7vw, -5vh) scale(0.94); }
}
@keyframes ups-glow-3 {
    from { transform: translate(0, 0) scale(1); }
    to   { transform: translate(-6vw, 7vh) scale(1.1); }
}
@media (prefers-reduced-motion: reduce) {
    .ups-animated-bg .ups-glow,
    .ups-animated-bg .ups-dotgrid { animation: none !important; }
}
</style>
"""


# The dot-grid + glow layer rendered once behind all content. Kept separate
# from the stylesheet so the (style + markup) pair is injected together. The
# glows come first (furthest back); the dot lattice paints on top of them.
_ANIMATED_BG_HTML = """
<div class="ups-animated-bg" aria-hidden="true">
  <span class="ups-glow g1"></span>
  <span class="ups-glow g2"></span>
  <span class="ups-glow g3"></span>
  <div class="ups-dotgrid"></div>
</div>
"""


def inject_css() -> None:
    """Inject the global stylesheet and the ambient background once per pass."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)
    st.markdown(_ANIMATED_BG_HTML, unsafe_allow_html=True)


def status_chip(label: str, kind: str = "neutral") -> str:
    """Return the HTML for a small pill-shaped status chip."""
    css_class = _CHIP_KINDS.get(kind, _CHIP_KINDS["neutral"])
    return f'<span class="ups-chip {css_class}">{label}</span>'


def render_app_header(
    title: str,
    subtitle: str,
    *,
    chip_label: str | None = None,
    chip_kind: str = "neutral",
) -> None:
    """Render the product header: title, subtitle, and an optional chip."""
    chip_html = (
        f'<div>{status_chip(chip_label, chip_kind)}</div>'
        if chip_label
        else ""
    )
    st.markdown(
        f"""
        <div class="ups-header">
          <div>
            <p class="ups-title">{title}</p>
            <p class="ups-subtitle">{subtitle}</p>
          </div>
          {chip_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()


def section_label(text: str) -> None:
    """Render a small uppercase section label (used inside cards)."""
    st.markdown(f'<p class="ups-section">{text}</p>', unsafe_allow_html=True)


def sidebar_title(text: str) -> None:
    """Render the sidebar section title."""
    st.markdown(f'<p class="ups-side-title">{text}</p>', unsafe_allow_html=True)
