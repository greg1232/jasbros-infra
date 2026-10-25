"""The brain — sends the question to Claude and gets the answer back.

This is the only part that talks to the internet. Everything else (the
microphone, the speech recognition, the voice) runs on the laptop.
"""

import re

from datetime import datetime
from pathlib import Path

import anthropic

import sys

import config
import tools
import weather

# This tells Claude it's a speaker, not a chat window. It matters a lot:
# without it Claude writes bullet lists and headings, which sound terrible
# when a robot voice reads them out loud.
SYSTEM_PROMPT = """You are the voice of a small speaker that sits on a desk. \
A person talks to you out loud and hears your answer read aloud by a \
computer voice.

Because everything you say is spoken, not read:
- Answer in one or two sentences. Three at the very most.
- Use plain spoken language, the way a friendly person would talk.
- Never use markdown, bullet points, numbered lists, headings, or code blocks.
- Don't use emoji, asterisks, or symbols that sound strange when read aloud.
- Write numbers and units the way you'd say them: "about twenty miles", \
not "~20mi".
- Open with a short sentence. Nothing is said out loud until the first one is finished being turned into speech, so a long opening is a long silence.
- If a question is unclear, ask one short question back.

You are talking with a kid and their family, so keep it friendly and easy \
to follow. If you don't know something, just say so.

The questions come from speech recognition, so they arrive with no \
punctuation and the occasional wrong word. If something looks like a \
misheard word, guess what was meant from the sound of it rather than \
answering the wrong question."""


def _timezone_name() -> str:
    """The IANA zone if we can find it, otherwise the short abbreviation.

    "America/Los_Angeles" says more than "PDT" — it pins down roughly where
    the speaker is, which helps with questions like how long until sunset.
    """
    link = Path("/etc/localtime")
    if link.is_symlink():
        parts = Path(link.readlink()).parts
        if "zoneinfo" in parts:
            return "/".join(parts[parts.index("zoneinfo") + 1:])
    return datetime.now().astimezone().tzname() or "unknown"


def _part_of_day(hour: int) -> str:
    """How a person would describe this hour out loud."""
    if hour < 5:
        return "very early morning"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "late evening"


def _now_block() -> str:
    """Facts about right now, refreshed for every question.

    This is rebuilt per question rather than at startup, because a speaker
    on a shelf runs for hours — a clock captured when the program launched
    would be wrong by dinner time.
    """
    now = datetime.now().astimezone()
    date = now.strftime("%A, %B %d, %Y").replace(" 0", " ")
    clock = now.strftime("%I:%M %p").lstrip("0").lower()

    lines = [
        "Things you can't work out for yourself:",
        f"- Right now it is {date}, {clock} — the {_part_of_day(now.hour)}.",
        f"- The time zone here is {_timezone_name()}.",
    ]
    if config.LOCATION:
        lines.append(f"- The speaker is in {config.LOCATION}.")
    today = weather.summary()
    if today:
        lines.append(f"- Weather: {today}")
    if config.HOUSEHOLD:
        lines.append(f"- You're talking with {config.HOUSEHOLD}.")
    lines.append(
        "- Your training data stops well before today. If a question turns "
        "on recent events, say what you knew and roughly when, rather than "
        "guessing."
    )
    return "\n".join(lines)


# Claude is told what the speaker can do, and the list is generated from
# the tools themselves rather than written out here. The old version of this
# file spelled out "you have no timers" in prose, and the day timers were
# added it was still saying so.
LIMITS = """What this speaker can and can't do:
- You can {can}. Those are real: use the tool and something actually \
happens. Say what you did in one short sentence.
- You have no shopping, smart-home controls, phone calls, messages or \
calendar. When you're asked for something you can't do, do two things: \
write it down with make_a_wish, and say in one short sentence both that \
you can't do it yet and that you've written it down. Don't just refuse — \
a wish that goes unwritten is a thing nobody knows was wanted, and a child \
who is told a flat no stops asking. Then answer the question behind the \
request if there is one.
- Never say you've set, started, played, or ordered anything unless a tool \
did it. Nothing happens when you only say it.
- You remember only the last few minutes of talking, and you forget \
everything when the speaker is switched off. Alarms are the exception — \
those survive.

Searching the web takes a few seconds, and the person is standing there \
waiting. Search when the answer really does turn on something recent, \
local, or specific — today's news, when a shop shuts, a score. Don't \
search for things you already know, and don't search twice for one \
question if the first one answered it."""


# The wake word fires by mistake perhaps forty times an hour with a
# television on in the room. Almost all of those record a fragment of the
# television and nothing else, and answering them out loud in an empty room
# is the most annoying thing this speaker does. Whisper catches most of them
# by hearing no speech at all; this catches the rest, where the television
# said something real that simply wasn't addressed to us.
MISHEARD = """One more thing. The speaker wakes on the phrase "hey Claude", \
and with a television on in the room it sometimes mishears and wakes up \
when nobody spoke to it. When that happens you get whatever was on: half a \
sentence, an advert, one side of somebody else's conversation.

If what arrives is clearly not somebody talking to this speaker, reply with \
exactly this and nothing else:

(nothing)

Use it more readily than feels natural. Measured in this room, most of \
what reaches you is television, and answering it out loud is the most \
irritating thing this speaker does — worse than missing a real question, \
because a person who is ignored simply asks again. Saying "I didn't catch \
a question there" to an empty room is not a safe middle course; it is the \
failure. There is no middle course: either answer or say (nothing).

So: if it does not read as somebody addressing a speaker — no question, no \
request, no greeting, just a fragment of a sentence that starts or stops \
mid-thought — reply (nothing).

The exception is a child. A short, odd, badly transcribed question is \
still a question, and so is a one word answer to something you just asked. \
If it plausibly continues the conversation above, answer it."""

# What Claude says instead of answering, when nobody was talking to it.
SILENCE = "(nothing)"


def _system_prompt() -> str:
    wake = "hey Claude" if "claude" in config.WAKE_MODEL.lower() else "hey Jarvis"
    name = (
        f"People wake you by saying \"{wake}\", then asking their question. "
        "If someone asks what you're called or how to talk to you, that's the "
        "answer."
    )
    limits = LIMITS.format(can=tools.summary())
    import eggs
    return "\n\n".join([SYSTEM_PROMPT, limits, MISHEARD, name,
                         eggs.lines(), _now_block()])


# How many times round the ask-Claude, run-a-tool loop before giving up.
# Two covers everything the speaker does today; the extra rounds are only
# there so a question that needs a timer *and* the weather still works.
MAX_TOOL_ROUNDS = 5

# The most messages to carry, whatever the turn count says. Only a run of
# web searches gets near it — their results ride along in the history and
# are much larger than anything spoken.
MAX_MESSAGES = 30

# What to say when something goes wrong. Spoken out loud, so keep it short.
TROUBLE_MESSAGE = "Sorry, I had trouble thinking about that. Try again?"

# Nice to have, but not every model accepts them. Grouped so that one
# rejected key drops only the feature it belongs to.
_OPTIONAL_FEATURES = {
    # A speaker should answer fast. Low effort keeps Claude from thinking
    # longer than a spoken question deserves.
    "low effort": {"output_config": {"effort": "low"}},
    # If a safety check declines the request, retry it on another model
    # automatically instead of going silent.
    "server-side fallback": {
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    },
}


class Brain:
    """Claude, plus a short memory of the conversation so far."""

    def __init__(self) -> None:
        if not config.ANTHROPIC_API_KEY:
            raise SystemExit(
                "No API key found.\n"
                "Copy .env.example to .env and put your key in it:\n"
                "    cp .env.example .env\n"
                "Get a key at https://console.anthropic.com/settings/keys"
            )
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        # The conversation so far, so follow-up questions like
        # "what about tomorrow?" make sense.
        self.messages: list[dict] = []
        # Not every model takes the same options — Sonnet rejects
        # `fallbacks`, Haiku rejects `effort`. Rather than keep a list of
        # which model supports what and watch it go stale, ask for the extras
        # once and drop whichever the model complains about.
        self.extras = dict(_OPTIONAL_FEATURES)

    def ask(self, question: str) -> str:
        """Send a question to Claude and return the spoken answer.

        An empty string means say nothing at all — see SILENCE.
        """
        # Where the conversation stood before this question, so a failure
        # halfway through a tool call can be rolled back cleanly. A turn
        # used to add exactly one message and one pop undid it; a turn with
        # tools adds four or more.
        mark = len(self.messages)
        # Whether anything was actually done this turn. A silent answer
        # means two very different things: nobody was talking to us, or a
        # tool did the whole job and there is nothing left to say. "I have
        # spoken" stops the speaker and rightly says nothing back — and
        # that was being written into the wake word's training data as a
        # false wake, teaching it not to fire on somebody talking to it.
        self.did_something = False
        self.messages.append({"role": "user", "content": question})
        self._forget_old_turns()

        try:
            answer = self._converse()
        except anthropic.APIError as error:
            print(f"[Claude error] {error}")
            del self.messages[mark:]
            return TROUBLE_MESSAGE

        if answer.strip() == SILENCE:
            # Nobody was talking to us. Forget it ever happened, so the next
            # real question doesn't arrive with a television in its history.
            del self.messages[mark:]
            return ""

        if not answer:
            # Claude declined to answer, or returned nothing at all.
            del self.messages[mark:]
            return "Sorry, I can't help with that one."

        return answer

    def _converse(self) -> str:
        """Talk to Claude until it stops asking for tools, then return text.

        Most questions go round this loop once. One that needs a timer set
        goes round twice: Claude asks for the tool, we run it, and Claude
        turns the result into a sentence. The person hears one answer and
        never learns there were two calls.
        """
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create()
            self.messages.append(
                {"role": "assistant", "content": response.content})

            wanted = [block for block in response.content
                      if block.type == "tool_use"]
            if not wanted:
                return _text_of(response)

            self.did_something = True
            results = []
            for block in wanted:
                print(f"[tool] {block.name} {block.input}")
                outcome = tools.run(block.name, block.input)
                print(f"[tool] -> {outcome}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome,
                })
            self.messages.append({"role": "user", "content": results})

        # Round and round without settling. Rare, but a speaker that goes
        # quiet is worse than one that admits defeat.
        print(f"[tools] gave up after {MAX_TOOL_ROUNDS} rounds")
        return "Sorry, I got a bit tangled up. Ask me again?"

    def _create(self):
        """Ask Claude, dropping any option this model won't accept.

        The API rejects the whole request with a 400 naming the offending
        parameter, so we can take it out and try again. Each option is only
        lost once — after that the model runs without it.
        """
        while True:
            try:
                return self.client.beta.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=4000,
                    # Rebuilt per question so the clock is current — see
                    # _now_block().
                    system=_system_prompt(),
                    messages=self.messages,
                    tools=tools.specs(),
                    **{k: v for extra in self.extras.values()
                       for k, v in extra.items()},
                )
            except anthropic.BadRequestError as error:
                unsupported = [
                    name for name, extra in self.extras.items()
                    if any(f"`{key}`" in str(error) or f"'{key}'" in str(error)
                           for key in extra)
                ]
                if not unsupported:
                    raise
                # Not worth telling the person on the sofa about — these are
                # speed and reliability extras, and the answer is the same
                # either way.
                for name in unsupported:
                    del self.extras[name]

    def _forget_old_turns(self) -> None:
        """Keep only the most recent turns, so the conversation stays small.

        This counts turns, not messages, and the difference matters now that
        there are tools. A plain question and answer is two messages; one
        that sets a timer is four, and one that searches the web can be more
        and much bigger. Trimming to a fixed number of messages would have
        quietly halved how much the speaker remembered on the days it was
        useful, and never on the days it wasn't.

        Cutting at a spoken question is also what keeps the history legal:
        land in the middle of a tool exchange and you leave behind a
        tool_result whose tool_use is gone, which the API rejects outright.
        """
        starts = [i for i, message in enumerate(self.messages)
                  if _is_spoken_question(message)]

        cut = 0
        if len(starts) > config.HISTORY_TURNS:
            cut = starts[-config.HISTORY_TURNS]

        # A backstop for the pathological case: a run of web searches, whose
        # results are carried along in full and are far bigger than anything
        # a person says. Ten turns of those would be a lot of tokens to
        # re-send with every question.
        while len(self.messages) - cut > MAX_MESSAGES:
            later = [i for i in starts if i > cut]
            if not later:
                break
            cut = later[0]

        if cut:
            self.messages = self.messages[cut:]


def _is_spoken_question(message: dict) -> bool:
    """True for a message someone actually said out loud.

    Tool results are also "user" messages, but their content is a list of
    blocks rather than a string — which is exactly what tells them apart.
    """
    return message["role"] == "user" and isinstance(message["content"], str)


def _text_of(response) -> str:
    """Pull the plain text out of Claude's response.

    The response is a list of blocks — some are thinking, some are text —
    so we collect the text ones and ignore the rest.
    """
    parts = [block.text for block in response.content if block.type == "text"]
    joined = " ".join(part.strip() for part in parts).strip()
    # A web search answer comes back as several text blocks split around the
    # citations, so the join can leave a space before a full stop. Harmless
    # to read, but the voice pauses at it.
    return re.sub(r"\s+([.,!?;:])", r"\1", joined)


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    # Step 2 of the build order: type a question, hear Claude answer.
    import tts

    brain = Brain()
    print("Type a question (or press Enter to quit).")
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        answer = brain.ask(question)
        print(f"Claude: {answer}")
        tts.speak(answer)
