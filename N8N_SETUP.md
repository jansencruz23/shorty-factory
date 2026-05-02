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

#### Telegram Trigger
- Credential: your Telegram bot.
- Updates: `message`.
- Additional Fields → Restrict to chat IDs: paste your chat ID so randoms can't fire it.
- The output's `message.text` looks like `/short some optional idea`. Parse it in the next node.

Add a **Set** node right after, only on the Telegram branch, named `Parse Telegram`:
- Mode: Manual mapping → Add value of type `String` named `idea`, expression: `{{ $json.message.text.replace(/^\/short\s*/, '') || '' }}` — empty string falls through to the LLM.
- Add `niche` = `''` (Telegram triggers don't currently specify niche; LLM picks).
- Add `_chat_id` = `{{ $json.message.chat.id }}` so the success notification replies to the same chat.

#### Webhook Trigger
- HTTP Method: `POST`
- Path: `short`
- Authentication: **Header Auth** with a credential containing a long random secret (e.g. header `X-Trigger-Token`, value any 40-char hex). Keeps openclaw or anything else from being callable without the token.
- Response Mode: **When last node finishes** so the caller sees the final result.
- Expected body: `{"idea": "...optional...", "niche": "...optional...", "num_scenes": 4}` — all optional.

Tell whatever "openclaw" turns out to be to POST to `http://your-host:5678/webhook/short` with the header `X-Trigger-Token: <secret>` and the JSON body.

### 3.2 Merge

A **Merge** node, mode: **Append** (or **Combine → Append**), with three inputs (one per trigger). All three branches now feed the same downstream pipeline.

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

(Telegram and Webhook branches go directly to Merge; they always run.)

### 3.4 Idea LLM (skip when trigger supplied one)

After Merge, an **IF** node `Has idea?`:
- Condition: `{{ $json.idea }}` is not empty.
- True branch: skip LLM, go to `Set: JobCreate`.
- False branch: call NVIDIA.

False-branch node: **HTTP Request** named `Generate idea`:
- Method: POST
- URL: `https://integrate.api.nvidia.com/v1/chat/completions`
- Auth: NVIDIA Build credential.
- Headers: `Content-Type: application/json`
- Body (JSON):
  ```json
  {
    "model": "meta/llama-3.3-70b-instruct",
    "messages": [
      {"role": "system", "content": "You write one-line video ideas for a short-form vertical AI shorts channel. Respond with strict JSON: {\"idea\": \"...\", \"niche\": \"filipino-mythology|cosmic-horror|cinematic\"}. The idea is a single declarative sentence describing a visual scene. The niche must be one of the three values."},
      {"role": "user", "content": "Generate today's idea. Vary the niche from previous days."}
    ],
    "temperature": 0.9,
    "response_format": {"type": "json_object"}
  }
  ```
- After this node, a **Set** node parses `choices[0].message.content` as JSON and pulls out `idea` and `niche`.

### 3.5 Set: JobCreate body

A **Set** node `Build JobCreate`:
- `idea`: `{{ $json.idea }}`
- `niche`: `{{ $json.niche }}`
- `num_scenes`: `4` (or `{{ $json.num_scenes ?? 4 }}` if the webhook supplied one)
- `webhook_url`: `{{ $env.WEBHOOK_URL }}webhook/shorty-done/{{ $execution.id }}` — every workflow execution gets a unique callback URL.
- Pass through `_chat_id` from the Telegram branch if present.

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
- Path: `shorty-done/{{ $execution.id }}` — must match the path in `webhook_url` from step 3.5.
- HTTP Method: POST
- Resume timeout: 4 hours (so a hung meta.ai run doesn't pin the workflow forever).

shorty-factory's `runner.post_webhook` will POST `{job_id, status, result_url|error}` to this URL when the job finishes.

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

### 3.10 YouTube upload

**YouTube** node, action **Upload Video**:
- Credential: your YouTube OAuth2 credential
- Title: `{{ $('Build JobCreate').item.json.idea | truncate(95) }}` — YouTube title cap is 100 chars.
- Description: `{{ $('Build JobCreate').item.json.idea }}\n\nGenerated by shorty-factory.` (no AI-disclosure stanza; per your decision)
- Privacy Status: `public` (per your decision)
- Made For Kids: `false`
- Tags: comma-separated from the niche.
- Binary Property: `final`
- Notify Subscribers: your call.

(If you decide to flip on AI disclosure later, expand "Additional Fields" → look for "Contains Synthetic Media" / "Self Declared Made For Kids". The exact field name on the YouTube node depends on n8n version. If absent, replace this node with a raw **HTTP Request** to `POST https://www.googleapis.com/youtube/v3/videos?part=status` after the upload to set `status.containsSyntheticMedia = true`.)

### 3.11 Notify success

**Telegram** node `Notify success`:
- Credential: Telegram bot
- Chat ID: `{{ $('Build JobCreate').item.json._chat_id ?? 'YOUR_DEFAULT_CHAT_ID' }}` — replies to the Telegram triggerer if the workflow was started by Telegram, otherwise hits your default ops chat.
- Text: `✅ {{ $('Build JobCreate').item.json.idea }}\nhttps://www.youtube.com/watch?v={{ $('YouTube').item.json.id }}`

### 3.12 Error path

A **Telegram** node on the IF false branch:
- Text: `❌ Job {{ $json.job_id }} failed at {{ $json.stage ?? 'unknown' }}: {{ $json.error }}`
- Chat ID: same default ops chat.

Also attach an **Error Trigger** node to the workflow (separate sub-flow that fires on any unhandled error) → Telegram with the workflow name and stack.

## 4. First-run testing

Run each trigger path once, in this order:

### 4.1 Schedule Trigger
In the editor, "Execute Node" on the Schedule Trigger. Walk through each node in the canvas, verifying outputs at each step. Make sure the YouTube upload lands on your channel (private at first if you want extra caution; per your plan it's public).

### 4.2 Telegram Trigger
Send `/short an astronaut sees something impossible` to your bot. The same workflow run should fire. Verify the Telegram chat gets a success message with the YouTube URL.

### 4.3 Webhook Trigger
```bash
curl -X POST http://localhost:5678/webhook/short \
  -H "X-Trigger-Token: YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"idea":"a Hello Kitty that scares children","niche":"cosmic-horror","num_scenes":4}'
```

The HTTP response (because Webhook responseMode = "When last node finishes") should be the YouTube ID/URL.

### 4.4 Concurrency check
Fire Schedule and Telegram in quick succession. The second one should hit shorty-factory's `429` (single-flight guard) and the workflow should fail at the Create-job step with a "Continue On Fail" branch — verify the error Telegram fires correctly.

## 5. Going live

1. Activate the workflow (toggle in top-right).
2. Watch the first ~3 cron-fired uploads on your channel and in LangSmith. Confirm:
   - YouTube videos are public, no AI disclosure
   - Music is MusicGen-generated (no Content ID claims)
   - LangSmith trace shows `compose → generate → stitch → music` end-to-end
   - Telegram success messages arrive
3. After ~10 successful uploads, you can stop watching the dashboard daily.

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
