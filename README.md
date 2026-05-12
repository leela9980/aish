# aish

Everybody has favourite or most used commands. Bash history is not always accurate in finding previous commands — this will save your time going through a notepad to find the exact command.

`aish` is a macOS shell assistant that turns English requests into terminal commands.

Edit `commands.yml` to add your most used commands:

```yaml
context:
  platform: macOS
  shell: zsh

aliases:
  list files here: ls -la
  open browser: open -a Safari
```

It uses a hybrid resolver:
- RapidFuzz for high-confidence matches from `commands.yml`
- LLM fallback for open-ended requests

Every resolved command is shown before execution and requires confirmation.

## Requirements

- macOS
- Python 3.10+
- A GitHub token in `GITHUB_TOKEN` for LLM fallback

## Setup

From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

## Configure the token (AI help)

Set your GitHub Models token before using LLM fallback:

```bash
export GITHUB_TOKEN=your_token_here
```

The token must include the `models` permission. If it does not, LLM fallback will fail with a 401 unauthorized error.

If you only use phrases that match aliases in `commands.yml`, the token is not needed for those requests.


## Show help

After activating the virtual environment:

```bash
aish --help
```

You can also run the script directly:

```bash
python3 aish.py --help
```

## Usage

Single-shot mode:

```bash
aish "list files here"
```

Dry run:

```bash
aish --dry-run "list files here"
```

Interactive mode:

```bash
aish
```

Type `exit` or `quit` to leave interactive mode.

## Useful flags

```bash
aish --debug "show disk usage"
aish --dry-run "find large files"
```

## Notes

- Run `source .venv/bin/activate` in each new terminal session before using `aish`.
- `python3 -m pip install -e .` installs the CLI in editable mode, so code changes are picked up without reinstalling.
- If `aish` is not found, confirm the virtual environment is active.