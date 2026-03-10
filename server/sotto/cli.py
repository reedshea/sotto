"""CLI entry points for Sotto server."""

from __future__ import annotations

import argparse
import importlib.resources
import logging
import shutil
import signal
import sys
import threading
from pathlib import Path

import uvicorn

from .config import DEFAULT_CONFIG_PATH, load_config
from .db import Database
from .orchestrator import Orchestrator
from .receiver import init_app
from .service import install_service, service_status, uninstall_service
from .worker import Worker


def cmd_init(args: argparse.Namespace) -> None:
    """Interactive setup — writes config.yaml."""
    dest = Path(args.config).expanduser()
    if dest.exists() and not args.force:
        print(f"Config already exists at {dest}")
        print("Use --force to overwrite.")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Try to copy the bundled example config
    try:
        ref = importlib.resources.files("sotto").joinpath("config.yaml.example")
        with importlib.resources.as_file(ref) as example:
            shutil.copy(example, dest)
    except (FileNotFoundError, TypeError):
        dest.write_text(_minimal_config(), encoding="utf-8")

    print(f"Config written to {dest}")
    print("Edit this file to set your API keys, model preferences, and output directory.")


def cmd_start(args: argparse.Namespace) -> None:
    """Start the receiver and worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = load_config(Path(args.config).expanduser() if args.config else None)
    config.ensure_dirs()

    # Initialize FastAPI app with config
    init_app(config)

    # Start worker in a background thread, with orchestrator for active intents
    db = Database(config.storage.output_dir / "sotto.db")
    db.connect()
    orchestrator = Orchestrator(config)
    worker = Worker(config, db, orchestrator=orchestrator)

    worker_thread = threading.Thread(target=worker.run, daemon=True)
    worker_thread.start()

    def shutdown(sig, frame):
        print("\nShutting down...")
        worker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, shutdown)

    # Start uvicorn (blocks)
    uvicorn.run(
        "sotto.receiver:app",
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="sotto", description="Sotto voice transcription server")
    sub = parser.add_subparsers(dest="command")

    # sotto init
    init_parser = sub.add_parser("init", help="Create config file")
    init_parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG_PATH), help="Config file path"
    )
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config")

    # sotto start
    start_parser = sub.add_parser("start", help="Start receiver and worker")
    start_parser.add_argument("--config", default=None, help="Config file path")

    # sotto install-service
    svc_install = sub.add_parser("install-service", help="Install Sotto as a system service")
    svc_install.add_argument("--config", default=None, help="Config file path")
    svc_install.add_argument(
        "--run-as", default=None, metavar="USERNAME",
        help="Windows user to run the service as (default: current user). "
             "Required for Claude CLI auth.",
    )

    # sotto uninstall-service
    sub.add_parser("uninstall-service", help="Remove Sotto system service")

    # sotto status
    sub.add_parser("status", help="Check if Sotto service is running")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "install-service":
        install_service(args.config, getattr(args, "run_as", None))
    elif args.command == "uninstall-service":
        uninstall_service()
    elif args.command == "status":
        st = service_status()
        if st:
            print(f"Sotto service: {st}")
        else:
            print("Sotto service not found or not installed.")
    else:
        parser.print_help()


def _minimal_config() -> str:
    return """\
storage:
  output_dir: ~/.local/share/sotto

pipelines:
  private:
    transcription: local
    llm_backend: ollama
    model: llama3.1:34b
  standard:
    transcription: local
    llm_backend: anthropic
    model: claude-sonnet-4-6

api_keys:
  anthropic: sk-...

ollama:
  endpoint: http://host.docker.internal:11434

whisper:
  model: large-v3
  device: cpu

server:
  host: 0.0.0.0
  port: 8377

auth:
  tokens:
    - "change-me-to-a-real-token"

# Where classified transcripts are dispatched as Obsidian-compatible markdown.
# Point this at your Obsidian vault or a synced directory.
destinations:
  obsidian_vault: ~/.local/share/sotto/vault

# Fast-path intent patterns — if the transcript starts with one of these
# trigger phrases, skip LLM classification and use the mapped intent directly.
patterns:
  - trigger: "note to self"
    intent: note_to_self
  - trigger: "meeting with"
    intent: meeting_debrief
  - trigger: "journal entry"
    intent: journal
  - trigger: "draft"
    intent: draft_request
  - trigger: "idea"
    intent: idea
"""
