# n8n Setup — shorty-factory autonomous pipeline

Daily-cron + generic webhook ("openclaw" entry point) → shorty-factory `/jobs` → YouTube upload → Telegram notification.

> **Telegram triggers are skipped on purpose.** Telegram's Bot API requires an HTTPS webhook URL for inbound messages, which doesn't work cleanly with plain `http://localhost`. We trigger from the phone via the generic Webhook trigger instead (callable from any phone shortcut, IFTTT applet, externally-hosted Telegram bot, or plain curl). Telegram is still used for **outbound notifications** — outbound `sendMessage` calls work fine over plain HTTP.

## 1. Install and start n8n

**Recommended: Docker (one container, persistent volume).**

```bash
docker volume create n8n_data
docker run -d --name n8n --restart unless-stopped \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  -e GENERIC_TIMEZONE=Asia/Manila \
  -e TZ=Asia/Manila \
  -e N8N_HOST=localhost \
  -e N8N_PORT=5678 \
  -e N8N_PROTOCOL=http \
  -e N8N_EDITOR_BASE_URL=http://localhost:5678/ \
  -e WEBHOOK_URL=http://localhost:5678/ \
  n8nio/n8n
```

The full set of `N8N_*` and `WEBHOOK_URL` env vars is set explicitly because different n8n versions read the OAuth callback URL from different ones. With all five aligned to `localhost:5678`, Google OAuth, browser callbacks, and webhook node URLs all agree.

Notes:
- **`WEBHOOK_URL=http://localhost:5678/`** is what n8n advertises to the *browser* and to *OAuth providers* (Google, etc.). It must be `localhost`, not `host.docker.internal` — Google's OAuth validator only accepts `localhost` or public-TLD domains as redirect URIs. Browsers reach n8n via Docker's port mapping, so `localhost:5678` works fine.
- **`host.docker.internal:8000` is for *server-to-server* calls** from inside the n8n container to shorty-factory on the host. You'll use it later in HTTP Request nodes (e.g. `POST http://host.docker.internal:8000/jobs`), but **not** in `WEBHOOK_URL`.
- shorty-factory (running natively on the Windows host) reaches n8n at `localhost:5678` because Docker Desktop maps the published port.

**Alternative: `npx n8n` (fast to try, no Docker).** Runs on `http://localhost:5678` directly. Use `http://localhost:8000` for shorty-factory and `http://localhost:5678/...` for webhook URLs. Trade-off: state lives in `~/.n8n` so don't `Ctrl-C` mid-workflow.

Open `http://localhost:5678`. First boot prompts you to create an owner account. Save the encryption key shown — losing it means losing all stored credentials.

## 2. Credentials (set up before building the workflow)

In n8n: **Settings → Credentials → New**.

### 2.1 NVIDIA Build (used to generate ideas)

n8n has no native "NVIDIA" credential, but NVIDIA's API is OpenAI-compatible. Use the generic **HTTP Header Auth** credential:

- Name: `NVIDIA Build`
- Header name: `Authorization`
- Header value: `Bearer YOUR_NVAPI_KEY` (the same key in your `.env`)

You'll attach this to a generic HTTP Request node, not an OpenAI node.

### 2.2 YouTube OAuth2

1. Google Cloud Console → new project (e.g. `shorty-factory`).
2. **APIs & Services → Library → enable "YouTube Data API v3"**.
3. **APIs & Services → OAuth consent screen** → external, fill in app name, your email, scopes: `https://www.googleapis.com/auth/youtube.upload`. Add yourself as a test user (otherwise you can't auth in "Testing" status).
4. **Credentials → Create Credentials → OAuth client ID → Web application**. Authorized redirect URI: `http://localhost:5678/rest/oauth2-credential/callback` (Docker-Compose users: same URL — n8n exposes it on the host port).
5. In n8n: **Credentials → New → YouTube OAuth2 API** → paste client ID + client secret → click "Connect my account" → Google consent screen → grant the upload scope.

Test-mode caveat: refresh tokens expire after 7 days while the OAuth app is in "Testing" status. Either submit for verification or accept weekly re-auths during the first month.

### 2.3 Telegram bot (for outbound notifications only)

We use Telegram **only to send success/error messages back to your chat** — not to trigger workflows. Outbound `sendMessage` works fine over plain HTTP, so no HTTPS tunnel needed.

1. Talk to `@BotFather` on Telegram → `/newbot` → pick a name → it returns a bot token.
2. Talk to your new bot once (`/start`) so it can DM you back.
3. Get your chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending a message — find the `"chat":{"id": ...}` field.
4. In n8n: **Credentials → New → Telegram API** → paste the bot token. Save the chat ID separately; you'll paste it into outbound notify nodes.

## 3. Build the workflow

Create a new workflow called `daily-short`. The shape is:

```
[Schedule Trigger]┐
                  ├─→ [Merge] → [Healthz Guard*] → [Idea LLM**] → [Set: JobCreate]
[Webhook Trigger ]┘                                                       │
                                                                          ▼
                                                  [HTTP POST /jobs] → [Wait: Resume on Webhook]
                                                                          │
                                                                          ▼
                                                                   [IF status=="done"]
                                                                     │           │
                                                                     ▼ (true)    ▼ (false)
                                                          [HTTP GET download]  [Telegram: error]
                                                                     │
                                                                     ▼
                                                          [YouTube: Upload Video]
                                                                     │
                                                                     ▼
                                                          [Telegram: success notify]
```

`*` Healthz Guard only runs for the cron path (skip if `ran_today=true`). The Webhook trigger bypasses it.
`**` Idea LLM only runs if the trigger didn't supply an idea. Webhook callers may pre-fill `idea`/`niche` in the POST body.

> **Why no Telegram Trigger?** Telegram requires HTTPS for inbound webhooks; we keep n8n on plain `http://localhost`. The Webhook trigger handles everything a Telegram trigger would — a phone-side bot, shortcut, or IFTTT applet POSTs to the webhook URL when you want to fire a job.

### 3.1 Triggers

#### Schedule Trigger
- Mode: **Every day**
- Hour: pick a time you reliably have your machine on (e.g. 09:00).
- Output: empty payload — the LLM step generates the idea.

#### Webhook Trigger
- HTTP Method: `POST`
- Path: `short`
- Authentication: **Header Auth** with a credential containing a long random secret (e.g. header `X-Trigger-Token`, value any 40-char hex). Keeps openclaw or anything else from being callable without the token.
- Response Mode: **When last node finishes** so the caller sees the final result.
- Expected body: `{"idea": "...optional...", "niche": "...optional...", "num_scenes": 4}` — all optional.

Tell whatever "openclaw" turns out to be to POST to `http://your-host:5678/webhook/short` with the header `X-Trigger-Token: <secret>` and the JSON body.

### 3.2 Merge

A **Merge** node, mode: **Append** (or **Combine → Append**), with **two inputs** — one for the Schedule branch (after the Healthz guard), one for the Webhook branch. Both feed the same downstream pipeline.

### 3.3 Healthz guard (cron-only)

After Merge, an **IF** node:
- Condition: `{{ $json.__trigger }} === 'cron'` — but you'll need to tag the trigger source. Easier alternative: put an **HTTP Request** before the Merge on the Schedule branch only, hitting `GET http://host.docker.internal:8000/healthz`, and gate on `ran_today === false`.

Concretely:

**Schedule Trigger → HTTP Request: Healthz**
- Method: GET
- URL: `http://host.docker.internal:8000/healthz`
- Response: JSON

**HTTP Request → IF: ran_today**
- Condition: `{{ $json.ran_today }}` equals `false`
- Branch true → continues to Merge.
- Branch false → ends silently (no notification — cron skip is normal).

(The Webhook branch goes directly to Merge; on-demand triggers always run regardless of `ran_today`.)

### 3.4 Idea LLM (skip when trigger supplied one)

After Merge, an **IF** node `Has idea?`:
- Condition: `{{ $json.idea }}` is not empty.
- True branch: skip LLM, go to `Set: JobCreate`.
- False branch: call NVIDIA.

**Recommended pattern: pre-pick the niche deterministically in n8n, then ask the LLM only for the idea.** This avoids the LLM hallucinating niches that don't exist in your `PROMPT_FOR_NICHE` map and gives you control over rotation. Add a **Code** node `Pick niche` *before* the Generate idea node:

```javascript
// Single source of truth for your channel's niches. Add new ones here whenever
// you expand. Each must also have a matching entry in app/providers/music/musicgen.py
// PROMPT_FOR_NICHE so MusicGen produces niche-appropriate music.
const niches = [
  "filipino-mythology",
  // "liminal-dread",
  // "sleep-paralysis",
];

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
return [{ json: { niche: pick(niches) } }];
```

False-branch node: **HTTP Request** named `Generate idea`:
- Method: POST
- URL: `https://integrate.api.nvidia.com/v1/chat/completions`
- Auth: NVIDIA Build credential.
- Headers: `Content-Type: application/json`
- Body (JSON) — note the LLM only generates `idea`; niche comes from the Code node above:
  ```json
  {
    "model": "meta/llama-3.3-70b-instruct",
    "messages": [
      {
        "role": "system",
        "content": "You write one-line video ideas for a short-form vertical AI shorts channel. Respond with STRICT JSON: {\"idea\": \"...\"}. The idea is a single declarative sentence describing a visual scene appropriate to the niche specified in the user message. No other fields."
      },
      {
        "role": "user",
        "content": "Niche: {{ $('Pick niche').item.json.niche }}\n\nGenerate today's idea for this niche."
      }
    ],
    "temperature": 0.9,
    "response_format": {"type": "json_object"}
  }
  ```
- After this node, a **Set** node parses `choices[0].message.content` as JSON, pulls out `idea`, and merges in the `niche` from the `Pick niche` node so both flow downstream together.

**Why split it this way?** Pre-picking the niche means:
1. You can extend your channel by editing one array (no prompt changes needed).
2. The LLM can't pick a niche that has no MusicGen mapping (which would silently fall back to default ambient music).
3. You can add deterministic rotation later — e.g. day-of-week themes — by replacing `pick(arr)` with a calendar-based selector.

### 3.5 Set: JobCreate body

A **Set** node `Build JobCreate`:
- `idea`: `{{ $json.idea }}`
- `niche`: `{{ $json.niche }}`
- `num_scenes`: `4` (or `{{ $json.num_scenes ?? 4 }}` if the webhook supplied one)
- `webhook_url`: `http://localhost:5678/webhook/shorty-done/{{ $execution.id }}`

The `webhook_url` is hardcoded `localhost:5678` (not `$env.WEBHOOK_URL`) because n8n's `$env` access is blocked by default for security and we don't need indirection — the URL is stable for your local-Docker setup. shorty-factory (running on the host) reaches `localhost:5678` via Docker's port mapping.

### 3.6 POST /jobs

**HTTP Request** `Create job`:
- Method: POST
- URL: `http://host.docker.internal:8000/jobs`
- Body type: JSON
- Body: full Set output above.
- On 4xx: turn on "Continue On Fail" and branch to a Telegram error: a 429 means another job is already running.

### 3.7 Wait + Resume on Webhook

A **Wait** node:
- Resume: **On Webhook Call**
- HTTP Method: POST
- Resume timeout: 4 hours (so a hung meta.ai run doesn't pin the workflow forever — bump higher if first MusicGen download still hasn't run).

The Wait node auto-generates the resume URL at runtime; you reference it as `{{ $execution.resumeUrl }}` in the **Build JobCreate** node above (step 3.5). When shorty-factory POSTs back, n8n matches the URL to this Wait node and resumes the workflow.

**Webhook payload shape** (what `$json` contains after resume):

On success:
```json
{
  "job_id": "abc123def456",
  "status": "done",
  "result_url": "/jobs/abc123def456/download",
  "youtube_title": "The Tikbalang Hunter's Last Mistake",
  "youtube_description": "Deep in the rainforest, a hunter pursues a creature said to vanish...\n\n#FilipinoMythology #Tikbalang #Folklore #Shorts"
}
```

On failure:
```json
{
  "job_id": "abc123def456",
  "status": "error",
  "error": "MetaSessionExpired: meta.ai shows a login wall",
  "error_type": "session_expired"
}
```

`error_type` lets you route the error notification by cause — see step 3.12.

### 3.8 IF status==done

**IF** node:
- Condition: `{{ $json.status }}` equals `done`.
- True → continue to download.
- False → Telegram error notification with `{{ $json.error }}` and exit.

### 3.9 Download final.mp4

**HTTP Request** `Download final`:
- Method: GET
- URL: `http://host.docker.internal:8000{{ $json.result_url }}` (the result_url is already the full `/jobs/{id}/download` path)

Actually — `result_url` is built from `settings.public_base_url` in shorty-factory. If that's set to `http://localhost:8000`, n8n inside Docker can't reach `localhost`. Set the env var on the shorty-factory side:

```
PUBLIC_BASE_URL=http://host.docker.internal:8000
```

Then n8n can fetch the result_url directly.

- Response: **File / Binary**
- Output binary field: `final`

### 3.10 YouTube upload — step-by-step

shorty-factory's composer LLM produces three pieces of YouTube-ready metadata in the webhook payload (step 3.7): `youtube_title`, `youtube_description`, and `youtube_tags`. The steps below paste each one into the right slot in n8n's YouTube node.

**Step 1 — Add the node.**
On the canvas, after the `Download final` node (step 3.9), click `+` → search "YouTube" → pick **YouTube** (the official integration).

**Step 2 — Pick the action.**
- **Resource**: `Video`
- **Operation**: `Upload`
- **Credential**: select the YouTube OAuth2 credential from §2.2.

**Step 3 — Top-level fields** (visible immediately after picking the operation):

| n8n field | Paste expression | Notes |
|---|---|---|
| **Title** | `{{ $json.youtube_title \|\| $('Build JobCreate').item.json.idea }}` | LLM-crafted; raw idea as fallback. Composer caps at 60 chars (under YouTube's 100 limit). |
| **Region Code** | `US` (or your audience's region) | Determines algorithm-classification region. |
| **Category Id** | `24` | Entertainment — the right bucket for horror / mythology / fictional shorts. |
| **Binary Property** | `final` | Must match the `Output binary field` set in the Download node (step 3.9). |

**Step 4 — Toggle "Additional Fields" open** (the collapsible section below the top-level fields). This is where Tags, Description, and the privacy/policy flags live.

Click "Add Field" and add each of these one at a time:

| Additional field | Paste expression | Notes |
|---|---|---|
| **Description** | `{{ $json.youtube_description \|\| $('Build JobCreate').item.json.idea }}` | LLM-crafted; already contains niche hashtags. |
| **Tags** | `{{ ($json.youtube_tags \|\| ['shorts', 'pov']).join(', ') }}` | LLM-generated tag array, joined with commas. *(See note below if n8n shows you a different input shape.)* |
| **Privacy Status** | `public` | Or `private` while you're still testing. |
| **Made For Kids** | `false` | Required for any non-children's-content channel. |
| **Notify Subscribers** | `true` | Or `false` for test runs. |

**About the Tags field shape**: n8n's YouTube node version varies on whether `Tags` accepts a comma-separated string or a string array.

- If the field is a **single text input** (most versions): paste the `.join(', ')` expression above. It produces `"filipino mythology, tikbalang, horror short, ..."`.
- If the field shows up as an **"Add Tag" button** (multi-value list): drop the `.join(', ')` and paste:
  ```
  {{ $json.youtube_tags || ['shorts', 'pov'] }}
  ```
  n8n will treat the expression as the array and bind each element as a separate tag.

You can tell which variant you have by clicking on the field — text input vs. an "Add Tag" repeater button. Both work; just match the expression shape.

**Step 5 — Verify.**
After saving the node, click "Execute Step" *only after* an upstream Wait node has resumed with a real `$json` payload (i.e., trigger an actual end-to-end run). The node panel will show the upload's video ID and URL on success.

**About tags vs description hashtags** — these are *different YouTube fields*:

- **Tags** (the metadata field you just configured) — no `#` prefix, lowercase, comma-separated. Used by YouTube's algorithm to classify and recommend; **not visible** to viewers.
- **Hashtags** (embedded in the description, start with `#`) — visible, clickable. The composer already appends 3-5 of these to `youtube_description` automatically.

Both layers help SEO; they're complementary, not redundant. Don't worry about duplicating tags into the description as hashtags — the composer handles each layer separately and the algorithm reads them differently.

**AI disclosure (off by default; flip if a niche drifts realistic)**:

YouTube's "altered or synthetic content" policy targets *realistic* AI content that could mislead. Stylized fictional shorts (mythology, horror) sit outside the policy, so the disclosure flag is **off by default** in this guide.

If you ever need to flip it on — e.g. you start producing realistic AI footage of real-looking people or events — the YouTube node may not expose the field directly. In that case, add an extra **HTTP Request** node after the upload:

- Method: PUT
- URL: `https://www.googleapis.com/youtube/v3/videos?part=status`
- Auth: same YouTube OAuth2 credential
- Body (JSON):
  ```json
  {
    "id": "{{ $json.id }}",
    "status": {
      "containsSyntheticMedia": true
    }
  }
  ```

This patches the `containsSyntheticMedia` flag on the just-uploaded video. The exact field name evolves; verify against the [current YouTube Data API v3 docs](https://developers.google.com/youtube/v3/docs/videos) at implementation time.

**Custom thumbnails (optional, deferred)**:

YouTube auto-generates thumbnails from your video frames. For more control, you can upload a custom thumbnail via the [thumbnails.set endpoint](https://developers.google.com/youtube/v3/docs/thumbnails/set) — but n8n's YouTube node doesn't natively expose this. If you want custom thumbnails later, generate them with another HTTP Request node calling `POST https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=<id>`. Thumbnails are huge for CTR but low priority for v1; stick with auto-generated until you have ~50 uploads to compare.

### 3.11 Notify success

**Telegram** node `Notify success`:
- Credential: Telegram bot (from §2.3)
- Chat ID: your default ops chat ID (saved in §2.3, step 3)
- Text:
  ```
  ✅ {{ $json.youtube_title || $('Build JobCreate').item.json.idea }}
  https://www.youtube.com/watch?v={{ $('YouTube').item.json.id }}
  ```

The notification reaches your phone within a few seconds of the upload completing. Tap the link to review the live video — flip to private from YouTube Studio if it looks bad.

### 3.12 Error path

A **Telegram** node on the IF=false branch (from step 3.8):
- Chat ID: your default ops chat
- Text:
  ```
  ❌ Job {{ $json.job_id }} failed
  Type: {{ $json.error_type }}
  Error: {{ $json.error }}
  ```

The `error_type` field gives you a stable token to route on. Common values:

| `error_type` | What it means | What to do |
|---|---|---|
| `session_expired` | meta.ai cookie invalid | Re-run `scripts/capture_session.py` from your Windows host |
| `rate_limited` | Provider throttled | Wait it out; the next cron run will retry |
| `ui_changed` | Selector didn't match | Edit `META_SELECTORS` in [app/providers/video/meta_ai.py](app/providers/video/meta_ai.py) |
| `quota_exceeded` | Daily quota hit | Wait for the quota window to reset |
| `pipeline` | ffmpeg / IO failure | Check shorty-factory logs |
| `config` | Missing asset / font | Fix env or assets/ |
| `orphaned` | uvicorn restarted mid-job | Cosmetic; ignore unless frequent |
| `unknown` | Anything else | Investigate via LangSmith trace |

If you want differentiated notifications (e.g. session_expired pings a "fix me" chat, rate_limited pings nothing), branch on `{{ $json.error_type }}` with multiple IF nodes.

Also attach an **Error Trigger** node to the workflow (separate sub-flow that fires on any unhandled n8n exception, not just shorty-factory errors) → Telegram with the workflow name and stack.

## 4. First-run testing

Test each trigger path before activating. **Activate the workflow first** — the Wait/Resume node only persists across editor sessions when the workflow is Active (in editor-only "Execute Workflow" mode it dies if you close the tab, leading to spurious 409 callbacks from shorty-factory).

### 4.1 Webhook Trigger (fastest test)

```cmd
curl -X POST http://localhost:5678/webhook/short ^
  -H "X-Trigger-Token: YOUR_SECRET" ^
  -H "Content-Type: application/json" ^
  -d "{\"idea\":\"a Tikbalang lures you into the rainforest at dusk\",\"niche\":\"filipino-mythology\",\"num_scenes\":4}"
```

(`^` is the cmd line-continuation; use `\` on bash.)

What to verify:
- The HTTP response (because Webhook responseMode = "When last node finishes") returns the YouTube video ID.
- The video appears on your channel as Public.
- Title and description match what's in the corresponding `outputs/<job_id>/storyboard.json` (`youtube_title`, `youtube_description`).
- Telegram success message arrives.

### 4.2 Schedule Trigger

Temporarily change the Schedule cron to a near-future time (e.g. 2 minutes from now), wait for it to fire, then revert to your daily time (e.g. 09:00). Verify the same end-to-end flow as 4.1.

### 4.3 Healthz guard

After 4.2 runs successfully, change the cron to fire again immediately. The workflow should hit `/healthz`, see `ran_today=true`, and silently exit without creating a duplicate video. Confirm no second YouTube upload appears.

### 4.4 Concurrency / single-flight check

While a job is in-flight (visible in the n8n Executions tab as "Waiting"), fire the Webhook trigger again. shorty-factory should return HTTP 429 ("another job is currently queued or running"). Your workflow's Create-job HTTP Request node should hit its "Continue On Fail" branch and route to the Telegram error notification with `error_type` showing 429-related text.

### 4.5 Error-type routing (optional but useful)

Temporarily rename `storage_state.json` to break meta.ai auth. Trigger any path. Verify:
- Job fails with `error_type: session_expired`.
- Telegram error message includes the type token.
- Restore `storage_state.json` and re-test to confirm recovery.

## 5. Going live

1. Activate the workflow (toggle in top-right).
2. Watch the first ~3 cron-fired uploads on your channel and in LangSmith. Confirm:
   - YouTube videos are public, no AI disclosure
   - **Title and description match `youtube_title` / `youtube_description` from the storyboard.json** — not the raw idea
   - Hashtags appear at the bottom of the description (composer-generated)
   - Music is MusicGen-generated (no Content ID claims)
   - LangSmith trace shows `compose → generate → stitch → music` end-to-end
   - Telegram success messages arrive with the actual title

3. **YouTube Studio review** for the first batch:
   - Look at YouTube's auto-detected category — does it match what you set in the upload node? Adjust if needed.
   - Check the auto-generated thumbnail. If it's bad, consider adding a thumbnail-set HTTP Request node later.
   - Watch the audience-retention curve: if drop-off is at second 1, your title/description aren't selling the hook; if at second 5+, the video itself isn't holding attention.

4. After ~10 successful uploads, you can stop watching the dashboard daily. Set up a weekly check-in: scroll your YouTube Studio for any policy/Content-ID flags, glance at LangSmith for failures.

## 6. Recurring chores

- **Every 2–4 weeks: re-capture meta.ai session** when you see `MetaSessionExpired` errors or the screenshot-on-timeout shows a login wall. Run `python scripts/capture_session.py` from your Windows host (where the headed browser can pop up), then copy the resulting `storage_state.json` to wherever shorty-factory expects it.
- **Weekly OAuth re-auth** while your YouTube OAuth app is in Testing status. Submit for verification once you trust the pipeline if you want to drop this chore.
- **Monthly disk check** on `outputs/` — startup cleanup keeps it bounded but verify.

## 7. Common gotchas

- **`host.docker.internal` doesn't resolve on Linux Docker** — use `--add-host=host.docker.internal:host-gateway` on the n8n container start command. Already works on Docker Desktop for Windows / Mac out of the box.
- **`PUBLIC_BASE_URL` mismatch** — if n8n is in a container and shorty-factory is on the host, `PUBLIC_BASE_URL=http://localhost:8000` will produce `result_url`s n8n can't fetch. Set it to `http://host.docker.internal:8000` (or your LAN IP).
- **NVIDIA chat completion sometimes returns non-JSON** even with `response_format: json_object` — wrap the parse step in a Try/Catch (n8n: connect to "On Error" branch) and retry once.
- **YouTube quota** — daily quota is 10,000 units, and each upload costs 1,600. You can do ~6 uploads/day before hitting the cap. Plenty for 1/day cadence.
- **Telegram node "Chat not found"** usually means you haven't messaged the bot first. Send `/start` from your account to your bot once.
