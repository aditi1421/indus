# nyaya-clerk

A WhatsApp-native clerk for the firm: post a question in the firm's WhatsApp
group and it answers from live court cause lists, the firm's case register,
and Zoho Invoice — plus a daily 07:00 IST digest of matters listed that day.

## Architecture

```
firm WhatsApp group
      │  (whatsmeow, group-only)
      ▼
gateway (Go)  ──POST /chat──▶  server.py (FastAPI, :8600)
      ▲                              │
      └────────POST /send───────────┘  agent.py (openai-agents, gpt-4o)
     (:8601)                                │
                                    skills.py (function tools)
                                   ┌─────────┼──────────────┐
                              causelists.py  cases.py     zoho.py
                              (sc/dhc/mhc)  (firm sheet)  (invoice drafts)
                                   │
                            browser.py (browser-use fallback
                            for scrapers that break)
```

- **gateway** (`gateway/`, Go, [whatsmeow](https://github.com/tulir/whatsmeow)) links to WhatsApp
  as a linked device, watches the firm's group only, and forwards messages to the agent's
  `/chat` endpoint. It also runs a small `/send` HTTP API (default `127.0.0.1:8601`) that the
  digest job posts to.
- **server.py** is a FastAPI app on port 8600. `/chat` takes `{chat, sender, text}`, rejects
  anything not from the firm group, and calls `agent.ask_full`. `/health` returns `{"ok": true}`.
- **agent.py** wraps an `openai-agents` agent (model `gpt-4o`) wired up with the tools in
  `skills.py`.
- **skills.py** exposes the tools the agent can call:
  - **Cause lists** (`causelists.py`) — `sc` (Supreme Court), `dhc` (Delhi High Court), `mhc`
    (Meghalaya High Court). `mhc` goes through a reverse-engineered eCourts flow (no stable
    direct index page was found for it).
  - **Firm file register** — a Google Sheet (`cases.py`) with columns `FILE`, `DEPARTMENT`,
    `RECEIPT DATE`, `REMARKS`, `ASSIGNED`, `STATUS` (column names/aliases vary a bit across
    real sheets; `cases.py` maps them onto a canonical schema).
  - **Zoho Invoice** (`zoho.py`) — **draft-only**. The agent can look up customers and create
    draft invoices; emailing invoices was deliberately removed from the tool surface. The firm
    reviews and finalizes/sends drafts from the Zoho dashboard.
  - **browser-use fallback** (`browser.py`) — when a court's scraper breaks, an
    `AsyncBrowserUse` session can be used instead of the brittle scraper.
- **digest.py** — a one-shot job: at 07:00 IST it looks up today's firm matters, formats them
  in one message (capped at 25 matters, with a "…and N more" line beyond that), and posts it
  to the gateway's `/send` API as a single WhatsApp group message. Run via a systemd timer.

## SSM parameters (region: `ap-south-1`, Mumbai)

All config is pulled from AWS SSM Parameter Store at startup (`config.py`). Store secrets as
`SecureString`.

| Parameter | Notes |
|---|---|
| `/apps/bucket` | S3 bucket name |
| `/core/openai/key_openai` | OpenAI API key (agent model + skills) |
| `/core/google/key_gemini` | unused currently, kept for parity with other projects |
| `/apps/courts/sheet_indus` | Google Sheet ID/URL for the firm file register |
| `/apps/courts/key_indus` | unused |
| `/apps/courts/key_browser_use` | browser-use API key (fallback scraping) |
| `/apps/courts/zoho_client_id` | Zoho OAuth client id |
| `/apps/courts/zoho_client_secret` | Zoho OAuth client secret |
| `/apps/courts/zoho_refresh` | Zoho OAuth refresh token |
| `/apps/courts/zoho_org` | Zoho Invoice organization id |
| `/apps/courts/whatsapp_group` | the firm group's JID, e.g. `120363xxxxxxxxxx@g.us` |
| `/apps/courts/whatsapp_bot_number` | reference only — no code reads this; it records the number to pass to `./gateway -paircode <number>` when (re)linking |

## Local dev

```bash
.venv/bin/python chat.py     # local REPL against the agent, no gateway/WhatsApp needed
.venv/bin/pytest             # 41 passed, 1 deselected (network tests are opt-in: -m network)
```

## EC2 deployment

Target: a small instance (e.g. `t4g.small`, arm64) in **`ap-south-1`**, with an IAM instance
role allowing `ssm:GetParameter`/`ssm:GetParameters` on `/apps/courts/*` and `/core/*`, plus S3
access to the bucket in `/apps/bucket`.

1. Launch the instance (Ubuntu 24.04) in `ap-south-1` with that IAM role attached.
2. Clone this repo to `~/agent` on the instance.
3. Copy the helper packages the code imports (`aides`, `wraps`) onto the box:
   ```bash
   scp -r ../packages ubuntu@<host>:~/packages
   ```
4. Run the setup script on the instance:
   ```bash
   cd ~/agent && bash deploy/setup.sh
   ```
   This installs system deps, creates `.venv`, installs `requirements.txt`, points the venv at
   `~/packages/aides` and `~/packages/wraps` (see note below), builds the Go gateway, writes
   `gateway/gateway.env` from SSM, installs the systemd units, and starts everything.
5. Link WhatsApp — two options:
   - **QR scan**: `journalctl -fu nyaya-gateway` and scan the code with the firm's WhatsApp
     (Settings > Linked Devices).
   - **Remote pairing code** (no QR/screen needed): stop the service, run
     `./gateway/gateway -paircode <bot-number>` (an 8-character code is printed; enter it on
     the phone under Settings > Linked Devices > Link with phone number instead — the code is
     only valid for a few minutes), then restart `nyaya-gateway` once linked.
6. Verify:
   ```bash
   curl -s localhost:8600/health          # {"ok":true}
   systemctl status nyaya-gateway         # active (running)
   systemctl list-timers | grep digest    # next run 07:00 IST
   ```
   Then in the firm group: "What matters do we have in the Supreme Court tomorrow?" should get
   a grounded answer.

### Note: why setup.sh doesn't `pip install -e` the helper packages

`aides`'s (and `wraps`'s) `pyproject.toml` declares `requires-python = ">=3.14.2"`, even though
the code runs fine on the distro python3 (3.12 on Ubuntu 24.04). `pip install -e` refuses to
install it under that version because of that constraint. Instead, `setup.sh` writes a `.pth`
path file into the venv's `site-packages` pointing at `~/packages/aides` and `~/packages/wraps`
directly, so they import normally without going through pip's version check.

### Changing the WhatsApp group

1. On the instance: `./gateway/gateway -listgroups` (with the service stopped, or from a
   separate session) to print `JID | name` for every group the linked number has joined.
2. Update the `/apps/courts/whatsapp_group` SSM parameter (region `ap-south-1`) to the new JID.
3. Re-run `deploy/setup.sh` (or just rewrite `gateway/gateway.env` and
   `sudo systemctl restart nyaya-gateway`).

### Adding a court

Add a new `Court(...)` entry to `COURTS` in `causelists.py` (key, display name, index URL, and
the date-format patterns tried against that court's listing page/PDF links).

## Caveats

- **whatsmeow / WhatsApp ToS risk**: this connects as an unofficial linked device, which is
  against WhatsApp's terms of service and carries a ban risk for the linked number. Mitigate by
  keeping it scoped to a single group and pacing outbound sends (the gateway serializes and
  paces `/send` calls) — don't add more groups or high-volume broadcasting.
- Zoho invoices created by the agent are **drafts only**; nothing is emailed automatically.
- The `mhc` (Meghalaya High Court) cause list goes through a reverse-engineered, session-gated
  eCourts flow rather than a stable public page, so it's the most likely of the three court
  integrations to break if the upstream site changes.
