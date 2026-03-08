# Plan: Dictation Workflow Integration

Two enhancements to make Sotto fit into your daily workflow: (1) route output into your existing Box/Obsidian notes infrastructure, and (2) add a new "plan" intent that kicks off a Claude Code session from a voice dictation.

---

## Part 1: Box.com Integration for Notes Syncing

### Current State
- Sotto's dispatcher writes Obsidian-formatted markdown to `destinations.obsidian_vault` (default: `~/.local/share/sotto/vault`)
- Your Obsidian notes live in a Box-synced folder, with an occasional git backup to GitHub
- Your server is running in your basement, always on

### Recommended Approach: Symlink the Vault into Box

The simplest, lowest-friction option — no code changes required:

1. **Install Box Drive on your server** (or use the Box CLI/rclone if headless)
2. **Point `destinations.obsidian_vault` directly at your Box notes folder** in `~/.config/sotto/config.yaml`:
   ```yaml
   destinations:
     obsidian_vault: ~/Box/Notes  # or wherever your Obsidian vault lives in Box
   ```
   Alternatively, symlink: `ln -s ~/Box/Notes/sotto ~/.local/share/sotto/vault`

3. **That's it.** Every dispatched note (notes/, meetings/, journal/, etc.) lands directly in your Box-synced Obsidian vault.

### Alternative: rclone for Headless Servers

If Box Drive doesn't work well on a headless Linux server:

1. Install `rclone` and configure it with your Box account (`rclone config`)
2. Add a post-dispatch step or a cron job:
   ```bash
   # Every 5 minutes, sync new sotto output to Box
   */5 * * * * rclone sync ~/.local/share/sotto/vault box:Notes/sotto --update
   ```
3. This is slightly more friction but works reliably on headless Linux

### Why Not Build a Box API Integration?

- Box API auth (OAuth2) adds complexity you don't need
- Filesystem-level sync (Box Drive or rclone) is battle-tested and requires zero code changes
- If Box ever goes away, you just re-point the config — no code to rip out

### Git Backup Consideration

Your occasional git check-in workflow stays exactly the same — the files are in the same Obsidian vault folder, just with new sotto-generated content appearing. If you want to automate the git commits for sotto-generated notes specifically, a small cron job could do it:

```bash
# In your Obsidian vault git repo
cd ~/Box/Notes && git add -A sotto/ && git diff --cached --quiet || git commit -m "sotto: auto-commit $(date +%Y-%m-%d)"
```

But this is optional — your manual check-in habit is probably fine to start.

---

## Part 2: Dictation → Claude Code Planning Workflow

### The Workflow You Described

1. Dictate into the iOS app ("I want to refactor the auth module in project X...")
2. Sotto transcribes and classifies it as a **plan request**
3. The system identifies which **project/repo** you're talking about
4. It kicks off a Claude Code session that:
   - Explores the codebase
   - Creates a plan based on your dictation
   - Saves the plan where you can see it
5. You open Claude Code (Mac app) and see the proposed plan
6. You review, iterate, then tell it to execute

### Implementation Plan

This breaks into four pieces:

#### Step 1: New Intent — `plan_request`

Add `plan_request` to the classifier and dispatcher.

**classifier.py** changes:
- Add `plan_request` to the valid intents list
- Update the classification prompt to recognize when someone is describing work they want planned/investigated on a codebase
- Add pattern triggers: `"plan for"`, `"investigate"`, `"look into"`, `"I want to work on"` in the config

**Config additions:**
```yaml
patterns:
  - trigger: "plan for"
    intent: plan_request
  - trigger: "create a plan"
    intent: plan_request

# Map of project names/aliases to repo paths
projects:
  sotto: /home/user/sotto
  myapp: /home/user/projects/myapp
  # Can also be GitHub URLs: owner/repo
```

**classifier.py** — enhanced entity extraction:
- The classifier already extracts `entities.projects` — extend it to look for project references and map them to configured repos

#### Step 2: New Dispatcher Handler — `_handle_plan_request`

**dispatcher.py** changes:
- Add `_handle_plan_request` handler
- This handler:
  1. Resolves the project/repo from the classification entities + config
  2. Writes a plan-request markdown file to `vault/plans/` (for your records)
  3. Invokes Claude Code to create the plan (see Step 3)
  4. Updates the markdown file with the plan output when done

#### Step 3: Claude Code Integration

Two options here, from simplest to most sophisticated:

**Option A: Claude Code CLI invocation (recommended to start)**

The server shells out to `claude` CLI:

```python
import subprocess

def _invoke_claude_plan(self, transcript, project_path, plan_file):
    """Run Claude Code CLI to create a plan from the dictation."""
    prompt = f"""The user dictated the following request via voice memo.
    Please explore the codebase and create a detailed implementation plan.

    User's dictation:
    {transcript}

    Write your plan to: {plan_file}
    """

    result = subprocess.run(
        ["claude", "--print", "--project-dir", project_path, prompt],
        capture_output=True, text=True, timeout=300
    )
    return result.stdout
```

- The plan gets written to a file in the project repo (e.g., `PLAN.md` or `.claude/plans/{date}-{slug}.md`)
- When you open Claude Code Mac app on that project, you'll see the plan file
- You can also have it written to your vault for Obsidian visibility

**Option B: Claude Code on Web via API**

If you want it to run as a web session you can pick up in the Claude Mac app:

- Use the Claude API to create a conversation that includes the codebase context
- The session would be visible in your Claude account across devices
- This is essentially what you're doing right now — dictating into the iOS Claude app

The key insight: **Option A is probably better for your workflow** because:
- The plan lives in the repo as a file, not just in a chat session
- You can `git diff` it, iterate on it, reference it later
- Claude Code Mac app picks up the project context automatically
- It runs on your always-on server, not dependent on a browser session

#### Step 4: iOS App — Project Selection

Add a project picker to the iOS recording UI:

- Pull the list of configured projects from the server (new API endpoint: `GET /projects`)
- User selects the target project before or after recording
- Alternatively: the system infers the project from the dictation content (works for clear cases like "in the sotto project..." but a picker is more reliable)

### Phased Rollout

**Phase 1 (minimal, try it this week):**
- Add `plan_request` intent to classifier
- Add `_handle_plan_request` to dispatcher
- Handler writes the plan-request to `vault/plans/`
- Handler shells out to `claude --print` with the transcript
- Saves Claude's output back to the plan file
- You manually check the plan file

**Phase 2 (better UX):**
- Add `projects` config section mapping names to repo paths
- Classifier extracts project references
- Plan output goes both to vault and to a `PLAN.md` in the target repo
- Add `GET /projects` endpoint for the iOS app
- iOS app gets a project picker

**Phase 3 (polish):**
- Plan status tracking in the DB (plan_pending → plan_generating → plan_ready)
- iOS app shows plan status and can open the plan
- Push notification when plan is ready
- Integration with Claude Code sessions for seamless handoff

---

## Recommended Starting Point

1. **This week:** Point `obsidian_vault` at your Box folder. Zero code, immediate value.
2. **Next session:** Implement Phase 1 of the plan_request intent — add the new intent type, a basic dispatcher handler, and the Claude CLI invocation. This is ~200 lines of code across classifier.py, dispatcher.py, and config.py.
3. **Iterate:** Use it for a week, see what friction points emerge, then tackle Phase 2.

The Box integration is deliberately boring infrastructure — that's the point. Get notes flowing into your existing habits first, then layer on the more ambitious dictation-to-plan workflow.
