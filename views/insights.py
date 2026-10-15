"""Insights tab: this week's numbers, and an AI coach that answers questions using only your
ProjectJim data (see jim/coach.py for how it's kept to that data)."""

from datetime import date, datetime, time, timedelta
from html import escape

import streamlit as st

from jim import coach, formatting, ios, nutrition, sources_sheet, ui

conn = ui.connection()
if ios.large_title("Insights", [("sources", "sync", "Sources")]) == "sources":
    sources_sheet.show()

# The questions asked so far: [{"question": ..., "answer": the dict coach.ask returns, "error": ...}]
if "insights_chat" not in st.session_state:
    st.session_state["insights_chat"] = []
chat = st.session_state["insights_chat"]


def ask(question: str) -> None:
    """Asks the coach and adds the question and its answer (or what went wrong) to the chat."""
    turn = {"question": question, "answer": None, "error": None}
    # The chat so far, so the coach can follow up. Questions that failed are left out.
    earlier = []
    for old in chat:
        if old["answer"] is not None:
            earlier.append({"question": old["question"], "answer": old["answer"]["text"]})
    try:
        with st.spinner("Looking through your data…"):
            turn["answer"] = coach.ask(conn, question, earlier)
    except coach.CoachError as error:
        turn["error"] = str(error)
    chat.append(turn)


week_start = datetime.combine(date.today() - timedelta(days=6), time.min).timestamp()
workouts = conn.execute("SELECT kind, distance FROM workouts WHERE start_ts >= ?", (week_start,)).fetchall()
cardio_distance = sum(w["distance"] or 0 for w in workouts if w["kind"] != "strength")
logged = [day for day in nutrition.days(conn, date.today(), 7) if day.is_logged]
calories = f"{sum(day.calories for day in logged) / len(logged):.0f} kcal" if logged else "No data"

ios.show(ios.section([
    ios.row("Workouts", value=str(len(workouts))),
    ios.row("Cardio distance", value=formatting.distance(cardio_distance) if cardio_distance else "0 km"),
    ios.row("Strength sessions", value=str(sum(w["kind"] == "strength" for w in workouts))),
    ios.row("Average calories eaten", value=calories),
], title="Last 7 Days"))

if not coach.is_configured():
    ios.show(ios.section([ios.block(
        f'<div class="ios-unavailable" style="padding:20px 8px">{ios.icon("auto_awesome")}<h3>Add an API Key</h3>'
        "<p>The coach needs an Anthropic API key: add ANTHROPIC_API_KEY to the .env file and restart the "
        "app (console.anthropic.com → API Keys).</p></div>")], title="Ask Your Coach"))
    st.stop()

# Before the first question: suggestions you can tap.
if not chat:
    suggestions = ["How has my running pace changed this month?", "Am I eating enough protein on lifting days?",
                   "Which lifts have stalled?", "How has my weight changed this year?"]
    rows = [ios.row(text, id=str(number), icon_name="chat_bubble", tint="blue", chevron=False)
            for number, text in enumerate(suggestions)]
    tapped = ios.tap(ios.section(rows, title="Ask Your Coach",
                                 footnote="Answers come only from your ProjectJim data."), key="suggestions")
    if tapped is not None:
        ask(suggestions[int(tapped)])
        st.rerun()

for turn in chat:
    ios.show(f'<div class="ios-bubble-row me"><div class="ios-bubble">{escape(turn["question"])}</div></div>')
    if turn["error"]:
        st.caption(f":orange[{turn['error']}]")
        continue
    answer = turn["answer"]
    text = escape(answer["text"]).replace("\n", "<br>")
    html = f'<div class="ios-bubble-row"><div class="ios-bubble">{text}</div></div>'
    if answer["unverified"]:
        html += ('<div class="ios-caption" style="color:var(--ios-orange);margin:0 4px 4px">'
                 f'Couldn\'t find these numbers in your data, so check them: {escape(", ".join(answer["unverified"]))}</div>')
    if answer["looked_up"]:
        html += f'<div class="ios-caption" style="margin:0 4px 12px">Looked up: {escape("; ".join(answer["looked_up"]))}</div>'
    else:
        html += '<div class="ios-caption" style="margin:0 4px 12px">From the data overview only.</div>'
    ios.show(html)

with st.form("insights_question", clear_on_submit=True, border=False):
    with st.container(horizontal=True, vertical_alignment="center"):
        question = st.text_input("Question", placeholder="Ask about your progress…", label_visibility="collapsed")
        sent = st.form_submit_button("Ask", icon=":material/arrow_upward:", type="primary")
if sent and question.strip():
    ask(question.strip())
    st.rerun()

if chat and st.button("Clear Chat", icon=":material/restart_alt:", type="tertiary"):
    chat.clear()
    st.rerun()
