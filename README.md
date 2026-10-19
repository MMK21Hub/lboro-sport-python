# `lboro-sport-python`

A Python API Client for the Loughborough Sport (Legend Online Services // myFitApp) service, based on the Android app's behaviour.

(Lowkey forgot that there was a web app that would've been way easier to reverse engineer)

## Usage

Use `uv` and Python 3.13 or newer.

```bash
# Install deps
uv sync
# Fetch client configuration by pretending to be an Android app
uv run bootstrap_config.py
# Fetch some sessions using get_available_sessions.py
uv run get_available_sessions.py --activity badminton --days 7
```

### Tests and linting

```bash
uv run pytest
uv run ruff check .
```

## Credits

Heavily vibecoded by Mish using Command Code and DeepSeek V4.1. Sorry not sorry. As such, licensed under CC-0 - do what you like as long as you don't me responsible.

README written by me.
