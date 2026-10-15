"""The Insights coach: its database lookups, the number check, and the ask loop.
No network: a stand-in client plays Claude."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from conftest import garmin, hevy

from jim import coach, merger
from jim.models import SourceSet


def timestamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()


class FakeClient:
    """Records each request and returns the replies the test queued up, in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **request):
        # Copy the messages: the coach keeps adding to the same list after each request.
        self.requests.append(request | {"messages": list(request["messages"])})
        return self.replies.pop(0)


def text_reply(text: str):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def tool_reply(name: str, tool_input: dict):
    block = SimpleNamespace(type="tool_use", id="call-1", name=name, input=tool_input)
    return SimpleNamespace(stop_reason="tool_use", content=[block])


@pytest.fixture
def data(conn):
    """Two runs and two bench press sessions."""
    merger.ingest(conn, [
        garmin("g1", start=timestamp("2026-10-01 07:00"), duration=1800.0, distance=5000.0),
        garmin("g2", start=timestamp("2026-10-08 07:00"), duration=1650.0, distance=5000.0),
        hevy(start=timestamp("2026-10-02 18:00"), sets=[
            SourceSet(0, "Bench Press (Barbell)", 0, set_type="warmup", weight_kg=40.0, reps=10),
            SourceSet(0, "Bench Press (Barbell)", 1, weight_kg=80.0, reps=6),
            SourceSet(1, "Pull Up", 0, reps=8)]),
        hevy(start=timestamp("2026-10-09 18:00"), sets=[
            SourceSet(0, "Bench Press (Barbell)", 0, weight_kg=82.5, reps=6),
            SourceSet(1, "Pull Up", 0, reps=10)]),
    ])
    return conn


def test_workouts_come_with_totals_and_pace(data):
    result = coach.get_workouts(data, "2026-10-01", "2026-10-14", "run")
    assert result["totals"] == {"count": 2, "total_km": 10.0, "total_minutes": 58, "average_run_pace": "5:45 /km"}
    assert [w["pace"] for w in result["workouts"]] == ["5:30 /km", "6:00 /km"]     # newest first


def test_exercise_history_tracks_progress_and_skips_warmups(data):
    result = coach.get_exercise_history(data, "bench", "2026-10-01", "2026-10-14")
    first, last = result["sessions"]
    assert first["sets"] == ["40 x 10 (warmup)", "80 x 6"]
    assert first["estimated_one_rep_max"] == 96.0       # 80 x (1 + 6/30); the warm-up doesn't count
    progress = result["progress_first_to_last_session"]["Bench Press (Barbell)"]["estimated_one_rep_max"]
    assert (progress["first"], progress["last"], progress["change"]) == (96.0, 99.0, 3.0)


def test_bodyweight_exercises_track_reps(data):
    result = coach.get_exercise_history(data, "pull up", "2026-10-01", "2026-10-14")
    assert result["sessions"][0]["sets"] == ["8 reps"]
    assert "estimated_one_rep_max" not in result["sessions"][0]
    assert result["progress_first_to_last_session"]["Pull Up"]["most_reps"]["change"] == 2


def test_pull_ups_count_body_weight_from_the_body_tab(data):
    data.execute("INSERT INTO body_metrics (metric, ts, value, source) VALUES ('weight', ?, 90.0, 'Scale')",
                 (timestamp("2026-09-30 07:00"),))
    first = coach.get_exercise_history(data, "pull up", "2026-10-01", "2026-10-14")["sessions"][0]
    assert first["sets"] == ["90 x 8"]
    assert first["estimated_one_rep_max"] == 114.0      # 90 x (1 + 8/30)


def test_an_unknown_exercise_lists_the_real_names(data):
    result = coach.get_exercise_history(data, "squat", "2026-10-01", "2026-10-14")
    assert result == {"found": False, "exercise_names": ["Bench Press (Barbell)", "Pull Up"]}


def test_body_metrics_use_the_chosen_weight_unit(conn):
    conn.executemany("INSERT INTO body_metrics (metric, ts, value, source) VALUES ('weight', ?, ?, 'Scale')",
                     [(timestamp("2026-09-01 07:00"), 90.0), (timestamp("2026-10-01 07:00"), 88.0)])
    conn.execute("INSERT INTO settings (key, value) VALUES ('weight_unit', '\"lb\"')")
    result = coach.get_body_metric(conn, "weight", "2026-09-01", "2026-10-14")
    assert result["unit"] == "lb"
    assert (result["summary"]["first"], result["summary"]["last"], result["summary"]["change"]) == (198.4, 194.0, -4.4)


def test_numbers_must_come_from_the_data():
    data = json.dumps({"weight": 81.65, "change": -2.35, "pace": "6:47 /km", "date": "2026-10-07"})
    assert coach.unverified_numbers("You weigh about 82 lb, down 2.3 since Oct 7. Pace 6:47.", data) == []
    assert coach.unverified_numbers("You weigh 81.6 lb and ran 12 km.", data) == ["12"]
    # A number from the question itself is fine.
    assert coach.unverified_numbers("Over the last 30 days, 82 lb.", data, "How was the last 30 days?") == []


def test_ask_looks_things_up_then_answers(data):
    client = FakeClient(
        tool_reply("get_workouts", {"start": "2026-10-01", "end": "2026-10-14", "kind": "run"}),
        text_reply("You ran 2 times for 10.0 km, averaging 5:45 /km."),
    )
    answer = coach.ask(data, "How did my running go?", client=client)
    assert answer["text"] == "You ran 2 times for 10.0 km, averaging 5:45 /km."
    assert answer["looked_up"] == ["Workouts (run): 2026-10-01 to 2026-10-14"]
    assert answer["unverified"] == []
    # Only ProjectJim's own tools are offered: no web search.
    assert {tool["name"] for tool in client.requests[0]["tools"]} == set(coach.LOOKUP_NAMES)
    # The tool result went back to Claude.
    assert client.requests[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_an_answer_with_made_up_numbers_is_rewritten_once(data):
    client = FakeClient(
        text_reply("You ran 14 km this week."),           # 14 isn't in the data
        text_reply("I don't have that in ProjectJim."),
    )
    answer = coach.ask(data, "How far did I run?", client=client)
    assert "aren't in the data: 14" in client.requests[1]["messages"][-1]["content"]
    assert (answer["text"], answer["unverified"]) == ("I don't have that in ProjectJim.", [])


def test_numbers_still_unverified_after_the_rewrite_are_flagged(data):
    client = FakeClient(text_reply("You ran 14 km."), text_reply("You ran 14 km."))
    answer = coach.ask(data, "How far did I run?", client=client)
    assert answer["unverified"] == ["14"]


def test_a_bad_date_from_the_ai_is_reported_back_to_it(data):
    client = FakeClient(
        tool_reply("get_workouts", {"start": "last week", "end": "2026-10-14", "kind": "all"}),
        text_reply("I don't have that in ProjectJim."),
    )
    coach.ask(data, "Workouts?", client=client)
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True


def test_without_a_key_it_says_how_to_add_one(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(coach.CoachError, match="ANTHROPIC_API_KEY"):
        coach.ask(conn, "Anything?")


# --- Keeping it quick: shorter lists, a richer overview, low effort -----------------------------

def test_long_body_metric_ranges_are_averaged_by_week_or_month(conn):
    rows = []
    for day in range(100):          # 100 daily step counts from Jan 1, 2026
        rows.append((datetime(2026, 1, 1, 12).timestamp() + day * 86_400, 1000.0 + day))
    conn.executemany("INSERT INTO body_metrics (metric, ts, value, source) VALUES ('steps', ?, ?, 'Watch')", rows)

    short = coach.get_body_metric(conn, "steps", "2026-01-01", "2026-01-31")
    assert (short["readings_per"], len(short["readings"])) == ("day", 31)

    weekly = coach.get_body_metric(conn, "steps", "2026-01-01", "2026-12-31")
    assert weekly["readings_per"] == "week"
    assert weekly["readings"][0] == {"week": "2026-W01", "average": 1002, "readings": 4}     # 1000 to 1003, Thu Jan 1 to Sun Jan 4
    assert weekly["summary"]["count"] == 100        # the summary still covers every day


def test_many_workouts_are_sent_as_monthly_totals(conn):
    runs = []
    for number in range(70):        # a 5 km run every 3 days
        runs.append(garmin(f"g{number}", start=datetime(2026, 1, 1, 7).timestamp() + number * 3 * 86_400,
                           duration=1800.0, distance=5000.0))
    merger.ingest(conn, runs)
    result = coach.get_workouts(conn, "2026-01-01", "2026-12-31", "run")
    assert "workouts" not in result
    assert len(result["most_recent_30"]) == 30
    assert result["by_month"][-1]["month"] == "2026-01" and result["by_month"][-1]["count"] == 11
    assert result["by_month"][-1]["run_pace"] == "6:00 /km"


def test_food_is_averaged_for_strength_days_and_other_days(data):
    data.execute("INSERT INTO food_entries (day, meal, name, quantity, units, calories, protein, source)"
                 " VALUES ('2026-10-02', 'Dinner', 'Chicken', 1, 'Serving', 600, 50, 'Lose It!')")
    data.execute("INSERT INTO food_entries (day, meal, name, quantity, units, calories, protein, source)"
                 " VALUES ('2026-10-03', 'Dinner', 'Rice', 1, 'Serving', 500, 10, 'Lose It!')")
    result = coach.get_food_days(data, "2026-10-01", "2026-10-07")
    assert result["averages_on_strength_days"]["protein"] == 50     # Oct 2 had a Hevy workout
    assert result["averages_on_other_days"]["protein"] == 10
    assert result["averages"]["protein"] == 30


def test_the_overview_summarizes_the_last_four_weeks(conn):
    recent = datetime.now().timestamp() - 2 * 86_400
    merger.ingest(conn, [garmin("g1", start=recent, duration=1500.0, distance=5000.0)])
    summary = coach.overview(conn)["last_4_weeks"]
    assert summary["run"]["count"] == 1 and summary["run"]["average_run_pace"] == "5:00 /km"
    assert "weight" not in summary          # no weigh-ins in the last 4 weeks


def test_requests_use_low_effort_except_on_haiku_4_5(conn, monkeypatch):
    client = FakeClient(text_reply("Nothing to report."))
    coach.ask(conn, "Hi?", client=client)
    assert client.requests[0]["output_config"] == {"effort": "low"}

    monkeypatch.setenv("PROJECTJIM_MODEL", "claude-haiku-4-5")     # doesn't have the setting
    client = FakeClient(text_reply("Nothing to report."))
    coach.ask(conn, "Hi?", client=client)
    assert "output_config" not in client.requests[0]


def test_earlier_questions_are_sent_so_follow_ups_make_sense(data):
    client = FakeClient(text_reply("Same as before: 2 runs."))
    earlier = [{"question": "How many runs this month?", "answer": "You ran 2 times for 10.0 km."}]
    coach.ask(data, "And last month?", earlier, client=client)
    messages = client.requests[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "How many runs this month?"
    assert messages[-1]["content"].endswith("Question: And last month?")


def test_only_the_last_five_earlier_questions_are_sent(data):
    client = FakeClient(text_reply("Done."))
    earlier = [{"question": f"Question {n}?", "answer": "Answer."} for n in range(8)]
    coach.ask(data, "Next?", earlier, client=client)
    assert client.requests[0]["messages"][0]["content"] == "Question 3?"
    assert len(client.requests[0]["messages"]) == 11      # 5 earlier pairs and the new question
