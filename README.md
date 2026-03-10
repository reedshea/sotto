# Sotto

*Sotto voce: in a quiet voice.* Your words, captured reliably, processed privately.

Sotto is a two-component system for private voice capture, transcription, and intelligent dispatch:

1. **Sotto iOS App** — a voice recorder that saves audio locally first and syncs to your server in the background
2. **Sotto Server** — a Python service that receives audio, transcribes with Whisper, classifies intent, and routes output to an Obsidian vault (with optional Claude Code CLI orchestration for code/plan requests)

## Windows Setup (from source)

This is the setup for running sotto directly from the cloned repo on a Windows machine with an NVIDIA GPU.

### Prerequisites

- Python 3.11+
- NVIDIA GPU with CUDA support (for Whisper transcription)
- [NSSM](https://nssm.cc/download) on PATH (for running as a persistent service)
- [Ollama](https://ollama.ai) installed and running (for the private/local LLM pipeline)
- An Anthropic API key (for the standard pipeline — classification, summaries, drafts)
- Claude CLI authorized (`claude /login`) if you want orchestrator support (code_request / plan_request intents)

### 1. Clone and install

```powershell
git clone https://github.com/reedshea/sotto.git
cd sotto/server
pip install -e ".[dev]"
```

### 2. Initialize config

```powershell
sotto init
```

This creates `~/.config/sotto/config.yaml` with defaults. Open it and configure:

```yaml
storage:
  output_dir: "C:/Users/Reed/SottoData"

pipelines:
  private:
    transcription: local
    llm_backend: ollama
    model: llama3.1:8b
  standard:
    transcription: local
    llm_backend: anthropic
    model: claude-sonnet-4-6

api_keys:
  anthropic: sk-ant-...

ollama:
  endpoint: http://localhost:11434

whisper:
  model: large-v3
  device: cuda

server:
  host: 0.0.0.0
  port: 8377

auth:
  tokens:
    - "your-secret-token-here"

destinations:
  obsidian_vault: "C:/Users/Reed/SottoData/vault"

# Optional: projects for code_request / plan_request routing
# projects:
#   sotto:
#     path: "C:/Users/Reed/sotto"
#     aliases: ["soto", "sotto voice"]
```

### 3. Test that it starts

```powershell
sotto start
```

You should see Whisper model loading and the server listening on port 8377. Hit `Ctrl+C` to stop.

### 4. Verify with a test upload

```powershell
# Health check
curl http://localhost:8377/health

# Upload a test audio file (sync mode for immediate feedback)
curl -X POST http://localhost:8377/upload `
  -H "Authorization: Bearer your-secret-token-here" `
  -F "file=@test.m4a" `
  -F "sync=true"
```

Check that output appears in your configured locations:
- **Audio file:** `C:/Users/Reed/SottoData/incoming/`
- **Transcript:** `C:/Users/Reed/SottoData/completed/YYYY/MM/<uuid>.txt` and `.json`
- **Vault note:** `C:/Users/Reed/SottoData/vault/<intent-folder>/<date>-<slug>.md`

### 5. Install as a persistent Windows service

Run from an **Administrator PowerShell**:

```powershell
sotto install-service
```

This will:
- Register sotto with NSSM as an auto-start service
- Resolve your config.yaml to an absolute path (so it works regardless of service account)
- Prompt for your Windows password to run the service as your user account (required for Claude CLI auth)
- Set up log files at `~/.local/share/sotto/logs/`

Then start it:

```powershell
nssm start Sotto
```

**Important:** The service must run as your Windows user (not LocalSystem) so it has access to:
- Your Claude CLI authorization token (stored in your user profile)
- Your configured output directories

You can verify the service account with:

```powershell
nssm get Sotto ObjectName
```

It should show `.\Reed` (or your username), not `LocalSystem`.

### 6. Managing the service

```powershell
# Check status
sotto status
nssm status Sotto

# Restart after pulling new code
nssm restart Sotto

# View logs
Get-Content ~\.local\share\sotto\logs\sotto-stderr.log -Tail 50

# Uninstall
sotto uninstall-service
```

### Updating (from source)

When iterating on the code:

```powershell
cd sotto
git pull
cd server
pip install -e ".[dev]"
nssm restart Sotto
```

No need to reinstall the service — NSSM points to the entry point which picks up code changes on restart.

## iOS App Setup

Configure the iOS app's server URL and auth token to point to your machine:

- **Server URL:** `http://<your-windows-ip>:8377`
- **Auth token:** the token from your `config.yaml` `auth.tokens` list

Make sure your Windows firewall allows inbound connections on port 8377.

## How It Works

```
iPhone → POST /upload (audio) → Sotto Server
                                    ├── Whisper transcription (local, GPU)
                                    ├── Intent classification (Anthropic API)
                                    ├── Title + summary generation
                                    ├── Write transcript to completed/YYYY/MM/
                                    └── Dispatch based on intent:
                                        ├── note_to_self    → vault/notes/
                                        ├── meeting_debrief → vault/meetings/
                                        ├── journal         → vault/journal/ (daily append)
                                        ├── draft_request   → vault/drafts/ (LLM-generated)
                                        ├── idea            → vault/ideas/
                                        ├── task            → vault/tasks/
                                        ├── code_request    → Orchestrator (Claude CLI)
                                        ├── plan_request    → Orchestrator (Claude CLI)
                                        └── general         → vault/inbox/
```

## Output Locations

| Location | Contents |
|----------|----------|
| `{output_dir}/incoming/` | Raw uploaded audio files |
| `{output_dir}/completed/YYYY/MM/` | `{uuid}.txt` transcript + `{uuid}.json` metadata |
| `{vault}/inbox/` | Uncategorized items |
| `{vault}/notes/` | Notes to self |
| `{vault}/meetings/` | Meeting debriefs |
| `{vault}/journal/` | Daily journal (appended) |
| `{vault}/drafts/` | LLM-generated drafts |
| `{vault}/ideas/` | Idea captures |
| `{vault}/tasks/` | Extracted action items |
| `{vault}/plans/` | Implementation plans |
| `{vault}/reports/` | Orchestrator output |

## API

### Upload audio

```
POST /upload
Content-Type: multipart/form-data
Authorization: Bearer <token>

file: <audio file>
privacy: "private" | "standard"   (optional, default: "standard")
sync: true | false                (optional, default: false)
transcribe_only: true | false     (optional, default: false)
```

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

## Troubleshooting

**Files appearing in `C:\WINDOWS\system32\config\systemprofile\...` instead of your user directory:**
The service is running as LocalSystem instead of your user. Fix with:
```powershell
nssm set Sotto ObjectName ".\YourUsername" "your-password"
nssm restart Sotto
```

**"Anthropic API key not configured" in logs:**
Your config.yaml isn't being loaded. Check that the service has the right config path:
```powershell
nssm get Sotto AppEnvironmentExtra
# Should show: SOTTO_CONFIG=C:\Users\Reed\.config\sotto\config.yaml
```

**Classification always falls back to "general":**
The Anthropic API key is missing or invalid in your config.yaml. Check the `api_keys.anthropic` field.

**Whisper model downloading on every start:**
This is normal on first run. The model is cached after the first download in the Hugging Face cache directory.

## License

MIT — Built by Reed Shea. Open source. Private by design.
