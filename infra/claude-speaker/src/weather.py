"""Today's weather, for the speaker to mention when asked.

Uses Open-Meteo, which is free, needs no API key and no account. Set
LOCATION in .env to switch it on:

    LOCATION=Palo Alto, California

Nothing here is allowed to slow down an answer. The forecast is fetched on
a background thread and cached; asking for it returns whatever was last
fetched, immediately, even while a refresh is in flight. If the network is
down or the location can't be found, it returns None and the speaker simply
doesn't know the weather — which is much better than a question hanging for
several seconds.

    python src/weather.py        print what the speaker would be told
"""

import threading
import time

import requests

import sys

import config
import tools

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Weather stays useful for a while, and re-asking every question would be
# rude to a free service.
REFRESH_SECONDS = 15 * 60
TIMEOUT_SECONDS = 8

# WMO weather codes, in the words a person would actually use out loud.
CONDITIONS = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy",
    45: "foggy", 48: "freezing fog",
    51: "drizzling lightly", 53: "drizzling", 55: "drizzling heavily",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "raining lightly", 63: "raining", 65: "raining heavily",
    66: "freezing rain", 67: "freezing rain",
    71: "snowing lightly", 73: "snowing", 75: "snowing heavily",
    77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}

_lock = threading.Lock()
_cached: str | None = None
_fetched_at: float = 0.0
_refreshing = False
_place: dict | None = None


def _find_place() -> dict | None:
    """Turn the LOCATION setting into coordinates. Done once."""
    global _place
    if _place is not None:
        return _place
    if not config.LOCATION:
        return None

    response = requests.get(
        GEOCODE_URL,
        params={"name": config.LOCATION, "count": 1},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    results = response.json().get("results")
    if not results:
        return None
    _place = results[0]
    return _place


def _describe(code: int) -> str:
    return CONDITIONS.get(code, "hard to describe")


def _fetch() -> str | None:
    """Ask Open-Meteo what it's like, and phrase it for reading aloud."""
    place = _find_place()
    if place is None:
        return None

    # Americans expect Fahrenheit and miles per hour; almost everyone else
    # expects Celsius and kilometres.
    american = place.get("country_code") == "US"
    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code",
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max,sunrise,sunset"),
            "timezone": "auto",
            "temperature_unit": "fahrenheit" if american else "celsius",
            "wind_speed_unit": "mph" if american else "kmh",
            "forecast_days": 1,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    now, day = data["current"], data["daily"]
    degrees = "degrees" if american else "degrees Celsius"
    rain = day["precipitation_probability_max"][0]

    parts = [
        f"Right now in {place['name']} it is {round(now['temperature_2m'])} "
        f"{degrees} and {_describe(now['weather_code'])}.",
        f"Today: {_describe(day['weather_code'][0])}, "
        f"between {round(day['temperature_2m_min'][0])} and "
        f"{round(day['temperature_2m_max'][0])} {degrees}.",
    ]
    if rain is not None and rain >= 20:
        parts.append(f"About a {rain} percent chance of rain.")
    parts.append(
        f"The sun rose at {_clock(day['sunrise'][0])} and sets at "
        f"{_clock(day['sunset'][0])}."
    )
    return " ".join(parts)


def _clock(stamp: str) -> str:
    """"2026-08-16T19:59" as "7:59 pm"."""
    hour, minute = stamp.split("T")[1].split(":")[:2]
    hour = int(hour)
    suffix = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}:{minute} {suffix}"


def _refresh_in_background() -> None:
    global _cached, _fetched_at, _refreshing

    def work() -> None:
        global _cached, _fetched_at, _refreshing
        try:
            fresh = _fetch()
        except Exception:
            # No weather is fine. A broken speaker is not.
            fresh = None
        with _lock:
            if fresh is not None:
                _cached = fresh
            _fetched_at = time.monotonic()
            _refreshing = False

    with _lock:
        if _refreshing:
            return
        _refreshing = True
    threading.Thread(target=work, daemon=True).start()


def summary() -> str | None:
    """Today's weather, or None if we don't know it.

    Returns immediately, always. A stale forecast is served while a fresh
    one is fetched behind it.
    """
    if not config.LOCATION:
        return None
    with _lock:
        cached, age = _cached, time.monotonic() - _fetched_at
    if cached is None or age > REFRESH_SECONDS:
        _refresh_in_background()
    return cached


def start() -> None:
    """Fetch once at startup, so the first question already knows."""
    if config.LOCATION:
        _refresh_in_background()


def forecast(days_ahead: int = 0) -> str:
    """The weather for one day, fetched now. For the get_weather tool.

    `summary()` is the cached one-liner that goes into every system prompt;
    this is the version that answers "what's it like on Thursday". It waits
    for the network instead of returning something stale, because a question
    was actually asked and a wrong answer is worse than a pause.
    """
    if not config.LOCATION:
        return ("I don't know where this speaker is, so I can't look up the "
                "weather. Set LOCATION in the .env file.")
    if not 0 <= days_ahead <= 6:
        return "I can only look a week ahead."

    try:
        place = _find_place()
    except Exception:
        place = None
    if place is None:
        return f"I couldn't find {config.LOCATION} on the map."

    american = place.get("country_code") == "US"
    try:
        response = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
                "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                          "precipitation_probability_max,sunrise,sunset"),
                "timezone": "auto",
                "temperature_unit": "fahrenheit" if american else "celsius",
                "wind_speed_unit": "mph" if american else "kmh",
                "forecast_days": days_ahead + 1,
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as error:
        return f"The weather service didn't answer ({type(error).__name__})."

    day, index = data["daily"], days_ahead
    degrees = "degrees" if american else "degrees Celsius"
    when = {0: "Today", 1: "Tomorrow"}.get(
        index, _weekday(day["time"][index]))

    parts = []
    if index == 0:
        now = data["current"]
        parts.append(
            f"Right now in {place['name']} it is "
            f"{round(now['temperature_2m'])} {degrees} and "
            f"{_describe(now['weather_code'])}."
        )
    parts.append(
        f"{when} in {place['name']}: {_describe(day['weather_code'][index])}, "
        f"between {round(day['temperature_2m_min'][index])} and "
        f"{round(day['temperature_2m_max'][index])} {degrees}."
    )
    rain = day["precipitation_probability_max"][index]
    if rain is not None:
        parts.append(f"A {rain} percent chance of rain.")
    parts.append(
        f"Sunrise {_clock(day['sunrise'][index])}, "
        f"sunset {_clock(day['sunset'][index])}."
    )
    return " ".join(parts)


@tools.tool(
    "Look up the weather forecast for where the speaker is. Today's weather "
    "is already in your notes above, so only use this for another day, or "
    "for detail the notes don't have.",
    properties={
        "days_ahead": {
            "type": "integer",
            "description": "0 for today, 1 for tomorrow, up to 6.",
        },
    },
    says="look up the weather forecast",
)
def get_weather(days_ahead: int = 0) -> str:
    return forecast(int(days_ahead))


def _weekday(stamp: str) -> str:
    """"2026-08-27" as "Thursday"."""
    from datetime import date
    return date.fromisoformat(stamp).strftime("%A")


def place_hint() -> dict | None:
    """Where the speaker is, for the web search tool to narrow results with.

    Comes free: the weather geocoder already looked the town up, and it
    hands back the region, country and time zone in the form the search
    tool wants. Without this, "when does the library shut" searches the
    whole world.
    """
    if not config.LOCATION:
        return None
    try:
        place = _find_place()
    except Exception:
        return None
    if place is None:
        return None

    hint = {"type": "approximate"}
    if place.get("name"):
        hint["city"] = place["name"]
    if place.get("admin1"):
        hint["region"] = place["admin1"]
    if place.get("country_code"):
        hint["country"] = place["country_code"]
    if place.get("timezone"):
        hint["timezone"] = place["timezone"]
    return hint


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if not config.LOCATION:
        raise SystemExit(
            "LOCATION isn't set, so the speaker has no weather.\n"
            "Add a line like this to .env:\n"
            "    LOCATION=Palo Alto, California"
        )
    start()
    for _ in range(50):
        if summary():
            break
        time.sleep(0.2)
    print(summary() or "Couldn't fetch the weather.")
