"""The Nutrition tab's sheets: add food (search, barcode, create, AI estimate), choose the
amount, daily goals, and meal sections.

A sheet is a Streamlit dialog. Add Food has several screens (search -> amount, and so on);
which one is showing, and the food being added, live in st.session_state["food_sheet"].
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html import escape

import streamlit as st

from . import estimator, food_search, ios, nutrition, ui
from .foods import VOLUME_UNITS, Food, Portion, food_from_amount

STATE = "food_sheet"


def _token() -> str:
    """A fresh suffix for widget keys, so a reopened sheet starts blank instead of remembering
    what was typed last time."""
    return uuid.uuid4().hex[:8]


# --- Opening the sheets -----------------------------------------------------------------------

def add_food(day: date, meal: str | None) -> None:
    """Opens Add Food for a day. `meal` is the section to add to, or None to pick by time of day."""
    st.session_state[STATE] = {"screen": "search", "day": day, "meal": meal, "token": _token()}
    _add_sheet()


def edit_entry(entry_id: int) -> None:
    """Opens a logged food to change its amount or meal, or delete it."""
    conn = ui.connection()
    entry = conn.execute("SELECT * FROM food_entries WHERE id=?", (entry_id,)).fetchone()
    if entry is None:
        return
    food = Food.from_json(entry["food_json"])
    if entry["source"] != nutrition.LOGGED_HERE or food is None:
        _imported_note()
        return
    st.session_state[STATE] = {"screen": "amount", "day": date.fromisoformat(entry["day"]), "meal": entry["meal"],
                               "food": food, "entry": dict(entry), "token": _token()}
    _edit_sheet()


@st.dialog("Add Food")
def _add_sheet() -> None:
    _screen()


@st.dialog("Edit Food")
def _edit_sheet() -> None:
    _screen()


@st.dialog("Imported from Lose It!")
def _imported_note() -> None:
    st.write("This food came from your Lose It! export, so a newer export would undo changes made here. "
             "Edit it in Lose It!, or add it again in ProjectJim.")
    if st.button("OK", type="primary", width="stretch"):
        st.rerun()


def _screen() -> None:
    state = st.session_state[STATE]
    screens = {"search": _search, "amount": _amount, "create": _create, "estimate": _estimate}
    screens[state["screen"]](ui.connection(), state)


def _go(state: dict, screen: str, **changes) -> None:
    """Shows another screen of the sheet."""
    state.update(changes, screen=screen)
    st.rerun(scope="fragment")


def _choose(state: dict, food: Food) -> None:
    """A food was picked: on to its amount."""
    _go(state, "amount", food=food, came_from=state["screen"], token=_token(), detailed=False)


def _back(state: dict, label: str, screen: str) -> None:
    if st.button(label, icon=":material/chevron_left:", type="tertiary", key=f"back_{state['token']}"):
        _go(state, screen)


# --- Search -----------------------------------------------------------------------------------

_online_cache: dict[tuple[str, str], tuple[float, list[Food]]] = {}


def _remembered(source: str, search, query: str) -> list[Food]:
    """Runs an online search, remembering results for an hour: repeating a search is instant
    and doesn't use up USDA's hourly limit."""
    key = (source, query.lower())
    saved = _online_cache.get(key)
    if saved and time.time() - saved[0] < 3600:
        return saved[1]
    foods = search(query)
    _online_cache[key] = (time.time(), foods)
    return foods


def search_online(query: str) -> list[tuple[list[Food], str | None]]:
    """USDA and Open Food Facts at the same time. Each gives (foods, error message or None)."""
    def run(source):
        name, search = source
        try:
            return _remembered(name, search, query), None
        except food_search.FoodSearchError as error:
            return [], str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, [("usda", food_search.usda_search), ("off", food_search.off_search)]))


def _result_row(food: Food, index: int, times: int = 0) -> str:
    parts = [f"{times}×"] if times > 1 else []
    if food.brand:
        parts.append(food.brand)
    if food.portions:
        portion = food.portions[0]
        parts.append(f"{food.calories * portion.grams / 100:.0f} kcal per {portion.label}")
    else:
        parts.append(f"{food.calories:.0f} kcal per 100 g")
    return ios.row(food.name, " · ".join(parts), id=f"food:{index}", kind="small", two_lines=True)


def _search(conn, state: dict) -> None:
    """Your own foods (offline), USDA and Open Food Facts (online), a barcode, or a new food."""
    token = state["token"]
    query = st.text_input("Search", value=state.get("query", ""), key=f"query_{token}", icon=":material/search:",
                          placeholder="Search foods, e.g. jollof rice", label_visibility="collapsed")
    state["query"] = query = (query or "").strip()
    yours = nutrition.personal_foods(conn)
    shown: list[Food] = []      # every food listed below; a row's id is its position here

    def results(foods: list[Food], counts: list[int] | None = None) -> list[str]:
        items = []
        for offset, food in enumerate(foods):
            items.append(_result_row(food, len(shown), counts[offset] if counts else 0))
            shown.append(food)
        return items

    if not query:
        with ios.card(f"barcode_{token}"):
            with st.container(horizontal=True, vertical_alignment="bottom"):
                barcode = st.text_input("Barcode", key=f"barcode_{token}", placeholder="Type a barcode",
                                        label_visibility="collapsed")
                digits = "".join(c for c in barcode or "" if c.isdigit())
                look_up = st.button("Look Up", key=f"lookup_{token}", disabled=len(digits) < 8)
            if look_up:
                try:
                    with st.spinner("Looking up…"):
                        product = food_search.off_product(digits)
                except food_search.FoodSearchError as error:
                    st.caption(f":orange[{error}]")
                else:
                    _choose(state, product)
        html = ios.section([
            ios.row("Estimate with AI (Photo or Description)", id="estimate", icon_name="auto_awesome", kind="action"),
            ios.row("Create a Food", id="create", icon_name="edit_square", kind="action"),
        ])
        frequent = [(food, count) for food, count in yours if count >= 2][:20]
        if frequent:
            html += ios.section(results([f for f, _ in frequent], [c for _, c in frequent]), title="Frequent")
        recent = nutrition.recent_foods(conn)
        if recent:
            html += ios.section(results(recent), title="Recent")
    else:
        matches = nutrition.matching(query, yours)[:25]
        html = ios.section(
            results([f for f, _ in matches], [c for _, c in matches]) + [
                ios.row(f"Create “{query}”", id="create", icon_name="edit_square", kind="action"),
                ios.row("Estimate with AI", id="estimate", icon_name="auto_awesome", kind="action"),
            ], title="Your Foods")
        if len(query) < 2:
            online = [([], "Keep typing…"), ([], "Keep typing…")]
        else:
            with st.spinner("Searching USDA and Open Food Facts…"):
                online = search_online(query)
        titles = ["Whole Foods & Dishes · USDA", "Brands & Packaged · Open Food Facts"]
        for title, (foods, error) in zip(titles, online):
            items = results(foods) or [ios.row(error or "No matches.", kind="muted")]
            html += ios.section(items, title=title)

    tapped = ios.tap(html, key=f"results_{token}")
    if tapped == "estimate":
        _go(state, "estimate", chat=None)
    elif tapped == "create":
        _go(state, "create")
    elif tapped and tapped.startswith("food:"):
        _choose(state, shown[int(tapped.split(":")[1])])


# --- Amount -----------------------------------------------------------------------------------

def _number(value: float | None, digits: int = 1) -> str:
    return f"{round(value, digits):g}"


def _grams_typed(amount_key: str, unit_key: str, grams_key: str) -> None:
    """Typing a weight switches the amount to grams."""
    st.session_state[unit_key] = "g"
    st.session_state[amount_key] = st.session_state[grams_key]


def _amount(conn, state: dict) -> None:
    """How much, in any unit, and which meal; shows the nutrition live. Also edits a logged food."""
    token = state["token"]
    entry = state.get("entry")
    if not entry:
        came_from = state.get("came_from", "search")
        _back(state, {"search": "Search", "create": "Search", "estimate": "Estimate"}[came_from],
              "estimate" if came_from == "estimate" else "search")
        # Search results lack portions (USDA) or the serving size (Open Food Facts): fetch them once.
        if not state.get("detailed"):
            state["detailed"] = True
            with st.spinner("Loading serving size…"):
                state["food"] = food_search.with_details(state["food"])
    food: Food = state["food"]

    ios.show(ios.section([ios.row(food.name, food.brand or "", kind="headline", two_lines=True)]))

    labels = food.unit_labels()
    amount_key, unit_key, grams_key, meal_key = (f"{name}_{token}" for name in ("amount", "unit", "grams", "meal"))
    sections = nutrition.sections(conn)
    if unit_key not in st.session_state:    # first showing: start from the usual amount, or the logged one
        if entry and entry["units"] in labels:
            unit, amount = entry["units"], entry["quantity"]
        elif entry:
            unit, amount = "g", entry["grams"] or entry["quantity"]
        else:
            unit, amount = food.default_unit(), food.default_amount()
        st.session_state[unit_key], st.session_state[amount_key] = unit, float(amount)
        meal = state.get("meal") or sections[nutrition.default_section_index(len(sections))]
        st.session_state[meal_key] = meal

    def unit_name(label: str) -> str:
        portion = next((p for p in food.portions if p.label == label), None)
        if portion and not food.portion_only and " g)" not in label:
            return f"{label} ({_number(portion.grams)} g)"
        return label

    density_known = food.density_info()[1] != "assumed"
    is_volume = st.session_state[unit_key] in VOLUME_UNITS
    if food.portion_only:
        note = f"Measured in {labels[0] if labels else 'servings'}s as you logged it before; its weight isn't known."
    elif is_volume or density_known:
        note = food.density_explanation()
    else:
        note = ""
    with ios.card(f"amount_{token}", title="Amount", footnote=note):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            amount = st.number_input("Amount", min_value=0.0, step=1.0, format="%g", key=amount_key) or 0.0
            unit = st.selectbox("Unit", labels, format_func=unit_name, key=unit_key)
        grams = food.grams(amount, unit)
        if not food.portion_only:
            st.session_state[grams_key] = round(grams, 1)   # keep the weight in step with the amount
            st.number_input("Weight (g)", min_value=0.0, step=1.0, format="%g", key=grams_key,
                            on_change=_grams_typed, args=(amount_key, unit_key, grams_key))
            if unit in VOLUME_UNITS or density_known:
                ios.show(ios.rows([ios.row("Volume", value=f"≈ {_number(food.milliliters(grams))} ml")]))

    options = sections if st.session_state[meal_key] in sections else sections + [st.session_state[meal_key]]
    with ios.card(f"meal_{token}", title="Meal"):
        meal = st.selectbox("Meal", options, key=meal_key, label_visibility="collapsed")

    values = food.nutrients(grams)
    facts = [ios.row("Calories", value=f"{values['calories']:.0f} kcal")]
    for name, key, suffix in [("Protein", "protein", "g"), ("Carbs", "carbs", "g"), ("Fat", "fat", "g"),
                              ("Fiber", "fiber", "g"), ("Sugar", "sugars", "g"),
                              ("Saturated fat", "saturated_fat", "g"), ("Sodium", "sodium", "mg")]:
        if values[key] is not None:
            facts.append(ios.row(name, value=f"{_number(values[key])} {suffix}"))
    origin = "an AI estimate" if food.id.startswith("custom:ai-") else food.source_name
    ios.show(ios.section(facts, title="Nutrition", footnote=f"Nutrition from {origin}."))

    if st.button("Save Changes" if entry else f"Add to {meal}", type="primary", width="stretch", disabled=amount <= 0,
                 key=f"save_{token}"):
        if entry:
            nutrition.update_entry(conn, entry["id"], food, amount, unit, meal)
        else:
            nutrition.add_entry(conn, food, amount, unit, meal, state["day"])
        st.rerun()      # closes the sheet and shows the day with the food in it
    if entry and st.button("Delete Food", width="stretch", key=f"destructive_{token}"):
        nutrition.delete_entry(conn, entry["id"])
        st.rerun()


# --- Create a food ----------------------------------------------------------------------------

def _create(conn, state: dict) -> None:
    """A food that isn't in any database (a homemade dish, a local restaurant meal), from its
    nutrition for one serving. It's saved to Your Foods."""
    token = state["token"]
    _back(state, "Search", "search")
    with st.form(f"create_{token}", border=False):
        with ios.card(f"create_name_{token}"):
            name = st.text_input("Name", value=state.get("query", ""), placeholder="e.g. Ewa Riro")
            brand = st.text_input("Brand or restaurant (optional)")
        with ios.card(f"create_serving_{token}", title="Serving", footnote=(
                "With a weight, you can also log this food in grams, ounces or cups. Without one, it's logged "
                "in servings.")):
            label = st.text_input("Serving", value="serving", placeholder="plate, cup, piece")
            grams = st.number_input("Weight of one serving in grams (optional)", min_value=0.0, value=None, format="%g")
        with ios.card(f"create_nutrition_{token}", title="Nutrition per Serving"):
            fields = [("calories", "Calories (kcal)"), ("protein", "Protein (g)"), ("carbs", "Carbs (g)"),
                      ("fat", "Fat (g)"), ("fiber", "Fiber (g)"), ("sugars", "Sugar (g)"), ("sodium", "Sodium (mg)")]
            nutrients = {key: st.number_input(title, min_value=0.0, value=None, format="%g") for key, title in fields}
        saved = st.form_submit_button("Save", type="primary", width="stretch")
    if not saved:
        return
    name, label = (name or "").strip(), (label or "").strip() or "serving"
    if not name or nutrients["calories"] is None:
        st.caption(":orange[A food needs a name and its calories.]")
        return
    # With a weight, the food is stored per gram; without, as servings.
    food = food_from_amount(id=f"custom:{uuid.uuid4()}", name=name, brand=(brand or "").strip() or None,
                            quantity=grams or 1, units="Grams" if grams else label, nutrients=nutrients,
                            source="custom")
    if grams:
        food.portions = [Portion(label, grams)]
    nutrition.save_food(conn, food)
    _choose(state, food)


# --- AI estimate ------------------------------------------------------------------------------

def _estimate_card(estimate: estimator.Estimate) -> str:
    macros = "".join(
        f'<div><b style="color:var(--ios-{tint})">{grams:.0f} g</b><span class="ios-caption">{name}</span></div>'
        for name, grams, tint in [("Protein", estimate.protein, "blue"), ("Carbs", estimate.carbs, "orange"),
                                  ("Fat", estimate.fat, "purple")])
    low = estimate.confidence == "low"
    assumptions = "".join(f"<div>• {escape(text)}</div>" for text in estimate.assumptions)
    return ios.section([ios.block(
        f'<div class="ios-big"><div style="flex:1;min-width:0"><div style="font-weight:600">{escape(estimate.name)}</div>'
        f'<div class="ios-subtitle">{escape(estimate.portion)}</div></div>'
        f'<span style="font-size:20px;font-weight:700;white-space:nowrap">{estimate.calories:.0f} kcal</span></div>'
        f'<div class="ios-macros">{macros}</div>'
        f'<div class="ios-caption" style="font-weight:600;{"color:var(--ios-orange)" if low else ""}">'
        f'Confidence: {escape(estimate.confidence.capitalize())}</div>'
        f'<div class="ios-caption" style="margin-top:4px">{assumptions}</div>')])


def _send(state: dict, text: str, photo: bytes | None = None) -> None:
    """Adds your message to the chat and asks the model for the next reply."""
    chat = state["chat"]
    chat["error"] = None
    try:
        image = estimator.prepare_photo(photo) if photo else None
    except Exception:
        chat["error"] = "That photo couldn't be read. Try a JPEG or PNG."
        return
    chat["messages"].append(estimator.user_message(text, image))
    try:
        with st.spinner("Thinking…"):
            reply, answer = estimator.ask(chat["messages"])
    except estimator.EstimatorError as error:
        chat["messages"].pop()      # so the same message can be sent again
        chat["error"] = str(error)
        return
    chat["messages"].append(answer)
    chat["turns"] += [{"me": True, "text": text.strip(), "image": image}, {"me": False, "reply": reply}]


def _estimate(conn, state: dict) -> None:
    """Chat with the AI to estimate a meal: add a photo, describe it, tap answers to a couple of
    quick questions, then log the estimate."""
    token = state["token"]
    if not state.get("chat"):
        state["chat"] = {"messages": [], "turns": [], "error": None}
    chat = state["chat"]
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        _back(state, "Search", "search")
        if chat["turns"] and st.button("Start Over", icon=":material/restart_alt:", type="tertiary", key=f"restart_{token}"):
            _go(state, "estimate", chat=None, token=_token())

    if not chat["turns"]:
        intro = ('<div style="font-weight:600;display:flex;gap:6px;align-items:center">'
                 f'{ios.icon("auto_awesome", "blue", 20)}Snap or describe your meal</div>'
                 '<div class="ios-subtitle">Add a photo, describe it (“jollof rice with fried plantain and chicken”), '
                 "or both. The AI may ask a quick question or two, like how oily it was, then estimates the calories "
                 "and macros for you to log.</div>")
        if not estimator.is_configured():
            intro += ('<div class="ios-caption" style="color:var(--ios-orange);margin-top:6px">Needs an Anthropic '
                      "API key: add ANTHROPIC_API_KEY to the .env file and restart.</div>")
        ios.show(ios.section([ios.block(intro)]))

    for index, turn in enumerate(chat["turns"]):
        latest = index == len(chat["turns"]) - 1
        if turn["me"]:
            if turn["image"]:
                with st.container(horizontal=True, horizontal_alignment="right"):
                    st.image(turn["image"], width=160)
            if turn["text"]:
                ios.show(f'<div class="ios-bubble-row me"><div class="ios-bubble">{escape(turn["text"])}</div></div>')
            continue
        reply: estimator.Reply = turn["reply"]
        html = f'<div class="ios-bubble-row"><div class="ios-bubble">{escape(reply.message)}</div></div>'
        if latest:      # quick replies only on the latest turn, so old ones can't be re-sent
            for number, question in enumerate(reply.questions):
                chips = "".join(f'<button class="ios-chip" data-id="{number}:{choice}">{escape(option)}</button>'
                                for choice, option in enumerate(question.options))
                html += f'<div class="ios-question">{escape(question.question)}</div><div class="ios-chips">{chips}</div>'
        answer = ios.tap(html, key=f"turn_{token}_{index}")
        if answer:
            number, choice = (int(part) for part in answer.split(":"))
            question = reply.questions[number]
            _send(state, f"{question.question} {question.options[choice]}")
            st.rerun(scope="fragment")
        if reply.estimate:
            ios.show(_estimate_card(reply.estimate))
            if st.button("Log This", icon=":material/add_circle:", type="primary", width="stretch",
                         key=f"log_{token}_{index}"):
                _choose(state, reply.estimate.to_food())
            st.caption("An estimate: you can adjust the amount before saving, or reply to correct it.")

    if chat["error"]:
        st.caption(f":orange[{chat['error']}]")

    use_camera = st.toggle("Use the camera", key=f"camera_on_{token}")
    with st.form(f"composer_{token}", clear_on_submit=True, border=False):
        shot = st.camera_input("Take a photo", label_visibility="collapsed") if use_camera else None
        picked = st.file_uploader("Add a photo of the meal (optional)", type=["jpg", "jpeg", "png", "webp"])
        text = st.text_input("Message", placeholder="Describe your meal…" if not chat["turns"] else "Reply…",
                             label_visibility="collapsed")
        sent = st.form_submit_button("Send", icon=":material/arrow_upward:", type="primary", width="stretch")
    photo = shot or picked
    if sent and ((text or "").strip() or photo):
        _send(state, text or "", photo.getvalue() if photo else None)
        st.rerun(scope="fragment")


# --- Goals and meals --------------------------------------------------------------------------

@st.dialog("Daily Goals")
def edit_goals() -> None:
    conn = ui.connection()
    current = nutrition.goals(conn)
    titles = {"calories": "Calories (kcal)", "protein": "Protein (g)", "carbs": "Carbs (g)", "fat": "Fat (g)"}
    values = {}
    with ios.card("goals"):
        for name, title in titles.items():
            lowest, highest, step = nutrition.GOAL_RANGES[name]
            values[name] = st.number_input(title, lowest, highest, min(max(current[name], lowest), highest), step,
                                           format="%g")
    if st.button("Done", type="primary", width="stretch"):
        nutrition.save_goals(conn, values)
        st.rerun()


def edit_meals() -> None:
    """Opens the editor for the meal sections: rename, reorder, add and remove."""
    conn = ui.connection()
    st.session_state["meal_rows"] = [{"id": _token(), "original": name, "name": name} for name in nutrition.sections(conn)]
    _meals_sheet()


@st.dialog("Edit Meals")
def _meals_sheet() -> None:
    conn = ui.connection()
    rows: list[dict] = st.session_state["meal_rows"]
    with ios.card("meals", footnote=(
            "Removing a meal doesn't delete its foods; they show under their old meal name. Imported Lose It! "
            "meals are matched to these by position (breakfast, lunch, afternoon snack, dinner, after dinner).")):
        for index, row in enumerate(rows):
            with st.container(horizontal=True, vertical_alignment="center", key=f"mealrow_{row['id']}"):
                row["name"] = st.text_input("Meal name", value=row["name"], key=f"meal_{row['id']}",
                                            placeholder="Meal name", label_visibility="collapsed")
                tools = "".join(
                    f'<button class="ios-tool" data-id="{action}" title="{title}" {"disabled" if off else ""}>'
                    f'{ios.icon(icon)}</button>'
                    for action, icon, title, off in [
                        ("up", "arrow_upward", "Move up", index == 0),
                        ("down", "arrow_downward", "Move down", index == len(rows) - 1),
                        ("remove", "do_not_disturb_on", "Remove", len(rows) == 1)])
                action = ios.tap(f'<div class="ios-toolbar">{tools}</div>', key=f"mealtools_{row['id']}", width="content")
            if action == "up":
                rows.insert(index - 1, rows.pop(index))
            elif action == "down":
                rows.insert(index + 1, rows.pop(index))
            elif action == "remove":
                rows.pop(index)
            if action:
                st.rerun(scope="fragment")
        if st.button("Add Meal", icon=":material/add:", type="tertiary"):
            rows.append({"id": _token(), "original": None, "name": ""})
            st.rerun(scope="fragment")
    if st.button("Reset to Default Meals", width="stretch"):
        st.session_state["meal_rows"] = [{"id": _token(), "original": None, "name": name}
                                         for name in nutrition.DEFAULT_SECTIONS]
        st.rerun(scope="fragment")
    names = [row["name"].strip() for row in rows if row["name"].strip()]
    if st.button("Save", type="primary", width="stretch", disabled=not names):
        # Foods logged in ProjectJim follow a renamed section.
        renamed = {row["original"]: row["name"].strip() for row in rows if row["original"] and row["name"].strip()}
        nutrition.save_sections(conn, [row["name"] for row in rows], renamed)
        st.rerun()


@st.dialog("Choose a Day")
def choose_day(current: date) -> None:
    chosen = st.date_input("Day", value=current, max_value=date.today(), format="MM/DD/YYYY",
                           label_visibility="collapsed")
    if st.button("Done", type="primary", width="stretch"):
        st.session_state["nutrition_day"] = chosen
        st.rerun()
