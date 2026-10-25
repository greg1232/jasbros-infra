# What the speaker can do

Twenty-eight tools, in ten modules. Claude is told what they are and picks;
you never name a tool out loud. This page is what each one does and what it
needs set up.

Run `python src/tools.py` to print the live list — that is generated from
the code and cannot go stale.

| Capability | Needs | Works offline |
|---|---|---|
| Answering questions | An Anthropic key | no |
| Timers and alarms | nothing | yes |
| Background sounds | nothing | yes |
| The LED ring | the array | yes |
| Voice enrolment | nothing | yes |
| Wishes | nothing | yes |
| Weather | nothing | no |
| Web search | `WEB_SEARCH=on` (default) | no |
| Sound effects | a Freesound key (optional) | no |
| Books | the book index | no |
| Music | Spotify Premium | no |

## Answering questions

The default and the reason for the rest. `src/brain.py` keeps the last
`HISTORY_TURNS` exchanges (10) and hands Claude every tool at once, so a
question can turn into several actions in one breath — "play rain and set a
timer for twenty minutes" is one turn.

The model is `claude-sonnet-5`. Claude is about two thirds of the silence
between a question and a reply, so the choice is mostly a latency one:
measured on the same questions, Opus took 2.85 s, Sonnet 1.52 s and Haiku
0.87 s, and all three answered the same. Change it with `CLAUDE_MODEL`.

`LOCATION` and `HOUSEHOLD` are both optional and both only make it sound
less like a stranger — a town makes "how long until it gets dark"
answerable. Leave them empty if you'd rather not say.

Claude is also told to keep quiet when it wasn't being spoken to. With a
television on, the wake word fires by mistake dozens of times an hour, and
the speaker answering the television is worse than the speaker missing you.

## Timers and alarms

`set_timer`, `set_alarm`, `list_timers`, `cancel_timer`, and `start_mission`
— a timer with a name a child will take seriously.

Say "a minute and a half" and Claude works out 90 seconds itself. Alarms are
24-hour; with no date they take the next time that clock time comes round.
Both survive being asked about: "how long is left on the pasta".

A ringing timer beeps in bursts with gaps to listen in, so **saying the wake
word stops it**. `RING_SECONDS` (30) is the backstop for when nobody is in
the room.

## Background sounds

`play_sound`, `stop_sound`, `what_is_playing`. Seven of them, all synthesised
as they play rather than looped from a file: `rain`, `ocean`, `fireplace`,
`fan`, and `white`, `pink` and `brown` noise. Nothing is downloaded and
nothing repeats.

They run for `SOUND_HOURS` (8) unless somebody says otherwise, and play at
`SOUND_VOLUME` (0.30) — deliberately quiet, because it is something to fall
asleep to and the microphone still has to hear you over it.

Anything playing steps aside for a whole turn, not just while the speaker
talks, so questions aren't recorded over rain.

## The LED ring

Twelve LEDs on the array, showing what the speaker is doing. `LEDS=off`
turns it dark; `LED_BRIGHTNESS` (40 of 255) is a lamp on a shelf in the
evening, not a status light in a server room.

| | |
|---|---|
| dark | waiting for the wake word |
| blue | listening to your question |
| blue, breathing | thinking about it |
| green | talking back |
| red, fast | a timer is going off |
| purple | learning a voice |

The ring going dark on the way out of every turn is deliberate: a speaker
left glowing blue looks like it is still listening to you.

## Voice enrolment

`learn_wake_word`. "Hey Claude, learn my voice" — then say the wake word ten
or so times with a pause between each, while the ring is purple. It refits
and reloads in seconds.

This is the cure for the speaker missing a particular person, and it needs
no labelling: the person was asked to say the wake word over and over, so
every segment is the wake word by construction. It needs at least three
segments, and any segment that doesn't resemble the others — a cough, a
door, a sibling shouting — is dropped before fitting.

Recordings go in `state/`, not `train/`, because deploy mirrors the project
directory and would delete them.

## Wishes

`make_a_wish`. When somebody asks for something there is no tool for, the
speaker says plainly that it can't and that it has written it down. Both
halves matter: a child who is told no stops asking, and asking is the useful
part.

Read them with `./wishes.sh`. Repeats are folded together and counted.

## Weather

`get_weather`. Open-Meteo, no key and no account. Today's forecast is
fetched in the background at startup and put in Claude's notes, so "is it
cold out" costs no call at all; the tool is for other days.

Needs `LOCATION` set to be useful.

## Web search

Runs on Anthropic's side, not on the Pi, so it costs no local compute — but
it costs money per search and adds a few seconds. `WEB_SEARCH=off` disables
it; `WEB_SEARCH_MAX` (3) caps both the wait and the bill per question.

## Sound effects

`find_effect` then `play_effect`. Real recordings, looked up on demand:
Freesound if `FREESOUND_API_KEY` is set, otherwise Wikimedia Commons, which
needs no account and is noticeably worse at this.

Searching and playing are deliberately separate. A search match is very
often the wrong thing with the right word in it — a search for a cow
returned a 1922 jazz record by Cow Cow Davenport — so Claude reads the names
before choosing, and says so in words if none of them is the actual sound.

## Books

`find_book`, `play_book`, `stop_reading`, `change_chapter`, `what_book`.
Two sources, in order:

**LibriVox first** — twenty thousand public-domain books read aloud by human
volunteers, free and no key. For a bedtime story a real voice for two hours
beats a very good two-sentence voice stretched over a chapter, and it costs
the Pi no synthesis at all. Chapters arrive pre-split with titles and
durations, so "next chapter" is an index.

**Project Gutenberg second**, for the books nobody has recorded, read by
Piper. Not from gutenberg.org, which answers programs with 503; from the
copy on Hugging Face, with a 2.4 MB index of all 48,284 titles held locally
so searching needs no network. Build it with `train/build_book_index.py`.

It remembers where you stopped, per book, and picks up there. As with
effects, finding and playing are separate, because several recordings of a
well-known story usually exist at very different lengths, and among them an
abridgement, a sequel or a parody.

## Music

`find_music`, `play_music`, `pause_music`, `skip_music`, `music_volume`,
`what_music`.

**Needs Spotify Premium** — librespot, which does the streaming on the Pi,
cannot play on a free account at all. Set `SPOTIFY_CLIENT_ID` and
`SPOTIFY_CLIENT_SECRET` from a free app at
[developer.spotify.com](https://developer.spotify.com/dashboard), then run
`train/spotify_login.py` once on a machine with a browser for
`SPOTIFY_REFRESH_TOKEN`. The speaker never sees your password.

`SPOTIFY_DEVICE` (default "Claude Speaker") is what it calls itself in the
Spotify app's device list.

`music_volume` changes only the music, not how loud the speaker talks.

### It checks it is still a Spotify speaker

librespot can be running and useless, and systemd cannot tell the
difference. It sat here for five days reporting `active (running)` with its
last words being *"Connection to server closed"* — no sockets, no processor
— while `Restart=always` never fired, because that only fires when a
process *exits* and this one did not.

So every `MUSIC_WATCH_EVERY` (300 s) the speaker asks Spotify whether it
can still be played to, and after `MUSIC_WATCH_PATIENCE` (10 min) of not
being there it restarts librespot and says so:

    [music] not on Spotify for 11 min — restarting librespot

**There is a second way to lose it that a restart does not fix.** Spotify
Connect is one account per device, so a guest picking this speaker in their
own app does not play alongside you — librespot signs in as them, and the
account the speaker searches with can no longer see the device at all.

### It takes itself back when asked

Nothing patrols for that and nothing grabs the speaker back on a timer. A
guest keeps it for as long as nobody in the room wants it. The moment
somebody says **"play …"**, the speaker puts its own credentials back,
restarts librespot and plays — because the guest's music was already coming
out of this speaker, in this room, and whoever is standing in front of it
has the better claim.

    [music] the speaker is signed in as <someone> — taking it back
    [music] got it back

Measured here: 1.4 to 1.6 seconds to restart librespot and be playable
again, and 2.1 seconds from asking to hearing music, search included. Less
than the speaker spends transcribing the question.

It can only do this if it has somewhere to come back to, so it keeps a copy
of its own credentials in `state/spotify-owner.json` whenever they are
current — in `state/` because deploy mirrors the project directory and
would delete anything else. "Its own" needs no configuring: the owner is
the account the refresh token belongs to, which is already the account that
has to own librespot for any of this to work.

`MUSIC_TAKE_BACK=off` leaves it to say "open Spotify and pick Claude
Speaker once" instead, which is the only cure if there is no saved copy.

The dashboard carries the same answer under `spotify`, which is the only
place the difference shows without asking for a song and being told no.

`MUSIC_WATCH=off` turns the checking off.

## Easter eggs

Ten of them, in `src/eggs.py`, from Groot to Mark Rober. They are meant to
be found by accident, so they are not listed here.

`stop_everything` lives there too — talking, reading, music, sounds and a
ringing timer, all at once. "I have spoken" is one of the ways to say it.

## Voice and speech

| | on a laptop | on the Pi |
|---|---|---|
| Voice | macOS `say`, `VOICE=Samantha` | Piper, `PIPER_VOICE=en_GB-alan-medium` |
| Recognition | `WHISPER_MODEL=base.en` | `tiny.en` |

`SPEECH_RATE` (180 wpm) means the same on both. `SENTENCE_PAUSE` (0.12 s) is
Linux only — Piper's own spacing is about a fifth of a second once the
silence at both ends is counted, which sounds slow read aloud.

Stick to a *medium* Piper voice on a Pi 4: medium synthesises 0.32 seconds
per second of speech, so it talks three times faster than it can be listened
to, while *high* takes 2.0 and the speaker falls behind its own sentences.

`OUTPUT_VOLUME` (100) is applied at startup because USB audio often arrives
attenuated — the array came set about 20 dB down. Set it blank to leave the
system mixer alone.
