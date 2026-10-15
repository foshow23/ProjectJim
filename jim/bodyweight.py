"""Bodyweight exercises: working out how much a pull-up or dip set actually lifted.

Hevy logs a pull-up with no weight, so on its own it would count as 0 kg. Instead, the load is
your body weight from the Body tab on the day of the workout (the closest weigh-in before it),
plus any weight logged on top (a dip belt), or minus it for an assisted version (Hevy logs the
machine's help as the weight).
"""

# How much of your body weight each exercise lifts. Pull-ups, chin-ups, dips and muscle-ups lift
# all of it. A push-up lifts about 64% (Ebben et al., 2011, Journal of Strength and Conditioning
# Research), because your feet hold up the rest.
BODYWEIGHT_SHARE = {
    "pull up": 1.0,
    "chin up": 1.0,
    "dip": 1.0,
    "muscle up": 1.0,
    "push up": 0.64,
}


def share_of_body_weight(exercise):
    """How much of your body weight an exercise lifts (1.0 = all of it), or None if it isn't a
    bodyweight exercise."""
    name = exercise.lower().replace("-", " ")      # "Pull-Up" -> "pull up"
    if "machine" in name:       # e.g. "Seated Dip Machine": the logged weight is the real load
        return None
    for keyword in BODYWEIGHT_SHARE:
        if keyword in name:
            return BODYWEIGHT_SHARE[keyword]
    return None


def weigh_ins(conn):
    """Every weigh-in from the Body tab as a list of (timestamp, kg), oldest first."""
    weights = []
    for row in conn.execute("SELECT ts, value FROM body_metrics WHERE metric = 'weight' ORDER BY ts"):
        weights.append((row["ts"], row["value"]))
    return weights


def body_weight_on(weights, timestamp):
    """Your weight at a moment: the last weigh-in before it, or the first one after it if there's
    none before. None when there are no weigh-ins at all."""
    if len(weights) == 0:
        return None
    latest = weights[0][1]
    for ts, kilograms in weights:
        if ts > timestamp:      # the list is in date order, so everything after this is later too
            break
        latest = kilograms
    return latest


def set_load(exercise, logged_kg, body_kg):
    """The weight a set actually lifted, in kg."""
    share = share_of_body_weight(exercise)
    assisted = "assisted" in exercise.lower()

    # Not a bodyweight exercise: the logged weight is the load.
    if share is None:
        return logged_kg
    # No weigh-ins yet, so body weight is unknown. For an assisted set the logged weight is the
    # machine's help, not the load, so there's nothing to show.
    if body_kg is None:
        if assisted:
            return None
        return logged_kg

    if logged_kg is None:       # nothing logged on top (a plain pull-up)
        logged_kg = 0
    load = body_kg * share
    if assisted:
        return load - logged_kg     # the machine takes some weight off
    return load + logged_kg         # e.g. a dip belt adds weight
