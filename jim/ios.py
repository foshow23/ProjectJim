"""The iOS look, so the web app feels like a factory Apple application; grouped lists on a gray
background, large titles, a tab bar at the bottom, and sheets.

How it works: Python builds small pieces of HTML (always escaping text), a stylesheet makes them
look like iOS, and one tiny JavaScript component reports which element was tapped.
"""

from __future__ import annotations

from contextlib import contextmanager
from html import escape

import altair as alt
import streamlit as st

# --- Colors -----------------------------------------------------------------------------------
# Apple's system colors for light and dark mode.

_LIGHT = {
    "bg": "#F2F2F7", "card": "#FFFFFF", "sheet": "#F2F2F7", "sheet-card": "#FFFFFF",
    "label": "#000000", "secondary": "rgba(60,60,67,.6)", "tertiary": "rgba(60,60,67,.3)",
    "separator": "rgba(60,60,67,.2)", "fill": "rgba(118,118,128,.12)", "bar": "rgba(249,249,249,.85)",
    "navbar": "rgba(242,242,247,.85)",
    "segment": "#FFFFFF", "blue": "#007AFF", "green": "#34C759", "orange": "#FF9500", "red": "#FF3B30",
    "purple": "#AF52DE", "indigo": "#5856D6", "cyan": "#32ADE6",
}
_DARK = {
    "bg": "#000000", "card": "#1C1C1E", "sheet": "#1C1C1E", "sheet-card": "#2C2C2E",
    "label": "#FFFFFF", "secondary": "rgba(235,235,245,.6)", "tertiary": "rgba(235,235,245,.3)",
    "separator": "rgba(84,84,88,.65)", "fill": "rgba(118,118,128,.24)", "bar": "rgba(22,22,24,.82)",
    "navbar": "rgba(0,0,0,.8)",
    "segment": "#636366", "blue": "#0A84FF", "green": "#30D158", "orange": "#FF9F0A", "red": "#FF453A",
    "purple": "#BF5AF2", "indigo": "#5E5CE6", "cyan": "#64D2FF",
}


def is_dark() -> bool:
    """Whether to use the dark colors. Follows `base` in .streamlit/config.toml (now "light"),
    not the device's setting."""
    return st.get_option("theme.base") == "dark"


def color(name: str) -> str:
    """A system color for the current appearance, e.g. color("orange"), for charts and maps."""
    return (_DARK if is_dark() else _LIGHT)[name]


# --- Stylesheet -------------------------------------------------------------------------------

WIDTH = 560     # the column is phone-shaped, like the app was

_CSS = """
/* Page frame: no Streamlit header, a phone-width column, room for the tab bar. */
[data-testid="stHeader"] { display: none; }
[data-testid="stMainBlockContainer"] { max-width: %(width)spx; padding: 6px 16px 120px; }
[data-testid="stMain"] { scrollbar-width: none; }
.stApp { background: var(--ios-bg); -webkit-font-smoothing: antialiased; }

/* Icons: Google's Material Symbols, which Streamlit already ships. */
.ms { font-family: "Material Symbols Rounded"; font-weight: 400; font-style: normal; font-size: 24px;
      line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap; direction: ltr;
      display: inline-block; font-feature-settings: "liga"; -webkit-font-smoothing: antialiased;
      user-select: none; flex: none; }

/* Navigation: toolbar buttons, the large title, and the bar on pushed screens. */
.ios-toolbar { display: flex; justify-content: flex-end; align-items: center; gap: 2px; min-height: 44px; }
.ios-tool { background: none; border: 0; padding: 8px; margin: 0; color: var(--ios-blue); cursor: pointer;
            display: flex; border-radius: 50%%; }
.ios-tool:active, .ios-back:active { opacity: .4; }
.ios-tool:disabled { color: var(--ios-tertiary); cursor: default; }
.ios-tool[data-id="remove"]:not(:disabled) { color: var(--ios-red); }
.ios-large-title { font-size: 34px; font-weight: 700; letter-spacing: .37px; line-height: 41px;
                   color: var(--ios-label); margin: 0 0 2px 2px; padding: 0; }
.ios-navbar { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; min-height: 44px; }
.ios-back { justify-self: start; display: flex; align-items: center; background: none; border: 0;
            padding: 8px 8px 8px 0; margin-left: -8px; color: var(--ios-blue); font: inherit; font-size: 17px;
            cursor: pointer; }
.ios-back .ms { font-size: 30px; margin-right: -4px; }
.ios-navbar-title { font-size: 17px; font-weight: 600; color: var(--ios-label); text-align: center;
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 280px; }
.ios-navbar .ios-toolbar { justify-self: end; }
.st-key-navbar { position: sticky; top: 0; z-index: 90; background: var(--ios-navbar);
                 backdrop-filter: saturate(180%%) blur(20px); -webkit-backdrop-filter: saturate(180%%) blur(20px);
                 margin: -6px -16px 0; padding: 0 16px; width: auto; }

/* Tab bar. */
.st-key-tabbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 100; background: var(--ios-bar);
                 backdrop-filter: saturate(180%%) blur(20px); -webkit-backdrop-filter: saturate(180%%) blur(20px);
                 border-top: .5px solid var(--ios-separator); padding-bottom: env(safe-area-inset-bottom); }
.ios-tabs { display: flex; max-width: %(width)spx; margin: 0 auto; }
.ios-tab { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 7px 0 8px;
           background: none; border: 0; cursor: pointer; color: var(--ios-secondary); font: inherit;
           font-size: 10px; font-weight: 500; }
.ios-tab .ms { font-size: 27px; }
.ios-tab.on { color: var(--ios-blue); }
.ios-tab.on .ms { font-variation-settings: "FILL" 1; }

/* Grouped lists. */
.ios-section + .ios-section { margin-top: 26px; }
.ios-header { display: flex; justify-content: space-between; gap: 12px; padding: 0 16px 7px; font-size: 13px;
              line-height: 16px; text-transform: uppercase; color: var(--ios-secondary); }
.ios-footer { padding: 7px 16px 0; font-size: 13px; line-height: 17px; color: var(--ios-secondary); }
.ios-card { background: var(--ios-card); border-radius: 12px; overflow: hidden; }
.ios-row { position: relative; display: flex; align-items: center; gap: 12px; width: 100%%; min-height: 44px;
           padding: 0 16px; margin: 0; background: none; border: 0; text-align: left; font: inherit;
           font-size: 17px; line-height: 22px; color: var(--ios-label); }
button.ios-row { cursor: pointer; }
button.ios-row:active { background: var(--ios-fill); }
.ios-row + .ios-row::before { content: ""; position: absolute; top: 0; left: 16px; right: 0; height: .5px;
                              background: var(--ios-separator); }
.ios-row.has-icon + .ios-row.has-icon::before { left: 60px; }
.ios-row > .ms { width: 32px; text-align: center; font-size: 26px; }
.ios-leading { width: 20px; font-size: 15px; color: var(--ios-secondary); font-variant-numeric: tabular-nums; }
.ios-body { flex: 1; min-width: 0; padding: 11px 0; }
.ios-title-line { display: flex; align-items: baseline; gap: 8px; }
.ios-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ios-title.two { white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.ios-detail { font-size: 15px; color: var(--ios-secondary); white-space: nowrap; }
.ios-subtitle { font-size: 15px; line-height: 20px; color: var(--ios-secondary); margin-top: 1px; }
.ios-row.small .ios-subtitle { font-size: 12px; line-height: 16px; }
.ios-value { color: var(--ios-secondary); white-space: nowrap; font-variant-numeric: tabular-nums; }
.ios-chevron { color: var(--ios-tertiary); font-size: 22px !important; margin: 0 -8px 0 -8px; width: auto !important; }
.ios-row.headline .ios-title { font-weight: 600; }
.ios-row.action, .ios-row.action > .ms { color: var(--ios-blue); }
.ios-row.destructive { color: var(--ios-red); }
.ios-row.center .ios-title { text-align: center; }
.ios-row.action > .ms, .ios-row.plain > .ms { font-size: 22px; width: 24px; }
.ios-row.mono .ios-title { font-variant-numeric: tabular-nums; }
.ios-row.muted .ios-title { color: var(--ios-secondary); font-size: 15px; white-space: normal; }
.ios-row.macro .ios-detail { font-size: 17px; font-variant-numeric: tabular-nums; }
.ios-progress { height: 4px; border-radius: 2px; background: var(--ios-fill); overflow: hidden; margin-top: 7px; }
.ios-progress > div { height: 100%%; border-radius: 2px; }

/* Bits used inside rows. */
.ios-big { display: flex; align-items: baseline; gap: 6px; }
.ios-big-number { font-family: ui-rounded, "SF Pro Rounded", -apple-system, sans-serif; font-size: 34px;
                  line-height: 41px; font-weight: 700; }
.ios-secondary { color: var(--ios-secondary); }
.ios-spacer { flex: 1; }
.ios-small { font-size: 15px; }
.ios-caption { font-size: 12px; line-height: 16px; color: var(--ios-secondary); }
.ios-daypicker { display: flex; align-items: center; justify-content: space-between; min-height: 52px; padding: 4px 6px; }
.ios-daypicker button { background: none; border: 0; color: var(--ios-blue); cursor: pointer; font: inherit; padding: 8px; display: flex; }
.ios-daypicker button:disabled { color: var(--ios-tertiary); cursor: default; }
.ios-day { display: flex; flex-direction: column; align-items: center; }
.ios-day-title { color: var(--ios-label) !important; font-size: 17px !important; font-weight: 600 !important; gap: 6px; align-items: center; padding: 4px 8px !important; }
.ios-day-title .ms { font-size: 20px; color: var(--ios-blue); }
.ios-day-today { font-size: 12px !important; padding: 0 8px 4px !important; }

/* "Nothing here yet" screens. */
.ios-unavailable { text-align: center; padding: 56px 24px 8px; }
.ios-unavailable .ms { font-size: 56px; color: var(--ios-secondary); }
.ios-unavailable h3 { font-size: 22px; font-weight: 700; margin: 10px 0 6px; padding: 0; color: var(--ios-label); }
.ios-unavailable p { font-size: 15px; line-height: 20px; color: var(--ios-secondary); margin: 0; }

/* Chat bubbles and answer chips (AI estimator). */
.ios-bubble { display: inline-block; max-width: 85%%; padding: 8px 12px; border-radius: 18px; font-size: 16px;
              line-height: 21px; background: var(--ios-fill); color: var(--ios-label); white-space: pre-wrap; }
.ios-bubble-row { display: flex; margin: 6px 0; }
.ios-bubble-row.me { justify-content: flex-end; }
.ios-bubble-row.me .ios-bubble { background: var(--ios-blue); color: #fff; }
.ios-question { font-size: 15px; font-weight: 600; margin: 12px 0 6px; color: var(--ios-label); }
.ios-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.ios-chip { background: var(--ios-fill); color: var(--ios-blue); border: 0; border-radius: 999px; padding: 7px 14px;
            font: inherit; font-size: 15px; cursor: pointer; }
.ios-chip:active { opacity: .5; }
.ios-macros { display: flex; margin: 10px 0 4px; }
.ios-macros > div { flex: 1; }
.ios-macros b { display: block; font-size: 15px; font-variant-numeric: tabular-nums; }

/* Streamlit's own widgets, nudged toward iOS. */
[class*="st-key-card-"] { background: var(--ios-card); border-radius: 12px; padding: 12px 16px; overflow: hidden; }
[class*="st-key-card-flush-"] { padding: 0; }
[class*="st-key-card-"] .ios-row { padding-left: 0; padding-right: 0; }
[class*="st-key-card-"] .ios-row + .ios-row::before { left: 0; }
.stButton button, .stFormSubmitButton button, .stLinkButton a, .stDownloadButton button {
    border: 0; border-radius: 12px; min-height: 44px; font-size: 17px; }
.stButton button[kind="secondary"], .stFormSubmitButton button[kind="secondaryFormSubmit"], .stLinkButton a[kind="secondary"] {
    background: var(--ios-fill); color: var(--ios-blue); }
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"] { min-height: 50px; font-weight: 600; }
.stButton button[kind="primary"] p, .stFormSubmitButton button[kind="primaryFormSubmit"] p { font-weight: 600; }
.stButton button[kind="tertiary"] { color: var(--ios-blue); min-height: 0; }
[class*="st-key-destructive"] button { color: var(--ios-red) !important; }
.stButton button p, .stLinkButton a p { font-size: 17px; }
.stButtonGroup [role="radiogroup"] { background: var(--ios-fill); border-radius: 9px; padding: 2px; gap: 0;
                                    flex-wrap: nowrap; width: 100%%; border: 0; }
.stButtonGroup button[data-variant="segmented_control"] {
    flex: 1; border: 0 !important; border-radius: 7px !important; background: transparent !important;
    min-height: 28px; padding: 3px 8px; box-shadow: none; }
.stButtonGroup button[data-variant="segmented_control"] p { font-size: 13px; font-weight: 500; color: var(--ios-label); }
.stButtonGroup button[data-variant="segmented_control"][data-selected="true"] {
    background: var(--ios-segment) !important; box-shadow: 0 3px 8px rgba(0,0,0,.12), 0 3px 1px rgba(0,0,0,.04); }
.stButtonGroup button[data-variant="segmented_control"][data-selected="true"] p { font-weight: 600; }
[data-testid="stWidgetLabel"] p { font-size: 13px; color: var(--ios-secondary); }
[data-testid="stDialog"] { --ios-card: var(--ios-sheet-card); }
[data-testid="stDialog"] > div { background: var(--ios-sheet); border-radius: 14px; }
[data-testid="stForm"] { border: 0; padding: 0; }
[data-testid="stAlert"] { border-radius: 12px; }
[data-testid="stElementToolbar"] { display: none; }
[data-testid="stFileUploaderDropzone"] { background: transparent; padding: 4px 0; }
[data-testid="stFileUploaderDropzone"] button { background: var(--ios-fill); color: var(--ios-blue); border: 0;
                                                border-radius: 12px; }
"""


def style() -> None:
    """Adds the stylesheet. Called once at the top of every run, from app.py."""
    palette = _DARK if is_dark() else _LIGHT
    variables = "".join(f"--ios-{name}:{value};" for name, value in palette.items())
    st.html(f"<style>:root{{{variables}}}{_CSS % {'width': WIDTH}}</style>")


# --- Taps -------------------------------------------------------------------------------------
# The one piece of JavaScript: show the HTML Python built, and report the data-id of whatever
# is tapped. It also remembers how far the page was scrolled, so coming back from a workout
# returns to the same place in the list.

_TAP_JS = """
export default function (component) {
    const { data, parentElement, setTriggerValue } = component;
    const root = parentElement.querySelector(".jim-root");
    if (!root) return;
    root.innerHTML = data.html;
    const scroller = document.querySelector('[data-testid="stMain"]');
    const scrollKey = "jim-scroll:" + data.name;
    if (data.restore && scroller) {
        const saved = Number(sessionStorage.getItem(scrollKey) || 0);
        requestAnimationFrame(() => scroller.scrollTo(0, saved));
    }
    root.onclick = (event) => {
        const target = event.target.closest("[data-id]");
        if (!target || !root.contains(target) || target.disabled) return;
        if (scroller) sessionStorage.setItem(scrollKey, scroller.scrollTop);
        setTriggerValue("tapped", target.dataset.id);
    };
}
"""
_SCROLL_JS = """
export default function (component) {
    const scroller = document.querySelector('[data-testid="stMain"]');
    if (scroller) scroller.scrollTo(0, 0);
}
"""
_tappable = st.components.v2.component("jim_tappable", html='<div class="jim-root"></div>', js=_TAP_JS,
                                       isolate_styles=False)
_scroller = st.components.v2.component("jim_scroll_top", js=_SCROLL_JS, isolate_styles=False)


def tap(html: str, key: str, restore_scroll: bool = False, width: str = "stretch") -> str | None:
    """Shows HTML and returns the `data-id` of the element just tapped, if any."""
    result = _tappable(data={"html": html, "name": key, "restore": restore_scroll}, key=key, width=width,
                       on_tapped_change=lambda: None)
    return result.tapped


def show(html: str) -> None:
    """Shows HTML that has nothing to tap."""
    st.html(html)


def scroll_to_top(token: str) -> None:
    """Scrolls the page to the top when a new screen (identified by `token`) appears."""
    with st.container(key="scrolltop", height=1, border=False):
        _scroller(data={"token": token}, key="scroll_top")


# --- Building blocks --------------------------------------------------------------------------

def icon(name: str, tint: str = "", size: int | None = None) -> str:
    styles = (f"color:var(--ios-{tint});" if tint else "") + (f"font-size:{size}px;" if size else "")
    return f'<span class="ms" style="{styles}">{escape(name)}</span>'


def progress(fraction: float, tint: str) -> str:
    percent = max(0.0, min(fraction, 1.0)) * 100
    return f'<div class="ios-progress"><div style="width:{percent:.1f}%;background:var(--ios-{tint})"></div></div>'


def row(title: str = "", subtitle: str = "", value: str = "", *, id: str | None = None, icon_name: str = "",
        tint: str = "", leading: str = "", leading_tint: str = "", detail: str = "", kind: str = "",
        chevron: bool | None = None, two_lines: bool = False, below: str = "") -> str:
    """One row of a list. Give it an `id` and it can be tapped.

    `detail` sits at the end of the title line (a date), `value` at the end of the row,
    `leading` is short text before the title (a set number). `kind` is any of: headline,
    action, destructive, center, small, mono, plain. `below` is ready-made HTML placed under
    the text, such as a progress bar.
    """
    tappable = id is not None
    if chevron is None:
        chevron = tappable and not ({"action", "destructive", "center"} & set(kind.split()))
    parts = []
    if icon_name:
        parts.append(icon(icon_name, tint))
    if leading:
        tinted = f' style="color:var(--ios-{leading_tint})"' if leading_tint else ""
        parts.append(f'<span class="ios-leading"{tinted}>{escape(leading)}</span>')
    body = f'<div class="ios-title-line"><span class="ios-title{" two" if two_lines else ""}">{escape(title)}</span>'
    if detail:
        body += f'<span class="ios-detail">{escape(detail)}</span>'
    body += "</div>"
    if subtitle:
        body += f'<div class="ios-subtitle">{escape(subtitle)}</div>'
    parts.append(f'<div class="ios-body">{body}{below}</div>')
    if value:
        parts.append(f'<span class="ios-value">{escape(value)}</span>')
    if chevron:
        parts.append('<span class="ms ios-chevron">chevron_right</span>')
    classes = " ".join(part for part in ("ios-row", kind, "has-icon" if icon_name else "") if part)
    if tappable:
        return f'<button class="{classes}" data-id="{escape(id, quote=True)}">{"".join(parts)}</button>'
    return f'<div class="{classes}">{"".join(parts)}</div>'


def block(html: str) -> str:
    """Ready-made HTML as a row, for content that isn't title-and-value."""
    return f'<div class="ios-row"><div class="ios-body">{html}</div></div>'


def bold(line: str) -> str:
    """Turns **bold** into <strong>bold</strong>. Splitting on ** puts the bold parts at the odd
    positions: "a **b** c" -> ["a ", "b", " c"]. A ** with no partner is left as written."""
    parts = line.split("**")
    result = ""
    for number, part in enumerate(parts):
        if number % 2 == 0:
            result += part                          # outside the stars
        elif number < len(parts) - 1:
            result += f"<strong>{part}</strong>"    # between a pair of **
        else:
            result += "**" + part                   # a lone ** at the end
    return result


def simple_markdown(text: str) -> str:
    """The bits of Markdown the AI coach writes, as HTML: **bold**, "- " bullet points and
    "#" headings. Each line is escaped first, so the text can't add any other HTML."""
    html = ""
    in_list = False
    for line in text.strip().split("\n"):
        line = line.strip()
        is_bullet = line[:2] in ("- ", "* ", "• ")
        is_heading = line.startswith("#")
        if is_bullet:
            line = line[2:]
        if is_heading:
            line = "**" + line.lstrip("#").strip() + "**"     # a heading shows as a bold line
        line = bold(escape(line))

        if is_bullet:
            if not in_list:
                html += '<ul style="margin:2px 0;padding-left:20px">'
                in_list = True
            html += f"<li>{line}</li>"
            continue
        if in_list:
            html += "</ul>"
            in_list = False
        if line == "":
            html += '<div style="height:6px"></div>'     # a blank line: a small gap
        else:
            html += f"<div>{line}</div>"
    if in_list:
        html += "</ul>"
    return html


def header(text: str, trailing: str = "") -> str:
    return f'<div class="ios-header"><span>{escape(text)}</span><span>{escape(trailing)}</span></div>'


def footer(text: str) -> str:
    return f'<div class="ios-footer">{escape(text)}</div>'


def rows(items: list[str]) -> str:
    """Rows without their own card, for inside a `card()`."""
    return "".join(items)


def section(items: list[str], title: str = "", footnote: str = "", trailing: str = "") -> str:
    """A rounded group of rows with an optional header above and footnote below."""
    html = header(title, trailing) if title else ""
    html += f'<div class="ios-card">{"".join(items)}</div>'
    if footnote:
        html += footer(footnote)
    return f'<div class="ios-section">{html}</div>'


def unavailable(icon_name: str, title: str, text: str) -> str:
    """The centered "nothing here yet" message."""
    return (f'<div class="ios-unavailable">{icon(icon_name)}<h3>{escape(title)}</h3>'
            f'<p>{escape(text)}</p></div>')


@contextmanager
def card(key: str, title: str = "", footnote: str = "", trailing: str = "", flush: bool = False):
    """A rounded group holding Streamlit widgets (charts, pickers) instead of plain rows.
    `flush` drops the padding, for maps."""
    with st.container(key=f"group-{key}", gap=None):
        if title:
            st.html(header(title, trailing))
        with st.container(key=f"card-{'flush-' if flush else ''}{key}"):
            yield
        if footnote:
            st.html(footer(footnote))


def day_axis(days: list[str]):
    """An x axis for a chart with one mark per day ("2026-10-07"): a letter per weekday for a
    week, a few dates for anything longer."""
    if len(days) <= 7:
        return alt.Axis(title=None, labelAngle=0, labelExpr="slice(utcFormat(toDate(datum.value), '%a'), 0, 1)")
    return alt.Axis(title=None, labelAngle=0, values=days[::max(1, len(days) // 5)],
                    labelExpr="utcFormat(toDate(datum.value), '%b %-d')")


def chart(spec, height: int = 180) -> None:
    """Shows an Altair chart drawn straight onto its card: no background or frame of its own."""
    spec = spec.properties(height=height, background="transparent").configure_view(strokeWidth=0)
    st.altair_chart(spec.configure_axis(labelFontSize=11, titleFontSize=11, labelColor=color("secondary"),
                                        titleColor=color("secondary"), gridColor=color("separator"),
                                        domainColor=color("separator"), tickColor=color("separator")))


# --- Navigation -------------------------------------------------------------------------------
# Each tab can have one screen "pushed" on top of its list (a workout's detail). What's pushed is
# kept in Streamlit's session state, so it survives switching tabs, like a navigation stack.

def pushed(tab: str):
    """What's pushed on this tab (a workout ID), or None when its first screen is showing."""
    return st.session_state.get(f"{tab}_pushed")


def push(tab: str, value) -> None:
    st.session_state[f"{tab}_pushed"] = value
    st.rerun()


def pop(tab: str) -> None:
    st.session_state.pop(f"{tab}_pushed", None)
    st.session_state["restore_scroll"] = tab     # return to the same place in the list


def restoring_scroll(tab: str) -> bool:
    """True once, right after coming back to this tab's list."""
    if st.session_state.get("restore_scroll") == tab:
        del st.session_state["restore_scroll"]
        return True
    return False


def _tools(actions: list[tuple[str, str, str]]) -> str:
    buttons = "".join(
        f'<button class="ios-tool" data-id="{escape(id, quote=True)}" title="{escape(label, quote=True)}" '
        f'aria-label="{escape(label, quote=True)}">{icon(icon_name)}</button>'
        for id, icon_name, label in actions)
    return f'<div class="ios-toolbar">{buttons}</div>'


def large_title(title: str, actions: list[tuple[str, str, str]] = ()) -> str | None:
    """The top of a tab: toolbar buttons (id, icon, label) and the big title.
    Returns the id of a tapped button."""
    return tap(f'{_tools(list(actions))}<h1 class="ios-large-title">{escape(title)}</h1>', key="large_title")


def nav_bar(title: str, back: str, actions: list[tuple[str, str, str]] = ()) -> str | None:
    """The bar on a pushed screen: a back button, a centered title and toolbar buttons.
    Returns "back" or the id of a tapped button."""
    html = (f'<div class="ios-navbar"><button class="ios-back" data-id="back">{icon("chevron_left")}'
            f'{escape(back)}</button><div class="ios-navbar-title">{escape(title)}</div>{_tools(list(actions))}</div>')
    with st.container(key="navbar"):
        return tap(html, key="nav_bar")


def tab_bar(tabs: list[tuple[str, str, str]], selected: str) -> str | None:
    """The bar at the bottom. `tabs` are (id, title, icon). Returns the id of a tapped tab."""
    buttons = "".join(
        f'<button class="ios-tab{" on" if id == selected else ""}" data-id="{escape(id, quote=True)}">'
        f'{icon(icon_name)}<span>{escape(title)}</span></button>'
        for id, title, icon_name in tabs)
    with st.container(key="tabbar"):
        return tap(f'<div class="ios-tabs">{buttons}</div>', key="tab_bar")
