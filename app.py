"""ProjectJim: run with `streamlit run app.py`.

This file is the frame around every screen: the stylesheet, the tab bar, and which tab is showing.
"""

import streamlit as st

from jim import ios

st.set_page_config(page_title="ProjectJim", page_icon="🏋️", layout="centered")

# (id, title, icon). Each tab's screen is views/<id>.py.
TABS = [
    ("cardio", "Cardio", "directions_run"),
    ("strength", "Strength", "fitness_center"),
    ("nutrition", "Nutrition", "restaurant"),
    ("body", "Body", "favorite"),
    ("insights", "Insights", "auto_awesome"),
]
pages = {id: st.Page(f"views/{id}.py", title=title, default=(id == "cardio")) for id, title, _ in TABS}
current = st.navigation(list(pages.values()), position="hidden")
selected = next(id for id, page in pages.items() if page.title == current.title)

ios.style()
tapped = ios.tab_bar(TABS, selected)
if tapped == selected:
    ios.pop(selected)       # tapping the tab you're on goes back to its first screen
elif tapped:
    st.switch_page(pages[tapped])
current.run()
