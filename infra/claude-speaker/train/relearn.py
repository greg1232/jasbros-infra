"""Retrain the wake word on its own mistakes, in about a second.

The expensive part of training is turning audio into features: 159 ms per
two second window on a Pi, twenty minutes for the whole set. Fitting the
model on those features is nothing — measured on a Pi 4, a logistic
regression on 30,000 examples of 768 numbers takes 0.7 seconds.

So this never touches audio. It joins two piles of features that already
exist:

  the bank    everything train_whisper_wake.py embedded, saved beside the
              model as hey_claude_whisper.bank.npz.
  the log     every firing since, with the vector the detector scored it
              on. Those cost nothing at all — the encoder pass that made
              them is what fired the wake word in the first place.

Which is why this can run on the Pi itself, overnight, on data the Pi
collected, without a laptop being involved.

Label the log first:

    python train/label_wakes.py
    python train/relearn.py

    --dry     say what would change, write nothing
    --weight  how much to count a logged example against a bank one
"""

import argparse
import contextlib
from datetime import datetime
import fcntl
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import wake_log  # noqa: E402

def _setting(name: str, fallback: str) -> str:
    """One value from .env or the environment, without importing config."""
    import os
    if os.environ.get(name, "").strip():
        return os.environ[name].strip()
    env = HERE.parent / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or fallback
    return fallback


MODEL = HERE.parent / "models" / "hey_claude_whisper.npz"

# Below this many labelled firings there is nothing to hold back, so the
# new model is taken on trust — it can only be the bank plus a handful.
LEAST_TO_JUDGE = 25

# How many ways the test set is split, so that every row in it is
# scored by a model that was fitted without it.
FOLDS = 5
# And how many of those have to be the wake word before a recall figure
# means anything. With one, recall is 0% or 100% and nothing between.
ENOUGH_POSITIVES = 8
# How much worse a new model may be and still be taken, since these are
# small samples and being the same within noise is not a reason to refuse.
SLACK = 0.05
# How much more a label from somebody who listened to the clip is worth
# than one a rule guessed at.
BY_PERSON = 5.0

# How hard to hold the fit back. Small numbers mean more regularisation.
#
# This was 0.1, inherited from the trainer that built the bank, and it was
# the single biggest thing wrong with the recipe. Measured by five-fold
# cross-validation over the log — see train/evaluate.py — at the strictest
# threshold that still fires on no more than a tenth of known mistakes:
#
#     C=1.0    catches 75%, and 62% of the ones said to it
#     C=0.1    catches 85%, and 79%          (what it was)
#     C=0.01   catches 95%, and 90%
#     C=0.001  catches 99%, and 97%          (what it is)
#
# Seven hundred and sixty eight features against a few thousand rows is a
# lot of room to overfit, and it was taking all of it. Weighting the human
# labels 1x, 5x or 20x moved nothing by comparison, and a small neural net
# in place of the regression matched C=0.001 without beating it.
FIT_HELD_BACK = float(_setting("WAKE_FIT_C", "0.001"))

# The thresholds worth considering, and how much firing on the room the
# best of them may cost. A wake word that never fires is not a wake word,
# so this buys recall with false wakes up to a point and then stops.
THRESHOLDS = [round(x, 3) for x in np.arange(0.30, 0.999, 0.005)]
# How much firing on the room the best threshold may cost, as a fraction of
# the mistakes a person has labelled. This is the one number here that is a
# judgement rather than a measurement — how much television is worth how
# much of a child being heard — so it is a setting, and the sweep prints
# the whole curve beside its choice so the judgement can be checked.
#
# Five per cent. It was fifteen, on the argument that a false wake is
# nearly silent — nothing is said, nothing is answered, it costs a flash
# of the ring. That argument was made without measuring how often it
# happens. Sixteen firings an hour in this room with the television on,
# fourteen of them with nobody talking: a budget is a rate times an
# exposure, and the exposure turned out to be large.
#
# Measured leak-free on 443 hand labels, what each budget buys:
#     15%   line 0.400, catches 97%, fires on 14.8%
#      5%   line 0.630, catches 91%, fires on  4.4%
#      2%   line 0.780, catches 80%, fires on  1.8%
# Five keeps nine tenths of the recall for a third of the mistakes.
FALSE_BUDGET = float(_setting("WAKE_FALSE_BUDGET", "0.05"))

# What the sweep settled on for the model just fitted, so it can be saved
# with it.
_chosen = 0.0


def _best_threshold(p, real):
    """The threshold that catches most without firing on too much.

    Returns (threshold, recall, false rate).

    When no threshold meets the budget this used to fall back to the one
    that fired least, which is the strictest one, where a wake word
    catches nothing at all. That is how a retraining came back saying "I
    now catch 0% of the ones I have on file" — not a model that had
    learned nothing, a fallback that had chosen silence. It balances the
    two now, which at worst gives something usable.
    """
    best = None
    for line in THRESHOLDS:
        caught = float((p[real] >= line).mean()) if real.any() else 0.0
        fired = float((p[~real] >= line).mean()) if (~real).any() else 0.0
        within = fired <= FALSE_BUDGET
        rank = (within, caught if within else caught - 2 * fired)
        if best is None or rank > best[0]:
            best = (rank, line, caught, fired)
    return best[1], best[2], best[3]
# What this machine learns for itself goes in state/, never in models/.
# models/ is mirrored by deploy, so a model written there would be replaced
# by the shipped one on the next deploy, taking every night of learning
# with it. src/whisper_wake.py looks in state/ first.
LEARNED = HERE.parent / "state" / "hey_claude_whisper.npz"
LOCK = HERE.parent / "state" / "relearn.lock"




def load_bank(path: Path):
    if not path.exists():
        raise SystemExit(
            f"No feature bank at {path}.\n"
            "It's written by train/train_whisper_wake.py — run that once on "
            "a machine with the recordings, and deploy will carry it over.")
    saved = np.load(path, allow_pickle=True)
    return (saved["X"].astype(np.float32), saved["y"].astype(int),
            saved["who"].astype(str))


# Labels carrying this text came from a rule that kept every near miss in
# the fifteen seconds before a firing and called them all repetitions. In
# a room with a television on that is one real wake word manufacturing
# eight positives out of nothing, and it produced sixty-three per cent of
# the training data. The rule is fixed (src/wake_log.py); its output is
# still in the log, and is ignored here rather than deleted, because the
# log is append-only and the archive keeps everything.
DISCREDITED = "said again seconds later"


def load_log():
    """The labelled firings: vectors, labels, and who said so."""
    firings = wake_log.read()
    vectors = wake_log.vectors()

    X, y, kind, known = [], [], [], []
    thrown = 0
    for firing in firings:
        number = firing["n"]
        if "label" not in firing or not firing.get("has_vector"):
            continue
        if number >= len(vectors):
            continue
        if firing.get("by") != "person" and \
                DISCREDITED in firing.get("why", ""):
            thrown += 1
            continue
        X.append(vectors[number])
        y.append(int(firing["label"]))
        kind.append(_kind(firing))
        # When this example became *known*, which is the later of when the
        # speaker fired and when somebody said what the firing was. The
        # difference is the whole point: an afternoon spent labelling a
        # fortnight of old clips adds no rows at all, and it is the most
        # valuable thing that happens to this model. Judged on the firing
        # time alone, the gate sees nothing new and ends up comparing the
        # model with itself.
        known.append(max(firing.get("at", ""),
                         firing.get("labelled_at", "")))
    if thrown:
        print(f"  ignored {thrown} labels from the old repetition rule")
    if not X:
        return (np.zeros((0, wake_log.WIDTH), dtype=np.float32),
                np.zeros(0, dtype=int), np.zeros(0, dtype="<U8"),
                np.zeros(0, dtype="<U32"))
    return (np.array(X, dtype=np.float32), np.array(y), np.array(kind),
            np.array(known, dtype="<U32"))


def load_the_others(say=print):
    """Every other speaker's labelled firings, from the shared archive.

    Each speaker fits on the shipped bank plus its own log, which means a
    new one starts knowing nothing about this house and takes weeks to
    catch up, while everything the others have learned sits unused. The
    bottleneck this project has always had is too few confirmed positives
    — one speaker had 712 human labels and the next had none.

    So the training set is the union. The *test* set is not: rows from
    elsewhere come back marked `pooled`, which keeps them out of `human`
    and so out of the held-out slice and the threshold sweep. A bedroom at
    night and a kitchen with a television have different false-wake
    economics, and the operating point has to be chosen on the room it
    will run in.

    Costs one download per speaker per night, cached by the hub, and
    vectors.f16 is about ten megabytes each.
    """
    if _setting("POOL", "on").lower() in ("off", "0", "false", "no"):
        return None
    try:
        sys.path.insert(0, str(HERE))
        import archive
        from huggingface_hub import HfApi, hf_hub_download
        if not archive.token():
            return None
        api = HfApi(token=archive.token())
        repo = f"{api.whoami()['name']}/{archive.REPO}"
        mine = archive.where()
        files = set(api.list_repo_files(repo, repo_type="dataset"))
    except Exception as error:
        say(f"  not pooling ({type(error).__name__}: {error})")
        return None

    others = sorted({f.split("/")[0] for f in files
                     if f.endswith("/wakes/wakes.jsonl")} - {mine})
    if not others:
        return None

    X, y, kind = [], [], []
    for who in others:
        try:
            index = hf_hub_download(repo, f"{who}/wakes/wakes.jsonl",
                                    repo_type="dataset",
                                    token=archive.token())
            raw = hf_hub_download(repo, f"{who}/wakes/vectors.f16",
                                  repo_type="dataset",
                                  token=archive.token())
            vectors = np.fromfile(raw, dtype=np.float16)
            vectors = vectors[:len(vectors) // wake_log.WIDTH
                              * wake_log.WIDTH].reshape(-1, wake_log.WIDTH)
        except Exception as error:
            say(f"  couldn't read {who} ({type(error).__name__})")
            continue

        rows = {}
        for line in open(index):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if "n" in row:
                rows.setdefault(row["n"], {}).update(row)

        took = 0
        for number, row in sorted(rows.items()):
            if "label" not in row or number >= len(vectors):
                continue
            if row.get("by") != "person" and DISCREDITED in row.get("why", ""):
                continue
            X.append(vectors[number].astype(np.float32))
            y.append(int(row["label"]))
            # Keep the distinction between a person's answer and a rule's
            # guess — it decides the weight — but mark both as pooled so
            # neither can end up being tested on.
            kind.append("pooled-person"
                        if row.get("by") == "person" or row.get("taught")
                        else "pooled")
            took += 1
        say(f"  pooled {took} from {who}")

    if not X:
        return None
    return (np.array(X, dtype=np.float32), np.array(y),
            np.array(kind, dtype="<U16"))


def _kind(firing: dict) -> str:
    """Where a label came from, which is how much it can be trusted.

      person    somebody listened to the clip and said what it was
      enrolled  somebody said the wake word into it on purpose, so it is
                a positive by construction — as certain as a person
                listening, and a different distribution: deliberate,
                clear, close, and never the television
      moved     the same enrolment recordings slid around the window to
                make more of them. Fine to train on, never to test on:
                scoring a model on variants of its own training data is
                marking its own homework
      machine   a rule guessed from what happened next
    """
    if firing.get("by") == "person":
        return "person"
    if firing.get("taught"):
        return "moved" if ":moved" in firing.get("why", "") else "enrolled"
    return "machine"


def _fit_on(bank_X, bank_y, log_X, log_y, log_kind, weight, rows):
    """The same recipe as refit(), on a chosen slice of the log.

    Both sides of the comparison in _worth_having go through here, which
    is the whole point of it: two models are only comparable if they were
    made the same way and asked the same question.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    X = np.vstack([bank_X, log_X[rows]])
    y = np.r_[bank_y, log_y[rows]]
    w = np.r_[np.ones(len(bank_X)),
              np.where(np.isin(log_kind[rows],
                               ("person", "enrolled", "pooled-person")),
                       weight * BY_PERSON, weight)]
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(max_iter=5000, C=FIT_HELD_BACK,
                             class_weight="balanced")
    clf.fit(scaler.transform(X), y, sample_weight=w)
    return (scaler.mean_, scaler.scale_, clf.coef_[0],
            float(clf.intercept_[0]))


def _out_of_fold(bank_X, bank_y, log_X, log_y, log_kind, weight, held,
                 only=None, folds=FOLDS):
    """Score each held row with a model fitted without it.

    `only` narrows what may be trained on — how the running model is
    rebuilt from the data it actually had. Returns one probability per
    held row, in order, or None if some fold had nothing to learn from.
    """
    rows = np.where(held)[0]
    if len(rows) < folds:
        return None
    order = np.random.default_rng(0).permutation(len(rows))
    scored = np.zeros(len(log_X))
    for fold in range(folds):
        learn_from = np.ones(len(log_X), bool)
        learn_from[rows[order[fold::folds]]] = False
        if only is not None:
            learn_from &= only
        if int(learn_from.sum()) < LEAST_TO_JUDGE or \
                len(set(log_y[learn_from])) < 2:
            return None
        tested = rows[order[fold::folds]]
        scored[tested] = _probability(
            log_X[tested],
            *_fit_on(bank_X, bank_y, log_X, log_y, log_kind, weight,
                     learn_from))
    return scored[held]


def _worth_having(bank_X, bank_y, log_X, log_y, log_kind, log_known, weight,
                  old, say):
    """Would the new model be better, on firings it has not been shown?

    Comparing before and after on the very examples just fitted says only
    that the fit converged. It said 100% and 0% the first night, and the
    room went on waking the speaker every twenty seconds — because a model
    can memorise thirty-four clips of one evening's television and learn
    nothing about television.

    So a fifth of the logged firings are held back, the model is fitted
    without them, and both models are asked about them. That is the only
    number here that can say no.
    """
    if len(log_X) < LEAST_TO_JUDGE:
        say(f"  only {len(log_X)} labelled — too few to judge, so taking it")
        return True, float("nan")

    # Judge on the labels a person listened to, and only those.
    #
    # A random slice of everything was measuring agreement with the
    # machine's own guesses, which is not the same question and is why the
    # numbers swung between runs — the sample was redrawn each time from a
    # pool whose labels kept changing. Somebody who sat and listened to a
    # clip is the only ground truth here, and it is worth more as a test
    # than as three hundred and sixty five thousandths of a training set.
    #
    # It is also stable. The set only grows, as more get labelled, so two
    # runs a week apart can be compared.
    # Somebody who sat and listened is the only ground truth here, so it
    # is used for whichever half of the judgement it can answer.
    #
    # In practice one half. Labelling a day of a television room produces
    # almost entirely "no" — 142 of the first 143 — which measures false
    # wakes beautifully and says nothing at all about recall. So human
    # labels are used for the classes they cover, and machine labels fill
    # in the class they don't, with the report saying which was which.
    # What a person can vouch for, one way or another.
    #
    # Two kinds. Somebody listened to a clip and said what it was, and
    # somebody said the wake word into the speaker on purpose during
    # enrolment — the second is a positive by construction and every bit
    # as certain, which is why it belongs here and not only in the
    # training set. What does not belong is the augmented copies of those
    # recordings: scoring a model on variants of its own training data is
    # marking its own homework.
    #
    # They are held out of the fitting below, so this is a test and not a
    # memory check. And they are reported apart, because enrolment is
    # deliberate, clear and close, and spontaneous use is not — if recall
    # on one is far above the other, that difference is the interesting
    # thing and should not be averaged away.
    human = np.isin(log_kind, ("person", "enrolled"))

    # And only what the running model has not already seen.
    #
    # Otherwise the comparison is rigged in its favour: the new model is
    # judged on data held out of its fitting, while the old one is judged
    # on data it was fitted on. That looked like the running model
    # catching 100% of everything at a threshold of 0.3, which is not
    # skill, it is memory. Models carry the time they were fitted, so
    # anything learned since is fair to both.
    #
    # "Learned since", not "fired since". A run once reported before and
    # after agreeing to three decimal places — because nothing had been
    # logged since the model was fitted, so 'before' was rebuilt from
    # every row there was and the gate compared the model with itself.
    # What had changed that day was sixty labels on old clips. Rows are
    # not the unit of new information here; answers are.
    since = str(old["fitted_at"]) if "fitted_at" in old.files else ""
    # Before `human` is narrowed below, keep the whole of it. Judging a new
    # model and choosing its operating point are different questions and
    # want different data: the first wants rows neither model has seen, the
    # second wants as many as can be had.
    everyone = human.copy()
    fresh = human & (log_known > since) if since else human
    seen_by_old = bool(since)
    if int(fresh.sum()) >= LEAST_TO_JUDGE and len(set(log_y[fresh])) > 1:
        human = fresh
        seen_by_old = False
        say(f"  judging on the {int(human.sum())} learned since the running "
            f"model was fitted, which neither has seen")
    elif since:
        say(f"  only {int(fresh.sum())} learned since the running model was "
            "fitted, so judging on everything a person can vouch for — "
            "which the running model was fitted on")

    classes = set(log_y[human]) if human.any() else set()
    held = human.copy()
    borrowed = 0

    rng = np.random.default_rng(0)
    for missing in (0, 1):
        if missing in classes or human.sum() < LEAST_TO_JUDGE:
            continue
        could = (~human) & (log_kind != "moved") & (log_y == missing)
        if not could.any():
            continue
        # A fifth of them, the same fifth every time, so two runs can be
        # compared.
        take = could & (rng.random(len(log_X)) < 0.2)
        held |= take
        borrowed += int(take.sum())

    if held.sum() < LEAST_TO_JUDGE or len(set(log_y[held])) < 2:
        say(f"  only {int(human.sum())} a person can vouch for — not enough "
            "to judge on, so falling back to a random slice")
        held = rng.random(len(log_X)) < 0.2
        on = "firings held back from the fitting"
    elif borrowed:
        say(f"  judging on {int(human.sum())} a person can vouch for, plus "
            f"{borrowed} the machine labelled to cover the other answer")
        on = "firings"
    else:
        on = "firings a person can vouch for"

    if not held.any() or held.all() or len(set(log_y[~held])) < 2:
        return True, float("nan")

    # Every held row scored by a model that was not fitted on it, in
    # folds, rather than by one model with the whole test set held out.
    #
    # The difference matters because the human labels are both the best
    # training data and the only trustworthy test. Hold all of them out
    # at once and the model being judged is one that has never seen a
    # human label — not the model that ships, and sitting on a different
    # scale, so the threshold chosen from it does not fit the one it gets
    # saved beside. In folds, each model has four fifths of them, which
    # is near enough the real thing: 86% caught here against 74% for the
    # deprived version.
    after = _out_of_fold(bank_X, bank_y, log_X, log_y, log_kind, weight,
                         held)

    # And what to compare it against.
    #
    # The weights that are running are the honest answer only when the
    # rows being judged are ones they have never seen. Most nights they
    # are not: a model is fitted on everything labelled so far and then
    # judged before much else has been logged, so scoring those weights
    # on the test set is a memory test, and memory wins. It reported 96%
    # caught and 1% fired on this speaker's hand labels where the same
    # recipe, honestly scored, says 86% and 4% — and no challenger ever
    # got past it, which is a learning loop that cannot learn.
    #
    # So when the incumbent has seen the test, it is rebuilt rather than
    # loaded: the same recipe, the same folds, but fitted only on the log
    # as it stood when the running model was fitted. That asks the
    # question this gate is actually for — is the new data worth having —
    # and asks it of both sides identically.
    before = None
    if seen_by_old:
        earlier = log_known <= since
        if int((earlier & ~held).sum()) >= LEAST_TO_JUDGE:
            before = _out_of_fold(bank_X, bank_y, log_X, log_y, log_kind,
                                  weight, held, only=earlier)
        if before is None:
            say("  and too little of its data is left to rebuild it "
                "from, so this\n  comparison still flatters it — --force "
                "to override")
        else:
            say(f"  so 'before' is rebuilt from the "
                f"{int((earlier & ~held).sum())} it had, and both are "
                "scored only on\n  rows they were not fitted on")
    if before is None:
        before = _probability(log_X[held], old["mean"], old["scale"],
                              old["coef"], float(old["intercept"]))

    real = log_y[held] == 1
    scores = {"before": before, "after": after}
    # Each model at its own best threshold, not both at the same one.
    #
    # The threshold is a setting, not a property of the model, so holding
    # it fixed asks the wrong question: two models can sit on quite
    # different score distributions, and one that looks far worse at 0.95
    # may be better everywhere once allowed its own operating point. A
    # refit came out at 47% recall against 87% and was refused on that
    # basis, which was a comparison of two settings rather than of two
    # models.
    got = {}
    positives = int(real.sum())
    say(f"  on {int(held.sum())} {on}:")
    if positives < ENOUGH_POSITIVES:
        # A recall percentage from one clip is 0% or 100% and nothing
        # else. Saying so is the difference between a number and a
        # measurement, and this said "I now catch 0%" off a single clip
        # that happened to score low.
        say(f"    only {positives} of them are the wake word, so the recall "
            "figures below are\n    barely a measurement — label a few "
            "more with ./label.sh")
    for name, p in scores.items():
        line, caught, fired = _best_threshold(p, real)
        got[name] = (caught, fired)
        spoken = (log_kind[held] == "person") & real
        taught = (log_kind[held] == "enrolled") & real
        apart = ""
        if spoken.any() and taught.any():
            apart = (f"  ({(p[spoken] >= line).mean():.0%} of the ones said "
                     f"to it, {(p[taught] >= line).mean():.0%} of the ones "
                     "taught to it)")
        say(f"    {name:6s} at {line:.3f}: catches {caught:5.0%} of the real "
            f"ones, and still fires on {fired:5.1%} of the mistakes")
        if apart:
            say(f"           {apart.strip()}")

    # And the shape of the choice, because the budget above is a judgement
    # about how much television is worth how much of a child being heard,
    # and it should not be made silently inside a constant.
    say("    the new one across the range:")
    for line in (0.5, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995):
        p = scores["after"]
        caught = float((p[real] >= line).mean()) if real.any() else 0.0
        fired = float((p[~real] >= line).mean()) if (~real).any() else 0.0
        say(f"      {line:5.3f}  catches {caught:5.0%}  fires on {fired:5.1%}")

    # Where the line actually goes.
    #
    # Not from the rows above. Judging wants data neither model has seen,
    # which on a good night is a few dozen firings — and a few dozen is not
    # enough to place a threshold on. One clip was 11% of the mistakes in
    # that slice, and it moved the line from 0.800 to 0.825; the same code
    # could as easily have landed on 0.95, where being called across a room
    # is heard half the time. The sweep reported 0.0% fired on at 0.825,
    # and the honest figure over everything a person has vouched for was
    # 6.6%.
    #
    # So the line is chosen out-of-fold over every human label there is,
    # which costs one more set of folds — a few seconds, once a night —
    # and is the same machinery train/evaluate.py uses to measure it.
    global _chosen
    _chosen = _best_threshold(scores["after"], real)[0]
    wide = (_out_of_fold(bank_X, bank_y, log_X, log_y, log_kind, weight,
                         everyone)
            if int(everyone.sum()) > int(held.sum()) else None)
    if wide is not None and len(set(log_y[everyone])) > 1:
        wide_real = log_y[everyone] == 1
        line, caught, fired = _best_threshold(wide, wide_real)
        _chosen = line
        say(f"    the line, chosen on all {int(everyone.sum())} a person has "
            f"vouched for rather than the {int(held.sum())} above: "
            f"{line:.3f}, catching {caught:.0%} and firing on {fired:.1%}")
    else:
        say(f"    the line stays on the {int(held.sum())} above — there is "
            "nothing wider to use")

    was, now = got["before"], got["after"]
    # Room to move, because these are small samples and a model that is
    # the same within noise is worth taking for the fresh data in it.
    better = now[0] >= was[0] - SLACK and now[1] <= was[1] + SLACK
    return better, now[0]


def refit(model: Path = MODEL, weight: float = 3.0, dry: bool = False,
          say=print, only_if_better: bool = False) -> str:
    """Fit a new model from the bank plus the log. Returns one line to speak.

    Called both from the command line and from src/enroll.py, which does
    this live while somebody stands there — so it prints its working
    through `say` and hands back a sentence rather than a report.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    bank_X, bank_y, who = load_bank(model.with_suffix(".bank.npz"))
    # Compare against whatever is actually running, which is the learned
    # one if this machine has already learned something.
    running = LEARNED if LEARNED.exists() else model
    log_X, log_y, log_kind, log_known = load_log()

    # Everything the other speakers have learned, added to what this one
    # can be trained on but never to what it is judged on.
    shared = load_the_others(say)
    if shared is not None:
        more_X, more_y, more_kind = shared
        log_X = np.vstack([log_X, more_X]) if len(log_X) else more_X
        log_y = np.r_[log_y, more_y]
        log_kind = np.r_[log_kind.astype("<U16"), more_kind]
        # Pooled rows have no local history, and `known` only ever decides
        # which of *our* rows count as fresh. Empty sorts before any real
        # timestamp, so they are never mistaken for new.
        log_known = np.r_[log_known.astype("<U32"),
                          np.array([""] * len(more_y), dtype="<U32")]

    say(f"  bank: {len(bank_X)} features "
          f"({int((bank_y == 1).sum())} wake word, "
          f"{int((bank_y == 0).sum())} not)")
    say(f"  log:  {len(log_X)} labelled firings "
        f"({int((log_y == 1).sum())} real, "
        f"{int((log_y == 0).sum())} mistakes, "
        f"{int((log_kind == 'person').sum())} of them by a person, "
        f"{int((log_kind == 'enrolled').sum())} taught on purpose)")

    if not len(log_X):
        return "There's nothing labelled to learn from yet."

    X = np.vstack([bank_X, log_X])
    y = np.r_[bank_y, log_y]
    # Every logged example is from this room, this microphone, these
    # voices, and this television. The bank is a general model of the
    # world; the log is the actual problem. Counting the log for more is
    # the whole reason this is worth doing.
    # A person who listened to a clip is worth more than a rule that
    # guessed from what happened next, and there are far fewer of them —
    # a hundred and forty three human labels against two thousand
    # machine ones is a vote they lose on volume alone.
    logged = np.where(np.isin(log_kind, ("person", "enrolled")),
                      weight * BY_PERSON, weight)
    weights = np.r_[np.ones(len(bank_X)), logged]

    import time
    began = time.monotonic()
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(max_iter=5000, C=FIT_HELD_BACK, class_weight="balanced")
    clf.fit(scaler.transform(X), y, sample_weight=weights)
    say(f"  fitted {len(X)} x {X.shape[1]} in "
        f"{time.monotonic() - began:.2f}s")

    # Commit the data before fitting, so the sha names exactly what this
    # model is about to be trained on — not whatever the log looked like
    # by the time the fitting finished.
    import archive
    dataset = archive.snapshot(say)

    old = np.load(running)
    better, caught_after = _worth_having(
        bank_X, bank_y, log_X, log_y, log_kind, log_known, weight, old, say)

    if dry:
        say("  --dry, so nothing written.")
        return "Nothing written."

    if only_if_better and not better:
        say("  not an improvement — keeping the model that's running.")
        return "I had a look and the one I have is still better."

    # Keep the one that is being replaced, so a bad night can be undone.
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    backup = LEARNED.with_suffix(".npz.previous")
    if LEARNED.exists():
        backup.write_bytes(LEARNED.read_bytes())

    np.savez(LEARNED,
             mean=scaler.mean_.astype(np.float32),
             scale=scaler.scale_.astype(np.float32),
             coef=clf.coef_[0].astype(np.float32),
             intercept=np.float32(clf.intercept_[0]),
             whisper_model=str(old["whisper_model"]),
             window_seconds=np.float32(old["window_seconds"]),
             keep_frames=np.int32(old["keep_frames"]),
             # Which version of the data this came from, so the model can
             # always answer what it was trained on.
             dataset=dataset,
             fitted_at=datetime.now().astimezone().isoformat(
                 timespec="seconds"),
             examples=np.int32(len(log_X)),
             # The operating point the sweep chose for this model. Two
             # models are not comparable at one threshold, so each carries
             # its own — see src/whisper_wake.py.
             threshold=np.float32(_chosen or 0.0))
    say(f"  wrote {LEARNED}")
    archive.keep_model(LEARNED, dataset, {"recall": caught_after}, say)
    return (f"Learned from {len(log_X)} examples. "
            f"I now catch {caught_after:.0%} of the ones I have on file.")


def nightly(say=print) -> int:
    """Label whatever the day produced, refit, and keep it only if better.

    Run by a systemd timer on the Pi at four in the morning. Everything it
    needs is already on the machine: the vectors were computed when the
    wake word fired, and the labels come from what happened next.

    Labelling here never calls Claude. The free signals do most of the
    work — a firing followed by silence is a mistake, one followed by a
    question that got answered is real — and a nightly job that needs the
    network and costs money is a nightly job that fails quietly for a
    month.
    """
    import subprocess
    import sys as _sys

    with _only_one():
        say("Labelling what today produced...")
        # Not captured. This is the slow part — on a Pi it is seconds per
        # clip — and hiding its output made a job that was working look
        # exactly like a job that had hung.
        #
        # And no wake windows: transcribing them is the whole cost, and
        # measured against eighty real recordings tiny.en writes down the
        # wake word about one time in six. What came next does the work.
        done = subprocess.run(
            [_sys.executable, "-u", str(HERE / "label_wakes.py"),
             "--no-claude", "--windows", "0"])
        if done.returncode != 0:
            say(f"  labelling gave up ({done.returncode})")

        say("\nRefitting...")
        said = refit(say=say, only_if_better=True)
        say("\n" + said)

        if "still better" in said or "othing" in said:
            return 0

        # Only worth restarting if something was actually written.
        say("\nRestarting the speaker to pick it up...")
        subprocess.run(["systemctl", "--user", "restart", "claude-speaker"],
                       capture_output=True)
    return 0


@contextlib.contextmanager
def _only_one():
    """Refuse to run twice at once.

    Two of these overlapped and spent twenty minutes fighting each other
    and the speaker for the processor, which on four cores is felt. The
    timer fires nightly and a person can ask for it at any moment, so
    overlapping is ordinary rather than exotic.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Already learning — leaving that one to it.")
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--dry", action="store_true",
                        help="report, write nothing")
    parser.add_argument("--weight", type=float, default=3.0,
                        help="how much one logged example counts against "
                             "one from the bank. Above 1 because the log is "
                             "this room, and the bank is everywhere")
    parser.add_argument("--nightly", action="store_true",
                        help="label, refit, keep only if better, restart. "
                             "What the timer runs at four in the morning")
    parser.add_argument("--only-if-better", action="store_true",
                        help="keep the running model unless the new one "
                             "wins on firings held back from the fitting")
    args = parser.parse_args()

    if args.nightly:
        return nightly()

    print(refit(args.model, args.weight, args.dry,
                only_if_better=args.only_if_better))
    if not args.dry:
        print("  restart the speaker to pick it up:  "
              "systemctl --user restart claude-speaker")
    return 0


def _probability(X, mean, scale, coef, intercept):
    return 1.0 / (1.0 + np.exp(-(((X - mean) / scale) @ coef + intercept)))


if __name__ == "__main__":
    raise SystemExit(main())
