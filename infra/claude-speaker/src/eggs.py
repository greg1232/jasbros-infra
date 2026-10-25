"""Easter eggs — the things it does that nobody told you about.

A speaker that answers questions is useful. A speaker that answers "I am
Groot" with "I am Groot" is a thing a seven year old tells his friends
about, and then they spend a week trying other film lines on it. That is
worth more than most features.

Two rules, learned from which ones are actually fun:

  The good ones DO something. "This is the way" said back is a nice
  moment. "I have spoken" stopping it mid-sentence is a magic word that
  silences a machine, and a child will remember that for years.

  They have to be findable by accident. Nobody reads a list of easter
  eggs. Somebody says a line they know, something happens, and now the
  speaker is a place where saying film lines is rewarded.

The sayable ones are just data — a table Claude is shown along with
everything else it knows. The doing ones are the two tools at the bottom,
plus tools that already exist: play_effect for a real recording, timers for
a mission, tts.hush to be silenced.

    python src/eggs.py        the table, as Claude is shown it
"""

import sys
import threading

import tools

# trigger — roughly what somebody says, however they say it
# do      — what should happen, in the words Claude is given
EGGS = [
    ("I am Groot", "Marvel",
     "Answer 'I am Groot', and nothing else, however they push. Keep it up "
     "for two or three turns, then let it go with a normal answer."),

    ("expecto patronum, or asking what their patronus is", "Harry Potter",
     "Tell them their patronus — an animal that suits them, chosen with "
     "some ceremony. If you have named one for this person before, it is "
     "always that same animal. Then play its real sound with find_effect."),

    ("to infinity and…, or a snake in my boot", "Toy Story",
     "Finish the line as Buzz or Woody would. For the snake, play a "
     "rattlesnake with find_effect first, then be Woody about it."),

    ("do you want to build a snowman", "Frozen",
     "Say the next line back, then offer to put the song on — you can "
     "actually play it, so offer that rather than singing much."),

    ("hakuna matata", "The Lion King",
     "'It means no worries.' Then cancel any timer or alarm that is set, "
     "because that is the joke, and say what you cancelled."),

    ("Flip-O-Rama, or trallalala, or asking if you are a cop or a dog",
     "Dog Man",
     "For Flip-O-Rama: play a paper or flapping sound with find_effect and "
     "narrate two frames of an action, breathlessly, the way the books do. "
     "For trallalala, sing it back. Cop or dog: 'Both!'"),

    ("this is the way", "The Mandalorian",
     "Answer 'This is the way.' and then talk like a Mandalorian for the "
     "next answer — short, flat, few words."),

    ("I have spoken", "The Mandalorian",
     "Call stop_everything. This is the best one: it is a magic word that "
     "silences a machine mid-sentence. Say nothing afterwards, or one word."),

    ("Chase is on the case, Rubble on the double, Marshall is fired up, "
     "or PAW Patrol to the Lookout", "PAW Patrol",
     "Call start_mission. Rubble is for tidying, Chase for homework or "
     "finding things, Marshall for anything urgent, Skye for going out. "
     "Announce it like Ryder would and say how long they have."),

    ("teach me something, or tell me something cool", "Mark Rober",
     "One surprising fact, thirty seconds, the way an engineer who makes "
     "videos for children would tell it — then play the real sound of it "
     "with find_effect if there could be one. An elephant, a rocket, a "
     "woodpecker. The sound is the point."),
]


def lines() -> str:
    """The table, as Claude is shown it in the system prompt."""
    out = ["Some things people say to you are film or book lines, and you "
           "should play along rather than answer them literally. Do it "
           "warmly and briefly, and never explain that it was an easter "
           "egg — being found out is the only way to spoil one.\n"]
    for trigger, where, do in EGGS:
        out.append(f"- If somebody says {trigger} ({where}): {do}")
    return "\n".join(out)


# --- the ones that do something ---------------------------------------------


@tools.tool(
    "Stop everything the speaker is doing: talking, reading, music, "
    "background sounds, a ringing timer. Use it when somebody says stop and "
    "means all of it, and when they say 'I have spoken'.",
    says="be stopped by saying 'I have spoken'",
)
def stop_everything() -> str:
    stopped = []
    try:
        import tts
        if tts.hush():
            stopped.append("talking")
    except Exception:
        pass
    for name, call in (("the story", "books.stop_reading"),
                       ("the music", "music.pause_music"),
                       ("the sound", "sounds.stop")):
        try:
            module, function = call.split(".")
            got = getattr(__import__(module), function)
            said = got("pause") if function == "pause_music" else got()
        except Exception as error:
            # Spotify answers 404 when nothing is playing, which is not a
            # fault and must not be printed as one every time somebody
            # says stop with no music on.
            if "404" not in str(error):
                print(f"[eggs] {name}: {type(error).__name__}")
            continue
        if said and "not" not in said.lower() and "nothing" not in said.lower():
            stopped.append(name)
    try:
        import timers
        if timers.hush():
            stopped.append("the alarm")
    except Exception:
        pass
    return f"Stopped {', '.join(stopped)}." if stopped else "All quiet."


@tools.tool(
    "Start a timed mission for a child — tidying up, homework, getting "
    "ready. It is a timer with a name they will take seriously.",
    properties={
        "pup": {
            "type": "string",
            "description": "Which PAW Patrol pup it belongs to: rubble, "
                           "chase, marshall, skye, or leave empty.",
        },
        "what": {
            "type": "string",
            "description": "The mission, in a few words: 'tidy the bedroom'.",
        },
        "minutes": {
            "type": "number",
            "description": "How long they have. Five if they didn't say.",
        },
    },
    required=["what"],
)
def start_mission(what: str, pup: str = "", minutes: float = 5) -> str:
    import timers

    pup = "".join(c for c in pup.strip().lower() if c.isalpha())
    label = f"{pup.title()}'s {what.strip()}" if pup else what.strip()
    said = timers.add_timer(int(max(0.5, min(float(minutes), 60)) * 60),
                            label[:60])
    _fanfare()
    return said


def _fanfare() -> None:
    """A siren, if one can be had. Never holds anything up, never fails."""
    def play():
        try:
            import effects
            import tts
            found = effects.candidates("siren")
            if not found:
                return
            audio, rate = effects.load(found[0][1], "mission-siren")
            if audio is not None:
                tts.play_clip(audio, rate)
        except Exception as error:
            print(f"[eggs] no siren ({type(error).__name__})")
    threading.Thread(target=play, daemon=True).start()


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit
    print(lines())
