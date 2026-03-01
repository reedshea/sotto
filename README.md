# Sotto 

*Sotto voce: in a quiet voice.* Your words, captured reliably, processed privately.

Sotto is a two-component open source system for reliable private voice capture and transcription:

1. **Sotto iOS App** — a rock-solid voice recorder that saves audio locally first and syncs to your infrastructure in the background
2. **Sotto Server** — a lightweight Python service that receives audio files, transcribes them, generates a short title and summary, and writes clean text output to a configurable location

Sotto does one thing well: turn your spoken words into text files that live on hardware you control.

## Repository Structure

```
sotto/
├── README.md
├── docker-compose.yml
├── ios/                    # Swift iOS app (Xcode project)
│   └── Sotto/
└── server/                 # Python server package
    ├── pyproject.toml
    ├── config.yaml.example
    ├── Dockerfile
    └── sotto/
        ├── __init__.py
        ├── cli.py          # CLI entry points (init, start)
        ├── config.py       # Configuration loader
        ├── db.py           # SQLite job tracking
        ├── receiver.py     # FastAPI upload service
        └── worker.py       # Transcription + LLM pipeline
```

## Server Quickstart

### Via pip

```bash
pip install sotto
sotto init          # creates ~/.config/sotto/config.yaml
sotto start         # starts receiver + worker on port 8377
```

### Via Docker

```bash
docker compose up
```

### From source

```bash
cd server
pip install -e ".[dev]"
sotto init
sotto start
```

## Configuration

Copy and edit the example config:

```bash
cp server/config.yaml.example ~/.config/sotto/config.yaml
```

Key settings:

- **storage.output_dir** — where completed transcripts are written (default: `~/.local/share/sotto`)
- **pipelines** — separate pipelines for private (local LLM) and standard (API) processing
- **whisper.model** — Whisper model size (default: `large-v3`)
- **whisper.device** — `cuda` or `cpu`
- **auth.tokens** — bearer tokens for authenticating uploads from the iOS app

## API

### Upload audio

```
POST /upload
Content-Type: multipart/form-data
Authorization: Bearer <token>

file: <audio file>
privacy: "private" | "standard"
```

Returns: `{"uuid": "...", "status": "pending"}`

### Check job status

```
GET /jobs/<uuid>
Authorization: Bearer <token>
```

### List jobs

```
GET /jobs?limit=50&offset=0
Authorization: Bearer <token>
```

### Health check

```
GET /health
```

## Output

For each completed job, two files are written to `output_dir/completed/YYYY/MM/`:

- `<uuid>.txt` — clean transcript
- `<uuid>.json` — metadata (title, summary, duration, pipeline info)

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) running separately (for private pipeline)
- Anthropic API key (for standard pipeline)
- CUDA-capable GPU recommended for Whisper transcription

## License

MIT — Built by Reed Shea. Open source. Private by design.
