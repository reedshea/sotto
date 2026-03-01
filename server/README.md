# Sotto

*Sotto voce: in a quiet voice.* Your words, captured reliably, processed privately.

Sotto is a lightweight Python server that receives audio uploads, transcribes them locally with Whisper, generates a short title and summary via LLM, and writes clean text output to a configurable location.

Designed to run on hardware you control. Pairs with the [Sotto iOS app](https://github.com/reedshea/sotto) for reliable voice capture.

## Quickstart

```bash
pip install sotto
sotto init          # creates ~/.config/sotto/config.yaml
sotto start         # starts receiver + worker on port 8377
```

## Configuration

```bash
sotto init
# Edit ~/.config/sotto/config.yaml
sotto start
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
