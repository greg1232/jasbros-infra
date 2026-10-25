"""Measure the wake word honestly, on data the model being measured never saw.

    python train/evaluate.py

Every number this project has printed so far comes from `relearn.py`, whose
job is to decide whether to promote a model — not to say how good one is.
It compares a candidate against whatever is installed, and the installed
one was fitted on everything available at the time, so it is being asked
about its own training data. That is why it keeps reporting 100% recall and
0% false wakes, which is memory rather than skill.

This answers a different question: **how well does this recipe work on
firings it has never seen?** Two ways, both leak-free.

  before learning   The shipped model, fitted only on the recorded corpus
                    in train/, scored on everything from this room. It has
                    genuinely never seen any of it. This is the baseline —
                    what you get with no learning from the room at all.

  after learning    Five-fold cross-validation. The log is cut into five;
                    a model is fitted on the bank, the machine labels, and
                    four fifths of what a person vouched for, then scored
                    on the fifth it did not see. Five models, five
                    disjoint test sets, pooled into one curve. No model is
                    ever asked about its own training data.

Ground truth is what a person can vouch for: clips somebody listened to
and labelled, and enrolment recordings, where somebody said the wake word
on purpose. The augmented copies of enrolment recordings are allowed in
training and never in a test set — scoring a model on variants of its own
training data is the thing this file exists to avoid.

The two kinds of positive are reported apart. Enrolment is deliberate,
clear and close; somebody calling across a room is not, and averaging them
hides the difference that matters.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import relearn  # noqa: E402

SHIPPED = HERE.parent / "models" / "hey_claude_whisper.npz"
# The grid the tables are printed on. The middle of it is deliberately
# dense around where models actually get promoted to: relearn.py picks the
# operating point from the handful of firings held back since the last fit,
# and the whole reason to run this is to see what that line is really worth
# on everything a person has ever vouched for.
LINES = (0.30, 0.50, 0.70, 0.80, 0.825, 0.85, 0.875, 0.90, 0.95, 0.975, 0.99)
FOLDS = 5


def fit(X, y, weights, recipe=None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    recipe = recipe or {}
    scaler = StandardScaler().fit(X)
    if recipe.get("head") == "mlp":
        from sklearn.neural_network import MLPClassifier
        model = MLPClassifier(hidden_layer_sizes=(64,), max_iter=400,
                              alpha=1.0, random_state=0)
        model.fit(scaler.transform(X), y)   # no sample weights in sklearn's
        return scaler, model
    # The default has to be whatever relearn.py actually ships, or this
    # measures a speaker nobody owns. It said 0.1 long after the recipe
    # moved to 0.001 — a hundred times looser — and the curve it drew was
    # not the curve the Pi was running: 8.0% fired on at 0.800 where the
    # shipped recipe does 0.0%.
    model = LogisticRegression(max_iter=5000,
                               C=recipe.get("C", relearn.FIT_HELD_BACK),
                               class_weight="balanced")
    model.fit(scaler.transform(X), y, sample_weight=weights)
    return scaler, model


# Things worth trying, and the one number that says whether they helped.
# Everything is measured at the strictest threshold that still fires on no
# more than a tenth of known mistakes, because a recall figure without a
# false-wake figure beside it means nothing.
RECIPES = {
    "as it is now": {},
    "without the machine's guesses": {"machine": False},
    "without the augmented enrolment copies": {"moved": False},
    "human labels weighted 1x not 5x": {"by_person": 1.0},
    "human labels weighted 20x": {"by_person": 20.0},
    "less regularised (C=1)": {"C": 1.0},
    "more regularised (C=0.01)": {"C": 0.01},
    "a small neural net instead": {"head": "mlp"},
    # C fell monotonically — 1.0 gave 75%, 0.1 gave 85%, 0.01 gave 95% —
    # so it had not obviously bottomed out.
    "more regularised still (C=0.003)": {"C": 0.003},
    "much more regularised (C=0.001)": {"C": 0.001},
    "hardly fitted at all (C=0.0003)": {"C": 0.0003},
    "C=0.01 and no machine guesses": {"C": 0.01, "machine": False},
    "C=0.003 and no machine guesses": {"C": 0.003, "machine": False},
    # The overall figure is flattered by a leak the "said to it" column is
    # free of: the augmented copies of an enrolment recording stay in
    # training even when the recording itself is in the test fold, and a
    # near-duplicate of the thing you are testing on is a leak. There is no
    # link stored from a copy back to its original, so the only way to be
    # sure is to leave the copies out altogether.
    "C=0.001 with no copies at all": {"C": 0.001, "moved": False},
}
AT_MOST_FALSE = 0.10


AT_LEAST_CAUGHT = 0.80


def false_at(p, real, mistakes, wanted=AT_LEAST_CAUGHT):
    """False-wake rate at the strictest threshold still catching `wanted`.

    The other half of the same question. Comparing recall at a fixed false
    rate says one model wins there; comparing false rates at a fixed
    recall says whether it wins at the other end too. A model can do both
    or only one.
    """
    best = (1.0, 0.0)
    for line in np.arange(0.05, 0.999, 0.005):
        caught = float(np.mean(p[real] >= line))
        if caught < wanted:
            continue
        fired = float(np.mean(p[mistakes] >= line))
        if fired < best[0]:
            best = (fired, line)
    return best


def separation(p, real, mistakes):
    """How often a real firing outscores a mistake, over every pair.

    One number for the whole curve, so "better at this threshold" cannot
    hide a model that is worse everywhere else.
    """
    good, bad = p[real], p[mistakes]
    if not len(good) or not len(bad):
        return float("nan")
    order = np.argsort(np.concatenate([good, bad]))
    ranks = np.empty(len(order)); ranks[order] = np.arange(len(order)) + 1
    return float((ranks[:len(good)].sum() - len(good) * (len(good) + 1) / 2)
                 / (len(good) * len(bad)))


def recall_at(p, real, mistakes, budget=AT_MOST_FALSE):
    """Recall at the best threshold that keeps false wakes under budget."""
    best = (0.0, 0.0, 1.0)
    for line in np.arange(0.05, 0.999, 0.005):
        fired = float(np.mean(p[mistakes] >= line))
        if fired > budget:
            continue
        caught = float(np.mean(p[real] >= line))
        if caught > best[0]:
            best = (caught, line, fired)
    return best


def score(scaler, model, X):
    return model.predict_proba(scaler.transform(X))[:, 1]


def table(name, p, real, said, taught, mistakes, say):
    say(f"\n  {name}")
    say(f"  {'line':>6}{'catches':>9}{'false':>7}{'said to it':>12}"
        f"{'taught':>8}")
    for line in LINES:
        each = [np.mean(p[m] >= line) if m.any() else float("nan")
                for m in (real, mistakes, said, taught)]
        say(f"  {line:6.3f}{each[0]:9.0%}{each[1]:7.1%}{each[2]:12.0%}"
            f"{each[3]:8.0%}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folds", type=int, default=FOLDS)
    parser.add_argument("--only", default="",
                        help="only recipes whose name contains this")
    parser.add_argument("--compare", action="store_true",
                        help="cross-validate several training recipes and "
                             "say which is best")
    args = parser.parse_args()
    say = print

    bank_X, bank_y, _who = relearn.load_bank(
        SHIPPED.with_suffix(".bank.npz"))
    X, y, kind, _when = relearn.load_log()

    # What can be tested on, and what can only be trained on.
    vouched = np.isin(kind, ("person", "enrolled"))
    if not vouched.any():
        raise SystemExit("Nothing a person has vouched for yet — "
                         "run ./label.sh first.")

    real = vouched & (y == 1)
    said = (kind == "person") & (y == 1)
    taught = (kind == "enrolled") & (y == 1)
    mistakes = vouched & (y == 0)
    say(f"  ground truth: {int(real.sum())} real "
        f"({int(said.sum())} said to it, {int(taught.sum())} taught), "
        f"{int(mistakes.sum())} mistakes")
    say(f"  training material: {len(bank_X)} recorded, "
        f"{int((~vouched).sum())} logged and labelled by machine")

    # --- the baseline, which has seen none of this ------------------------
    shipped = np.load(SHIPPED)
    p = relearn._probability(X, shipped["mean"], shipped["scale"],
                             shipped["coef"], float(shipped["intercept"]))
    table("before learning — the shipped model, which has seen none of this",
          p, real, said, taught, mistakes, say)

    # --- and the recipe, cross-validated ----------------------------------
    rng = np.random.default_rng(0)
    where = np.flatnonzero(vouched)
    fold = np.zeros(len(X), dtype=int) - 1
    # Split each class separately, so every fold has some of both.
    for label in (0, 1):
        rows = where[y[where] == label]
        rows = rows[rng.permutation(len(rows))]
        fold[rows] = np.arange(len(rows)) % args.folds

    out = np.zeros(len(X))
    for number in range(args.folds):
        train = (fold != number)          # everything not in this fold,
        test = (fold == number)           # including all the machine labels
        weights = np.r_[
            np.ones(len(bank_X)),
            np.where(np.isin(kind[train], ("person", "enrolled")),
                     3.0 * relearn.BY_PERSON, 3.0)]
        scaler, model = fit(np.vstack([bank_X, X[train]]),
                            np.r_[bank_y, y[train]], weights)
        out[test] = score(scaler, model, X[test])
        say(f"    fold {number + 1}/{args.folds}: fitted on "
            f"{len(bank_X) + int(train.sum())}, tested on {int(test.sum())}")

    table(f"after learning — {args.folds}-fold, each row scored by a model "
          "that never saw it", out, real, said, taught, mistakes, say)

    say("\n  The two columns on the right are the same rows split by where "
        "they came from.")
    say("  Enrolment is deliberate and close; being called across a room "
        "is not.")

    if args.compare:
        compare(bank_X, bank_y, X, y, kind, vouched, real, said, taught,
                mistakes, args.folds, say, args.only)
    return 0


def cross_validate(bank_X, bank_y, X, y, kind, vouched, folds, recipe, rng):
    """Out-of-fold scores for every vouched row, under one recipe."""
    where = np.flatnonzero(vouched)
    fold = np.zeros(len(X), dtype=int) - 1
    for label in (0, 1):
        rows = where[y[where] == label]
        rows = rows[rng.permutation(len(rows))]
        fold[rows] = np.arange(len(rows)) % folds

    # What this recipe is allowed to learn from at all.
    allowed = np.ones(len(X), dtype=bool)
    if recipe.get("machine") is False:
        allowed &= (kind != "machine")
    if recipe.get("moved") is False:
        allowed &= (kind != "moved")

    by_person = recipe.get("by_person", relearn.BY_PERSON)
    out = np.zeros(len(X))
    for number in range(folds):
        train = allowed & (fold != number)
        test = (fold == number)
        weights = np.r_[
            np.ones(len(bank_X)),
            np.where(np.isin(kind[train], ("person", "enrolled")),
                     3.0 * by_person, 3.0)]
        scaler, model = fit(np.vstack([bank_X, X[train]]),
                            np.r_[bank_y, y[train]], weights, recipe)
        out[test] = score(scaler, model, X[test])
    return out


def compare(bank_X, bank_y, X, y, kind, vouched, real, said, taught,
            mistakes, folds, say, only=""):
    """Cross-validate each recipe and say which one is actually better."""
    say(f"\n  Recipes, each cross-validated over {folds} folds.")
    say(f"  Recall at the best threshold that fires on at most "
        f"{AT_MOST_FALSE:.0%} of known mistakes:\n")
    say(f"  {'':40}{'catches':>9}{'said':>7}"
        f"{'false @80%':>12}{'separation':>12}")

    results = {}
    for name, recipe in RECIPES.items():
        if only and only not in name and name != "as it is now":
            continue
        rng = np.random.default_rng(0)   # the same folds for every recipe
        p = cross_validate(bank_X, bank_y, X, y, kind, vouched, folds,
                           recipe, rng)
        caught, line, _fired = recall_at(p, real, mistakes)
        spoken = float(np.mean(p[said] >= line)) if said.any() else 0.0
        fired, _ = false_at(p, real, mistakes)
        apart = separation(p, real, mistakes)
        results[name] = (caught, spoken, fired, apart)
        say(f"  {name:40}{caught:9.0%}{spoken:7.0%}{fired:12.1%}"
            f"{apart:12.3f}")

    best = max(results, key=lambda n: results[n][0])
    now = results["as it is now"]
    say(f"\n  catches      recall at no more than {AT_MOST_FALSE:.0%} false")
    say(f"  said         the same, counting only firings said to the speaker")
    say(f"  false @80%   false wakes at the strictest threshold still "
        f"catching {AT_LEAST_CAUGHT:.0%}")
    say(f"  separation   how often a real firing outscores a mistake, over "
        "every pair")
    say(f"\n  Best: {best} — catches {results[best][0]:.0%} against "
        f"{now[0]:.0%}, fires on {results[best][2]:.1%} against {now[2]:.1%}, "
        f"separation {results[best][3]:.3f} against {now[3]:.3f}.")
    return results


if __name__ == "__main__":
    raise SystemExit(main())
