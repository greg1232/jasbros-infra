"""Claude Speaker — the whole program.

You talk to it, it talks back.

    Listen  ->  Wake word  ->  Record  ->  Transcribe  ->  Claude  ->  Speak
      ^                                                                  |
      +------------------------------------------------------------------+

Run it with:
    python src/main.py            listen for the wake word (or press Enter)
    python src/main.py --text     type questions instead of speaking them
"""

import os
import sys

import audio_in
import brain
import config
import dashboard
import enroll
import lights
import music
import rescue
import sounds
import stt
import wake_log
import timers
import tts
import wake
import weather


def run_voice_mode() -> None:
    """The real thing: talk to it, it talks back."""
    print("Starting up...")
    the_brain = brain.Brain()
    weather.start()  # Fetches in the background; never blocks a question.
    timers.start(tts.speak, tts.ring_once)  # Rings on its own thread.
    sounds.start()  # Watches the clock on anything left playing.
    dashboard.start()  # A page to look at, from a browser on this network.
    rescue.start()  # ...and an access point of its own if there isn't one.
    music.start()  # Notices if it stops being a Spotify speaker.
    stt.warm_up()  # Load the speech model now so the first question is fast.
    tts.warm_up()  # ...and the voice, so the first answer is too.
    waker = wake.make_waker()

    with audio_in.Microphone() as mic:
        mic.measure_noise_floor()  # Listen for a moment before saying ready.
        # Say how to stop *this* speaker. Ctrl-C is only an answer if
        # somebody is looking at a terminal; otherwise it is the service,
        # which is now the only other way it can have been started.
        stop = ("Press Ctrl-C to stop." if sys.stdout.isatty()
                else "Stop it with ./start.sh --stop")
        print(f"\nReady — {waker.label}. {stop}\n")
        lights.show("idle")

        while True:
            # 1. Wait to be woken up.
            if not waker.wait_for_wake(mic):
                break

            # If a timer is ringing, saying the wake word is how you stop
            # it. This has to come before the turn lock, not after: the
            # ringing thread is holding that lock, and waiting for it would
            # mean waiting out the alarm.
            if timers.hush():
                print("(hushed the alarm)")

            # Everything from here to the end of the answer is one turn. A
            # timer coming due in the middle waits for it: ringing over the
            # answer is rude, and ringing during step 3 puts a chime in the
            # middle of the recording, which Whisper duly transcribes as a
            # word in the question.
            # Anything playing in the background steps aside for the whole
            # turn, not just while the speaker talks — otherwise the
            # question is recorded over rain and Whisper has to guess
            # through it. It comes back afterwards, unless the question was
            # "stop", in which case there's nothing left to come back.
            try:
                one_turn(mic, waker, the_brain)
            except Exception as error:
                # One bad turn must not end the speaker. Before this, a
                # sound device that was busy for a moment raised out of
                # tts.speak, through run_voice_mode, and killed the
                # process — nine times in one afternoon, each time in the
                # middle of somebody using it.
                print(f"[turn failed] {type(error).__name__}: {error}")
                lights.show("idle")


def one_turn(mic, waker, the_brain) -> None:
    """Everything from the wake word to the end of the answer."""
    with timers.turn, sounds.paused():
        # Whatever happens in here — a question, a false wake,
        # a network error — the ring goes dark again on the way
        # out. A speaker left glowing blue looks like it is still
        # listening to you, which is the one thing it must not do
        # by mistake.
        try:
            # Write down what fired, before anything else touches
            # the detector. Whatever woke the speaker is exactly
            # the example worth learning from, and the expensive
            # part of making it is already paid for.
            firing = wake_log.note(waker, getattr(
                waker, "last_score", config.WAKE_THRESHOLD))
            # And whatever nearly fired just before it, which is
            # very likely the same person saying the same thing and
            # being missed the first time.
            wake_log.flush_near()

            # 2. Beep, and light the ring blue, so the person knows
            # it's listening. The ring is the half that keeps saying
            # so while they're actually talking.
            lights.show("listening")
            tts.beep()
            print("Listening...")

            # 3. Record until they stop talking. The cutoff is worked
            # out from the room as it sounded just now, not as it
            # sounded when the speaker was switched on — televisions
            # get turned on.
            audio = mic.record_until_silence()

            # 4. Turn the recording into words.
            lights.show("thinking")
            question = stt.transcribe(audio)
            if not question:
                # Nothing was said. With a television on, the wake word
                # fires by mistake dozens of times an hour, and this is
                # what almost all of those look like — so say nothing
                # rather than announcing the mistake to an empty room.
                print("(woke up, but nobody was talking)")
                wake_log.outcome(firing, "", False)
                dashboard.note("woke, nobody spoke",
                               score=getattr(waker, "last_score", 0))
                return
            print(f"You: {question}")

            # 5. Ask Claude, then say the answer out loud.
            answer = the_brain.ask(question)
            if not answer:
                if the_brain.did_something:
                    # A tool did the job and there is nothing to
                    # say — "I have spoken" stops the speaker and
                    # answering would defeat it. Somebody was
                    # plainly talking to us, so it counts as real.
                    print("(done, nothing to say)")
                    wake_log.outcome(firing, question, True)
                else:
                    # Somebody was talking, but not to us — the
                    # television. Claude spotted it; see
                    # brain.SILENCE.
                    print(f"(not for me: {question!r})")
                    wake_log.outcome(firing, question, False)
                    dashboard.note("not for me", said=question,
                                   score=getattr(waker, "last_score", 0))
                return
            wake_log.outcome(firing, question, True)
            dashboard.note("answered", said=question, answer=answer,
                           score=getattr(waker, "last_score", 0))
            print(f"Claude: {answer}\n")
            lights.show("speaking")
            tts.speak(answer)

            # "Learn my voice" arms a recording rather than doing
            # it inside the tool call, because the tool runs before
            # the answer is spoken — and the speaker has to finish
            # saying "off you go" before it can listen to them go.
            if enroll.armed():
                lights.show("listening")
                enroll.run(mic, waker, tts.speak)
        finally:
            lights.show("idle")


def run_text_mode() -> None:
    """Type questions instead of speaking them. Handy for testing."""
    the_brain = brain.Brain()
    timers.start(lambda words: (print(f"\n*** {words}"), tts.speak(words)),
                 tts.ring_once)
    print("Type a question (or press Enter to quit).\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break

        answer = the_brain.ask(question)
        if not answer:
            print("(Claude decided that wasn't meant for it.)\n")
            continue
        print(f"Claude: {answer}\n")
        tts.speak(answer)


def main() -> None:
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        return

    text_mode = "--text" in sys.argv

    try:
        if text_mode:
            run_text_mode()
        else:
            run_voice_mode()
    except KeyboardInterrupt:
        pass  # Ctrl-C is a normal way to stop.

    print("\nBye!")


if __name__ == "__main__":
    main()
