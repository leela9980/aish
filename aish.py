import argparse
import os
import readline
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from openai import APIStatusError, OpenAI
from rapidfuzz import fuzz, process
from rich.console import Console
from rich.panel import Panel


DEFAULT_BASE_URL = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_ACCEPT_THRESHOLD = 90
DEFAULT_AMBIGUOUS_THRESHOLD = 75
HISTORY_FILE = Path.home() / ".aish_history"
HISTORY_LENGTH = 200

console = Console()


@dataclass
class ResolverConfig:
    aliases: dict[str, str]
    context: dict[str, Any]


@dataclass
class Resolution:
    command: str
    source: str
    score: float | None = None
    alias_key: str | None = None
    skip_execute_prompt: bool = False


def normalize_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def load_config(config_path: Path) -> ResolverConfig:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    aliases = raw.get("aliases", {}) or {}
    context = raw.get("context", {}) or {}

    if not isinstance(aliases, dict):
        raise ValueError("'aliases' must be a mapping in commands.yml")
    if not isinstance(context, dict):
        raise ValueError("'context' must be a mapping in commands.yml")

    cleaned_aliases: dict[str, str] = {}
    for key, value in aliases.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("All alias keys and values must be strings")
        cleaned_aliases[key] = value

    return ResolverConfig(aliases=cleaned_aliases, context=context)


def build_system_prompt(config: ResolverConfig) -> str:
    alias_block = yaml.safe_dump(config.aliases, sort_keys=True, allow_unicode=False).strip()
    context_block = yaml.safe_dump(config.context, sort_keys=True, allow_unicode=False).strip()
    return (
        "You convert user English requests into one shell command for macOS zsh.\n"
        "Rules:\n"
        "1) Return exactly one command line, no explanation.\n"
        "2) Do not include markdown or code fences.\n"
        "3) Prefer safe, non-destructive commands.\n"
        "4) Respect aliases and context hints when relevant.\n\n"
        f"Aliases (natural language -> command):\n{alias_block}\n\n"
        f"Context hints:\n{context_block}\n"
    )


def strip_command_output(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^```(?:bash|sh|zsh)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[0] if lines else ""


def resolve_with_alias(
    user_text: str,
    aliases: dict[str, str],
    accept_threshold: int,
    ambiguous_threshold: int,
) -> Resolution | None:
    if not aliases:
        return None

    normalized_input = normalize_text(user_text)
    alias_keys = list(aliases.keys())
    normalized_pairs = [(key, normalize_text(key)) for key in alias_keys]

    match = process.extractOne(
        normalized_input,
        [item[1] for item in normalized_pairs],
        scorer=fuzz.WRatio,
    )

    if not match:
        return None

    _, score, index = match
    best_key = normalized_pairs[index][0]

    if score >= accept_threshold:
        return Resolution(command=aliases[best_key], source="alias", score=score, alias_key=best_key)

    if score >= ambiguous_threshold:
        return Resolution(
            command=aliases[best_key],
            source="alias-ambiguous",
            score=score,
            alias_key=best_key,
        )

    return None


def resolve_with_llm(
    user_text: str,
    config: ResolverConfig,
    model: str,
    base_url: str,
) -> Resolution:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN environment variable")

    client = OpenAI(api_key=token, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": build_system_prompt(config)},
                {"role": "user", "content": user_text},
            ],
        )
    except APIStatusError as exc:
        if exc.status_code == 401:
            raise RuntimeError(
                "GitHub token is missing access to GitHub Models. "
                "Create or update a token with the 'models' permission, then export it as GITHUB_TOKEN."
            ) from exc
        raise RuntimeError(f"LLM request failed: {exc.status_code}") from exc

    content = response.choices[0].message.content if response.choices else ""
    command = strip_command_output(content or "")
    if not command:
        raise RuntimeError("LLM did not return a usable command")

    return Resolution(command=command, source="llm")


def ask_yes_no(prompt: str, default_no: bool = True) -> bool:
    suffix = "[y/N]" if default_no else "[Y/n]"
    raw = console.input(f"{prompt} {suffix} ").strip().lower()
    if not raw:
        return not default_no
    return raw in {"y", "yes"}


def render_resolution(request: str, resolution: Resolution, debug: bool) -> None:
    lines = [f"Command: {resolution.command}"]
    if debug:
        lines = [
            f"Request: {request}",
            f"Source: {resolution.source}",
            f"Command: {resolution.command}",
        ]
        if resolution.alias_key is not None:
            lines.append(f"Alias: {resolution.alias_key}")
        if resolution.score is not None:
            lines.append(f"Score: {resolution.score:.2f}")

    console.print(Panel("\n".join(lines), title="aish"))


def execute_command(command: str) -> int:
    process_handle = subprocess.Popen(
        command,
        shell=True,
        executable="/bin/zsh",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process_handle.stdout is not None
    for line in process_handle.stdout:
        console.print(line, end="")

    return process_handle.wait()


def configure_readline() -> None:
    readline.set_history_length(HISTORY_LENGTH)
    if HISTORY_FILE.exists():
        try:
            readline.read_history_file(HISTORY_FILE)
        except OSError:
            pass


def save_history_entry(request: str) -> None:
    if not request:
        return

    history_length = readline.get_current_history_length()
    if history_length == 0 or readline.get_history_item(history_length) != request:
        readline.add_history(request)

    try:
        readline.write_history_file(HISTORY_FILE)
    except OSError:
        pass


def resolve_request(
    request: str,
    config: ResolverConfig,
    model: str,
    base_url: str,
    accept_threshold: int,
    ambiguous_threshold: int,
    debug: bool,
) -> Resolution:
    alias_result = resolve_with_alias(
        user_text=request,
        aliases=config.aliases,
        accept_threshold=accept_threshold,
        ambiguous_threshold=ambiguous_threshold,
    )

    if alias_result is None:
        if debug:
            console.print("No alias match above threshold. Falling back to LLM.")
        return resolve_with_llm(request, config, model, base_url)

    if alias_result.source == "alias":
        return alias_result

    render_resolution(request, alias_result, debug=debug)
    if ask_yes_no("Use this alias match ('yes' or 'no')?", default_no=True):
        alias_result.source = "alias"
        alias_result.skip_execute_prompt = True
        return alias_result

    if debug:
        console.print("Alias declined. Falling back to LLM.")
    return resolve_with_llm(request, config, model, base_url)


def run_once(args: argparse.Namespace, request: str, config: ResolverConfig) -> int:
    resolution = resolve_request(
        request=request,
        config=config,
        model=args.model,
        base_url=args.base_url,
        accept_threshold=args.accept_threshold,
        ambiguous_threshold=args.ambiguous_threshold,
        debug=args.debug,
    )

    render_resolution(request, resolution, debug=args.debug)

    if args.dry_run:
        console.print("Dry run enabled. Command not executed.")
        return 0

    if not resolution.skip_execute_prompt and not ask_yes_no("Execute command ('yes' or 'no')?", default_no=True):
        console.print("Execution cancelled.")
        return 0

    return execute_command(resolution.command)


def repl(args: argparse.Namespace, config: ResolverConfig) -> int:
    configure_readline()
    console.print("Interactive mode. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            request = input("aish> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nExiting.")
            return 0

        if not request:
            continue
        if request.lower() in {"clear", "cls"}:
            console.clear()
            continue
        if request.lower() in {"exit", "quit"}:
            return 0

        save_history_entry(request)

        try:
            status = run_once(args, request, config)
            if status != 0:
                console.print(f"Command exited with status {status}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"Error: {exc}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI shell for macOS with hybrid alias + LLM routing")
    parser.add_argument("request", nargs="*", help="Natural language request")
    parser.add_argument("--config", default="commands.yml", help="Path to YAML config file")
    parser.add_argument("--dry-run", action="store_true", help="Resolve command but do not execute")
    parser.add_argument("-d", "--debug", action="store_true", help="Show routing details")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name for LLM fallback")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL")
    parser.add_argument(
        "--accept-threshold",
        type=int,
        default=DEFAULT_ACCEPT_THRESHOLD,
        help="RapidFuzz score threshold to auto-accept alias",
    )
    parser.add_argument(
        "--ambiguous-threshold",
        type=int,
        default=DEFAULT_AMBIGUOUS_THRESHOLD,
        help="RapidFuzz score threshold to suggest alias before LLM fallback",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.accept_threshold < args.ambiguous_threshold:
        console.print("Error: --accept-threshold must be >= --ambiguous-threshold")
        return 2

    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Failed to load config: {exc}")
        return 2

    request = " ".join(args.request).strip()
    if request:
        try:
            return run_once(args, request, config)
        except Exception as exc:  # noqa: BLE001
            console.print(f"Error: {exc}")
            return 1

    return repl(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
