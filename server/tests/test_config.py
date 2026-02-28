"""Tests for the configuration loader."""

from pathlib import Path
from textwrap import dedent

from sotto.config import (
    Config,
    StorageConfig,
    WhisperConfig,
    load_config,
)


def test_default_config():
    """Default config should have sensible defaults."""
    config = Config()
    assert config.storage.output_dir == Path("~/.local/share/sotto").expanduser()
    assert config.server.port == 8377
    assert config.server.host == "0.0.0.0"
    assert "private" in config.pipelines
    assert "standard" in config.pipelines


def test_default_pipelines():
    """Default config should set up private and standard pipelines."""
    config = Config()
    assert config.pipelines["private"].llm_backend == "ollama"
    assert config.pipelines["standard"].llm_backend == "anthropic"
    assert config.pipelines["private"].transcription == "local"
    assert config.pipelines["standard"].transcription == "local"


def test_storage_config_incoming_dir_default():
    """Incoming dir should default to output_dir/incoming."""
    storage = StorageConfig(output_dir=Path("/tmp/sotto-test"))
    assert storage.incoming_dir == Path("/tmp/sotto-test/incoming")


def test_storage_config_completed_dir():
    """Completed dir should be output_dir/completed."""
    storage = StorageConfig(output_dir=Path("/tmp/sotto-test"))
    assert storage.completed_dir == Path("/tmp/sotto-test/completed")


def test_storage_config_custom_incoming_dir():
    """Custom incoming dir should be respected."""
    storage = StorageConfig(
        output_dir=Path("/tmp/sotto-test"),
        incoming_dir=Path("/tmp/custom-incoming"),
    )
    assert storage.incoming_dir == Path("/tmp/custom-incoming")


def test_whisper_config_defaults():
    """Whisper defaults to large-v3 on cpu."""
    whisper = WhisperConfig()
    assert whisper.model == "large-v3"
    assert whisper.device == "cpu"


def test_load_config_missing_file():
    """Loading a nonexistent config file should return defaults."""
    config = load_config(Path("/tmp/nonexistent-sotto-config.yaml"))
    assert config.server.port == 8377
    assert "private" in config.pipelines


def test_load_config_from_yaml(tmp_path):
    """Loading a config from YAML should parse correctly."""
    config_yaml = dedent("""\
        storage:
          output_dir: /tmp/sotto-yaml-test

        pipelines:
          private:
            transcription: local
            llm_backend: ollama
            model: mistral:7b

        whisper:
          model: small
          device: cpu

        server:
          host: 127.0.0.1
          port: 9000

        auth:
          tokens:
            - test-token-1
            - test-token-2
    """)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_yaml)

    config = load_config(config_file)
    assert config.storage.output_dir == Path("/tmp/sotto-yaml-test")
    assert config.pipelines["private"].model == "mistral:7b"
    assert config.whisper.model == "small"
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9000
    assert len(config.auth.tokens) == 2
    assert "test-token-1" in config.auth.tokens


def test_load_config_empty_yaml(tmp_path):
    """Loading an empty YAML file should return defaults."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")

    config = load_config(config_file)
    assert config.server.port == 8377


def test_config_ensure_dirs(tmp_path):
    """ensure_dirs should create all required directories."""
    config = Config(storage=StorageConfig(output_dir=tmp_path / "sotto-dirs-test"))
    config.ensure_dirs()

    assert config.storage.output_dir.exists()
    assert config.storage.incoming_dir.exists()
    assert config.storage.completed_dir.exists()
