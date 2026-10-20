# `lboro-sport-python`

A Python API Client for the Loughborough Sport (Legend Online Services // myFitApp) service, modelled on the [Android app](https://www.lboro.ac.uk/sport/app/)'s behaviour.

(Low-key forgot that there also exists a web app which would've been way easier to reverse engineer)

![Screenshot of the CLI being used: uv run get_available_sessions.py --activity badminton --days 2](assets/screenshot.png)

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
