"""Web search — the one tool that doesn't run here.

There's no function to write. Claude searches inside the API, between our
question going out and the answer coming back, so the Pi never fetches a
page and there's no second round trip to pay for. All this module does is
say the feature exists, and say where "here" is so local questions get
local answers.

It isn't free, in either sense. Each search costs money, and it adds
seconds to an answer that somebody is standing there waiting for — so the
number of searches per question is capped, and brain.py tells Claude to use
it only when the answer really does turn on something recent.

    python src/search.py        print the spec that gets sent
"""

import json
import sys

import config
import tools
import weather


def spec() -> dict | None:
    """The web search tool, or None if it's switched off."""
    if not config.WEB_SEARCH:
        return None

    built = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": config.WEB_SEARCH_MAX,
    }
    # Where we are, so "when does the museum close" doesn't search the
    # other side of the planet. Costs nothing — the weather geocoder has
    # already looked the town up, and hands back exactly these fields.
    here = weather.place_hint()
    if here:
        built["user_location"] = here
    return built


tools.server_tool(spec, says="search the web for things you don't know")


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    built = spec()
    print(json.dumps(built, indent=2) if built
          else "Web search is off (WEB_SEARCH=off).")
