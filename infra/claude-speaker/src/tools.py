"""Tools — the plumbing that lets Claude do things, not just talk about them.

This file is only the framework. It knows how to collect tools, describe
them to Claude, and run one safely. It knows nothing about timers or rain,
and adding a capability doesn't touch it.

A tool lives in the module that implements it. `timers.py` owns the timer
tools, `sounds.py` owns the ones that play rain, and each is one function
and one decorator next to the code it drives:

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

Then add the module's name to FEATURES below. That is the whole of it —
the API description, the round trip, and the line in the system prompt
telling Claude the speaker can do it all follow from those two things.

Three rules for a tool:

  Return a sentence, not a data structure. Claude reads it and rephrases
  it for speaking, so plain English costs nothing and saves a translation
  step where things get lost.

  Never raise. A tool that throws would take the whole answer down with
  it; run() turns any exception into a sentence saying so, and Claude
  passes that on. A broken timer should cost you a timer, not the speaker.

  Be quick. Everything here happens while somebody stands there waiting.

`says` is what the speaker tells Claude it can do, in the same words a
person would use. It's generated from the tools rather than written out in
the prompt, because the prompt used to insist there were no timers on the
day timers were added.

    python src/tools.py         list every tool
"""

import json
import sys

# Every module that owns tools. Importing them is what registers what they
# can do, so this list is the only place a new capability has to appear.
FEATURES = ("timers", "weather", "sounds", "effects", "books", "music",
            "search", "enroll", "wishes", "eggs")

_REGISTRY: dict[str, "Tool"] = {}
_SERVER: list = []
_loaded = False


class Tool:
    """One thing the speaker can do."""

    def __init__(self, name, description, properties, required, says, run):
        self.name = name
        self.description = description
        self.properties = properties
        self.required = required
        self.says = says
        self.run = run

    def spec(self) -> dict:
        """The shape the API wants."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
            },
        }


def tool(description: str, properties: dict | None = None, required=(),
         says: str = ""):
    """Register a function as something Claude can ask for."""
    def keep(function):
        _REGISTRY[function.__name__] = Tool(
            name=function.__name__,
            description=description,
            properties=properties or {},
            required=required,
            says=says,
            run=function,
        )
        return function
    return keep


def server_tool(build, says: str = ""):
    """Register a tool that runs on Anthropic's side rather than here.

    `build` is called each time the tools are described, and returns the
    spec or None — None meaning the feature is switched off. Web search is
    the only one of these; it has no function to run, because the search
    happens inside the API between our question going out and the answer
    coming back.
    """
    _SERVER.append((build, says))


def load() -> None:
    """Import every feature module, so its tools register themselves."""
    global _loaded
    if _loaded:
        return
    _loaded = True  # Set first: a feature importing tools mustn't loop.
    import importlib
    for name in FEATURES:
        try:
            importlib.import_module(name)
        except Exception as error:
            # One broken feature shouldn't cost you the other five.
            print(f"[tools] {name} unavailable: {type(error).__name__}: {error}")


# --- using them -------------------------------------------------------------


def specs() -> list[dict]:
    """Every tool, in the form the API wants, for both kinds."""
    load()
    everything = [t.spec() for t in _REGISTRY.values()]
    for build, _ in _SERVER:
        built = build()
        if built is not None:
            everything.append(built)
    return everything


def run(name: str, arguments: dict) -> str:
    """Do what Claude asked for, and say what happened.

    Anything can go wrong in here and the answer still comes out — the
    person gets "I couldn't set that timer" instead of silence, which is
    the difference between a bug and a broken speaker.
    """
    load()
    found = _REGISTRY.get(name)
    if found is None:
        return f"There is no tool called {name}."

    # Drop anything the model invented that the function doesn't take,
    # rather than failing the whole call on one stray key.
    wanted = {key: value for key, value in (arguments or {}).items()
              if key in found.properties}
    try:
        return str(found.run(**wanted))
    except Exception as error:
        print(f"[tool {name} failed] {type(error).__name__}: {error}")
        return f"That didn't work — {type(error).__name__}."


def summary() -> str:
    """One line naming what the speaker can do, for the system prompt."""
    load()
    can = [t.says for t in _REGISTRY.values() if t.says]
    can += [says for build, says in _SERVER if says and build() is not None]
    if not can:
        return "answer questions"
    if len(can) == 1:
        return can[0]
    return ", ".join(can[:-1]) + ", and " + can[-1]


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    # Run as a script this file is __main__, not `tools` — and the feature
    # modules do `import tools`, which would load a second copy of it with
    # its own empty registry. So ask that copy, the one the tools actually
    # registered with.
    import tools

    for spec in tools.specs():
        if "input_schema" in spec:
            takes = ", ".join(spec["input_schema"]["properties"]) or "nothing"
            print(f"\n{spec['name']}({takes})")
            print(f"    {spec['description']}")
        else:
            print(f"\n{spec['name']}  (runs on Anthropic's side)")
            print("    " + json.dumps(
                {k: v for k, v in spec.items() if k != "name"}))

    print(f"\nThe speaker can {tools.summary()}.")
