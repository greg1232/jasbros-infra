# Tools

A tool is one thing the speaker can do. Claude is handed all of them with
every question and decides which to call; nobody says a tool's name out
loud.

`src/tools.py` is the framework and nothing else. It collects tools,
describes them to the API, and runs one safely. It knows nothing about
timers or rain, and adding a capability does not touch it.

## Adding one

A tool lives in the module that implements it, as one function and one
decorator next to the code it drives:

```python
import tools

@tools.tool(
    "Turn the kitchen light on or off.",
    properties={"on": {"type": "boolean", "description": "..."}},
    required=["on"],
    says="turn the kitchen light on and off",
)
def set_kitchen_light(on: bool) -> str:
    ...
    return "The kitchen light is on."
```

Then add the module's name to `FEATURES` in `src/tools.py`:

```python
FEATURES = ("timers", "weather", "sounds", "effects", "books", "music",
            "search", "enroll", "wishes", "eggs")
```

That is the whole of it. The API description, the round trip, and the line
in the system prompt saying the speaker can do it all follow from those two
things.

Check it with `python src/tools.py`, which lists every registered tool and
prints the sentence Claude will be told.

## The three rules

**Return a sentence, not a data structure.** Claude reads what comes back
and rephrases it for speaking, so plain English costs nothing and saves a
translation step where things get lost.

**Never raise.** A tool that throws would take the whole answer down with
it. `run()` turns any exception into a sentence, and Claude passes it on: a
broken timer should cost you a timer, not the speaker. `load()` is the same
idea one level up — one feature module that fails to import doesn't cost you
the other nine.

**Be quick.** Everything here happens while somebody stands there waiting.

## `says`

`says` is what the speaker tells Claude it can do, in the words a person
would use. `tools.summary()` joins them into one line of the system prompt.

It is generated from the registered tools rather than written out in the
prompt by hand, because a hand-written prompt goes stale the moment a
capability is added — and did, insisting there were no timers on the day
timers were added.

A tool with no `says` still works; it just isn't advertised in that
sentence. That is right for the ones Claude should reach for only when the
situation calls for it, rather than mention.

## Arguments

`properties` and `required` are a JSON Schema, passed to the API as
`input_schema`. Describe each field in the same plain language as the tool
itself — the description is what the model actually reads.

`run()` drops any key the function does not take, rather than failing the
whole call on one invented argument.

## Two-step tools

Searching and playing are separate tools everywhere the speaker reaches into
a library it does not control: `find_effect` then `play_effect`,
`find_book` then `play_book`, `find_music` then `play_music`.

The find returns a numbered list and plays nothing. Claude reads the names
and calls play with a number, usually in the same breath. It exists because
a search match is very often the wrong thing with the right word in it, and
the model reading the results is a much better filter than the library's
own ranking.

## Server tools

`server_tool()` registers something that runs on Anthropic's side rather
than here. Web search is the only one. `build` is called each time the tools
are described and returns a spec or `None` — `None` meaning the feature is
switched off — so `WEB_SEARCH=off` removes it from the list entirely rather
than offering a tool that refuses.

There is no function to run, because the search happens inside the API
between the question going out and the answer coming back.

## Why the speaker doesn't write its own tools

This file is close to a specification a machine could meet, and given a
paragraph and told to read it, Claude Code has written a working feature
module — tools, `says` line, persisted state, the name added to `FEATURES` —
in a sandbox, on the first try.

What stops that being wired up is the television. It reaches the speaker's
transcript about fifty times an hour, and every one of those is a sentence
somebody else wrote being read aloud to a machine. The path from "something
was said in this room" to "code was written and run" has to be broken by
something a broadcast cannot cross, and the only reliable such thing is a
person reading a diff.

So the speaker's entire power here is to write a line to a file —
`make_a_wish`, read back by `./wishes.sh`. Everything that can execute lives
somewhere it cannot reach.
