"""Bodyweight exercises: a pull-up lifts your body weight that day, not 0 kg."""

from conftest import START, count, hevy

from jim import bodyweight, workout_views
from jim.merger import ingest
from jim.models import SourceSet

DAY = 86_400


def test_which_exercises_are_bodyweight():
    assert bodyweight.share_of_body_weight("Pull Up") == 1.0
    assert bodyweight.share_of_body_weight("Wide Pull Up") == 1.0
    assert bodyweight.share_of_body_weight("Triceps Dip (Assisted)") == 1.0
    assert bodyweight.share_of_body_weight("Push Up") == 0.64
    assert bodyweight.share_of_body_weight("Seated Dip Machine") is None
    assert bodyweight.share_of_body_weight("Bench Press (Barbell)") is None


def test_the_closest_weigh_in_before_the_workout_is_used():
    weights = [(START - 10 * DAY, 92.0), (START - 2 * DAY, 90.0), (START + 5 * DAY, 88.0)]
    assert bodyweight.body_weight_on(weights, START) == 90.0
    assert bodyweight.body_weight_on(weights, START - 20 * DAY) == 92.0     # none before: the first one
    assert bodyweight.body_weight_on([], START) is None


def test_set_loads():
    assert bodyweight.set_load("Pull Up", None, 90.0) == 90.0
    assert bodyweight.set_load("Pull Up", 10.0, 90.0) == 100.0              # a dip belt adds weight
    assert bodyweight.set_load("Pull Up (Assisted)", 20.0, 90.0) == 70.0    # the machine takes some off
    assert bodyweight.set_load("Push Up", None, 100.0) == 64.0
    assert bodyweight.set_load("Bench Press (Barbell)", 80.0, 90.0) == 80.0
    assert bodyweight.set_load("Pull Up", None, None) is None               # no weigh-ins yet
    assert bodyweight.set_load("Pull Up (Assisted)", 20.0, None) is None


def test_workout_volume_includes_body_weight(conn):
    conn.execute("INSERT INTO body_metrics (metric, ts, value, source) VALUES ('weight', ?, 90.0, 'Scale')",
                 (START - DAY,))
    ingest(conn, [hevy(sets=[SourceSet(0, "Pull Up", 0, set_type="warmup", reps=5),
                             SourceSet(0, "Pull Up", 1, reps=8),
                             SourceSet(1, "Bench Press", 0, weight_kg=60.0, reps=10)])])
    assert count(conn, "workouts") == 1
    stats = list(workout_views.strength_stats(conn).values())[0]
    assert stats == {"exercises": 2, "volume": 90.0 * 8 + 60.0 * 10}       # the warm-up isn't counted
