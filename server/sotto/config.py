"""Configuration loader for Sotto server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path("~/.config/sotto/config.yaml").expanduser()
DEFAULT_OUTPUT_DIR = Path("~/.local/share/sotto").expanduser()


@dataclass
class WhisperConfig:
    model: str = "large-v3"
    device: str = "cpu"


@dataclass
class OllamaConfig:
    # Default assumes Ollama runs on the Docker host.
    # Override with SOTTO_OLLAMA_ENDPOINT or in config.yaml.
    endpoint: str = "http://host.docker.internal:11434"


@dataclass
class PipelineConfig:
    transcription: str = "local"
    llm_backend: str = "ollama"
    model: str = "llama3.1:8b"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8377


@dataclass
class ProjectConfig:
    """A project/repo that can be targeted by plan_request dictations."""
    path: str = ""  # Local path to the repo
    aliases: list[str] = field(default_factory=list)  # Alternative names


class DestinationsConfig(dict):
    """Arbitrary named destinations, keyed by name, values are paths.

    Always includes 'obsidian_vault' with a default.
    Access via dict syntax: destinations["obsidian_vault"], destinations.get("e-reader").
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setdefault("obsidian_vault", "~/.local/share/sotto/vault")

    @property
    def obsidian_vault(self) -> str:
        """Convenience accessor for the primary vault path."""
        return self["obsidian_vault"]


@dataclass
class PatternConfig:
    trigger: str = ""
    intent: str = "general"


@dataclass
class OrchestratorConfig:
    """Configuration for the async Claude Code CLI orchestrator layer."""
    max_concurrent: int = 4
    timeout_seconds: int = 600
    session_store_path: str | None = None  # Defaults to output_dir/orchestrator.db
    report_dir: str | None = None  # Defaults to vault_root/reports
    allow_edits: bool = True  # Pass --dangerously-skip-permissions to Claude CLI


@dataclass
class AuthConfig:
    tokens: list[str] = field(default_factory=list)


@dataclass
class StorageConfig:
    output_dir: Path = field(default_factory=lambda: DEFAULT_OUTPUT_DIR)
    incoming_dir: Path | None = None

    def __post_init__(self):
        self.output_dir = Path(self.output_dir).expanduser()
        if self.incoming_dir is None:
            self.incoming_dir = self.output_dir / "incoming"
        else:
            self.incoming_dir = Path(self.incoming_dir).expanduser()

    @property
    def completed_dir(self) -> Path:
        return self.output_dir / "completed"


@dataclass
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    pipelines: dict[str, PipelineConfig] = field(default_factory=dict)
    api_keys: dict[str, str] = field(default_factory=dict)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    destinations: DestinationsConfig = field(default_factory=DestinationsConfig)
    patterns: list[PatternConfig] = field(default_factory=list)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

    def __post_init__(self):
        if not self.pipelines:
            self.pipelines = {
                "private": PipelineConfig(
                    transcription="local", llm_backend="ollama", model="llama3.1:8b"
                ),
                "standard": PipelineConfig(
                    transcription="local", llm_backend="anthropic", model="claude-sonnet-4-6"
                ),
            }

    def ensure_dirs(self):
        """Create all required directories."""
        self.storage.output_dir.mkdir(parents=True, exist_ok=True)
        self.storage.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.storage.completed_dir.mkdir(parents=True, exist_ok=True)


def load_config(path: Path | None = None) -> Config:
    """Load configuration from a YAML file."""
    if path is None:
        env_path = os.environ.get("SOTTO_CONFIG")
        path = Path(env_path) if env_path else DEFAULT_CONFIG_PATH

    if not path.exists():
        return Config()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    storage = StorageConfig(**raw.get("storage", {}))

    pipelines = {}
    for name, pconf in raw.get("pipelines", {}).items():
        pipelines[name] = PipelineConfig(**pconf)

    api_keys = raw.get("api_keys", {})
    ollama = OllamaConfig(**raw.get("ollama", {}))
    if env_ollama := os.environ.get("SOTTO_OLLAMA_ENDPOINT"):
        ollama.endpoint = env_ollama
    whisper = WhisperConfig(**raw.get("whisper", {}))
    server = ServerConfig(**raw.get("server", {}))
    auth = AuthConfig(**raw.get("auth", {}))
    destinations = DestinationsConfig(raw.get("destinations", {}))

    patterns = []
    for pconf in raw.get("patterns", []):
        patterns.append(PatternConfig(**pconf))

    projects = {}
    for name, pconf in raw.get("projects", {}).items():
        if isinstance(pconf, str):
            # Short form: project_name: /path/to/repo
            projects[name] = ProjectConfig(path=pconf)
        else:
            aliases = pconf.pop("aliases", [])
            projects[name] = ProjectConfig(**pconf, aliases=aliases)

    orchestrator = OrchestratorConfig(**raw.get("orchestrator", {}))

    return Config(
        storage=storage,
        pipelines=pipelines,
        api_keys=api_keys,
        ollama=ollama,
        whisper=whisper,
        server=server,
        auth=auth,
        destinations=destinations,
        patterns=patterns,
        projects=projects,
        orchestrator=orchestrator,
    )
