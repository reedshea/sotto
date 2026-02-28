"""CLI entry points for Sotto server."""

from __future__ import annotations

import argparse
import logging
import shutil
import signal
import sys
import threading
from pathlib import Path

import uvicorn

from .config import DEFAULT_CONFIG_PATH, load_config
from .db import Database
from .receiver import init_app
from .worker import Worker


def cmd_init(args: argparse.Namespace) -> None:
    """Interactive setup — writes config.yaml."""
    dest = Path(args.config).expanduser()
    if dest.exists() and not args.force:
        print(f"Config already exists at {dest}")
        print("Use --force to overwrite.")
        return

    # Copy the example config
    example = Path(__file__).parent.parent / "config.yaml.example"
    if not example.exists():
        # Fallback: write a minimal config
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_minimal_config(), encoding="utf-8")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, dest)

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

    # Start worker in a background thread
    db = Database(config.storage.output_dir / "sotto.db")
    db.connect()
    worker = Worker(config, db)

    worker_thread = threading.Thread(target=worker.run, daemon=True)
    worker_thread.start()

    def shutdown(sig, frame):
        print("\nShutting down...")
        worker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
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

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "start":
        cmd_start(args)
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
  endpoint: http://localhost:11434

whisper:
  model: large-v3
  device: cpu

server:
  host: 0.0.0.0
  port: 8377

auth:
  tokens:
    - "change-me-to-a-real-token"
"""
