# WeChat local workbench

> The "WeChat (local)" section of the messaging screen: your local WeChat's
> history and present, turned into a read-only work surface you can query,
> chart, and export — chat-history query and export, statistics and reports,
> media and Moments, contacts and recovery, new-message notifications, and a
> local-database HTTP API, all six surfaces living on one card. Every byte of
> data comes from the read-only channels of the managed wechat-bridge
> component (decrypted copies + the pixel chain): zero writes to WeChat data,
> zero process interference. The external key-provider protocol is defined in
> [the key provider protocol](../wechat-keyprovider.md) (Chinese), the full
> HTTP contract in [the local-database HTTP API](../wechat-http-api.md)
> (Chinese), and media decryption in
> [the media decrypt provider protocol](../wechat-mediadecrypt.md) (Chinese).

## 1. One-card overview

The card header carries a three-state probe pill (available / degraded /
unavailable) plus a version line (WeChat version / bridge component version,
monospaced); degraded and unavailable states list every reason code with its
fix hint (missing keys, WeChat not running, …). The six surfaces:

| Surface | What it does | Data source |
|---|---|---|
| Chat history | Live session read, local-database query, paged browsing, six-format export | Current-window pixel chain + decrypted copies |
| Statistics and reports | Private / group / annual / duo report kinds plus the AI report | Aggregates over decrypted copies |
| Media and Moments | Chat image thumbnails; Moments timeline and export | Local media files (via the decrypt provider) + decrypted copies |
| Contacts and recovery | Five-bucket browsing, search, "former friends" archaeology | Decrypted copies |
| New-message notifications | Minute-scale polling for new messages and recalls; system notifications | Decrypted copies (category form) |
| Local-database HTTP API | Serves the read-only surface over local HTTP | Decrypted copies |

Everything the workbench reads back is displayed in its panels only — it never
enters the intelligence pipeline (to get there, declare a category explicitly;
see section 7). Sending is not part of this workbench (outbound delivery rides
the `weixin-local` channel; conventions in the
[plugin guide](write-a-plugin.md)).

## 2. Prerequisites

### 2.1 Bridge component and system dependencies

Every capability of the workbench calls the managed wechat-bridge component as
a one-shot subprocess. Install it under **Settings → Python environment →
components**: the "WeChat local bridge (wechat-bridge)" one-click install
(depends on pyobjc + pillow). The local-database channel additionally needs
`brew install sqlcipher` (without it the decrypt step reports a structured
guide — never a silent half-install). The card header's "Refresh status"
re-runs the probe at any time.

### 2.2 Database keys (the key provider)

Local-database queries, statistics, Moments, and contacts all read
**decrypted copies** of the SQLCipher-encrypted databases, and the keys live
nowhere in MYIA product code — key acquisition is outsourced entirely to your
own key provider, per [the key provider protocol](../wechat-keyprovider.md).
The "WeChat account" card in Settings offers three pieces:

- **Database key status row**: a three-state badge (ready, N databases /
  stale / missing) plus when the keys were acquired;
- **Acquire / re-acquire buttons**: run the interactive acquisition (a system
  password prompt appears, and you are asked to log out of WeChat and scan the
  QR code again; the whole flow is read-only, WeChat data is never modified);
  "re-acquire" from the ready state overwrites old keys only after the new
  ones verify;
- **Key provider row**: saves the path to your provider executable; when
  unconfigured, a **one-click install** deploys the official reference
  provider (into the data root's private `wechat-localdb/bin/` and registers
  it as the provider path).

Only keys that verify in full land in the key cache `keys.json` (data-root
private area, mode 600). After switching WeChat accounts the old keys match
nothing: the bridge reports `key_unavailable` honestly and points you at
re-acquisition. CLI equivalents:

```console
wechat-bridge localdb key-status --json    # three-state source of truth: ready | stale | missing
wechat-bridge localdb key-acquire --json   # interactive acquisition (short-circuits when ready without --force)
```

### 2.3 Screen-recording permission

The "read-only session reader" reads the screen content of the **currently
open conversation** (window capture + OCR pixel chain), which needs the macOS
"Screen Recording" permission granted to the MYIA app itself: open System
Settings → Privacy & Security → Screen Recording, enable MYIA, then come back
and hit "Refresh status". The pixel chain never steals foreground focus,
never synthesizes clicks, and never activates the WeChat window.

## 3. Chat history: query, paging, and export

### 3.1 Read-only session reader (current window)

"Load sessions" → pick a conversation → the most recent messages render
(timestamp / sender / type badge / body; non-text messages show a "type +
placeholder" note). This reads only what the current window has loaded — for
history, use the local-database query below; if the session list comes back
empty, first make sure the main WeChat window is open.

### 3.2 Local-database query and paged browsing

- **Query row**: a chat id (e.g. `filehelper`) plus a day count (default 30)
  → a joint query across the three domains (message / session / contact);
  the display name and per-domain status badges are shown as they are — a
  missing domain is never dressed up as success;
- **Paged browsing**: window quick-picks (last 7 days / last 30 days / all) →
  "Load latest page", oldest on top; "Load earlier" continues at the head via
  a keyset cursor instead of re-flipping old pages.

With keys not ready the query reports `key_unavailable` (with a fix hint) —
never a silent degrade.

### 3.3 Six-format export

"Export this conversation": format dropdown + "Choose a directory and export"
(the shell opens a directory dialog; cancelling quietly stands down) → a
progress percentage you can cancel → a receipt listing the output paths
(clickable to reveal in Finder). The six formats:

| Format | What it is |
|---|---|
| JSON file | structured data |
| Plain text | `.txt` |
| CSV | tabular rows |
| Markdown | a document |
| HTML page | a web page |
| Excel workbook | `.xlsx` |

The receipt carries a manifest reconciliation (file name / path / row count /
sha256 / total rows / time range / elapsed). CLI equivalent (stdout streams
NDJSON progress):

```console
python -m wechat_bridge localdb export <chat> \
  --format json|txt|csv|md|html|xlsx --out <dir> [--all]
```

## 4. Statistics and reports

### 4.1 Four report kinds

Pick a report kind → pick a conversation (private and duo pick a friend,
group analysis picks an `@chatroom`, annual skips this and picks a year) →
pick a window (last 30 days / last year / all) → "Generate":

| Report | What you get |
|---|---|
| Private-chat analysis | total / sent / received messages, longest daily streak, session count with initiators, average response time, 24-hour bars, message-type distribution (text / image / voice / video / sticker / file-link / location / other / system / recall), weekday distribution, top phrases |
| Group analysis | everything from private chats + top-10 speakers |
| Annual report | total (private) messages / people chatted with / late-night (0-5:59) messages and the biggest night owl / top-10 chats / top phrases / hourly bars |
| Duo report | side-by-side columns for you and them (message count / topics opened / average reply / top phrases), active days, the very first message, a 7×24 heatmap |

Bars and the heatmap are pure CSS — zero chart libraries. Results carry a
cache hint: the first run is slow, later ones come back in seconds (a
bridge-side file cache; hits show the elapsed time).

### 4.2 AI report and export

"Generate AI report" feeds the **aggregates the panel already holds** back
through the report generator (the bridge does not re-query; the entry point
defensively strips per-message text, so the model sees aggregate numbers and
top phrases only — never message bodies). The report renders as four chapters
(overview / hour-of-day profile / relationship insights / closing) and names
the model and token cost. The endpoint lives in the `stats_report` section of
`<data root>/config/wechat-stats.yaml`: off by default — unenabled raises
`ai_disabled` with a configuration hint, and a missing openai client raises
`dependency_missing` with an install hint. "Export" lands same-named `.json`
+ `.md` files through one save dialog.

## 5. Media and Moments

### 5.1 Chat images (the decrypt provider)

Chat id + days → "Scan media": the in-window image / video inventory with
live thumbnails. Image thumbnails are decrypted by **your media decrypt
provider** (encrypted `.dat` → decrypt cache, idempotent, never re-decrypts);
videos show the plaintext `_thumb.jpg`; the WeChat live-photo format (wxgf)
is labeled honestly as "live format, preview not supported yet". Clicking an
item expands the details (media key, source file paths, decrypt-cache path,
in monospace). The stats line counts as it is (messages / images / videos /
other / missing files / decrypted). With no provider configured the media
surface degrades structurally (exit 23; the text surface is unaffected) —
protocol in [the media decrypt provider protocol](../wechat-mediadecrypt.md).

### 5.2 Moments timeline and export

Days + "Load Moments" → a read-only timeline (poster / time / text / media
badges "images ×n / videos ×n"; reposts credit the source). Export in two
formats, "web page (HTML)" or "data (JSON)", lands in a local private
directory (the receipt shows the directory and file count). Media download is
off by default; enabling it on the CLI is the bridge's only constrained
network exemption (GET only, every URL comes from the database):

```console
python -m wechat_bridge localdb sns export --format html --since 30d --with-media
python -m wechat_bridge localdb media scan <chat> --since 30d --decrypt --json
```

## 6. Contacts and "former friends"

### 6.1 Five-bucket browsing and search

The count-chip row (friends / groups / official accounts / former friends /
other; the numbers are live query values, never hardcoded) switches buckets;
the search box filters by remark / nickname / alias / WeChat ID / pinyin
(200ms debounce; clearing restores the first page); the list pages 100 rows
at a time with a cursor-driven "Load more"; group rows carry a member-count
badge.

### 6.2 The archaeology view

Only the "former friends" chip enters it, under a permanent declaration:
recovered from local-database residue — **completeness and accuracy are not
guaranteed**, entries are tiered by evidence, and stranger caches from group
chats may be included. Per-row evidence badges come in two tiers ("conversation
traces" / "identity-field residue"), with tier counts alongside (with
conversation traces / contact-table residue only). Companion sub-cards: the
**non-friend contact cache** (contact-like records never admitted to the
normal contact set) and **group-application records** (group verification /
join requests, presented read-only from the source table). The archaeology
query decrypts the message-database copy on demand (a large database; the
first run shows an honest "digging…" wait). The labels footnote gives only a
count of database labels — label-to-contact attribution lives inside the
encrypted buffer and is never fabricated per person.

### 6.3 Three-format export

Tick JSON file / CSV spreadsheet / address-book VCF → "Export current
filter" → the save dialog picks a base name (cancel is silent) → the receipt
lists total rows and each file's path.

## 7. New-message notifications and recalls

### 7.1 Category form (weixin-notify)

Notifications are not a panel polling in the UI — they are a standard
category source: the repo ships the template
`config/weixin.example.yaml`(随源码发布,本门面仓不含) (the
`notify` channel of the `wechat_local` engine; since the 2026-10-20
four-files-into-one merge, WeChat is a **single file, multiple channels** —
sources across pixel/localdb/sns/notify live together in one `weixin`
category sharing one schedule, with the notify-channel sample preserved as
a commented template in that file's sample area; copy it to
`<data root>/config/weixin.yaml` to activate). The file being present does
not mean it runs — register a cron job (desktop scheduling or `myssia cron`, see
[Scheduled jobs (cron)](cron.md)) and it fetches on the minute-scale
schedule; the factory `chat_whitelist` is an empty list, i.e. off by default
(the engine refuses at construction, preventing a no-op). An enabled example
(fill in your target conversations):

```yaml
id: weixin-notify-demo
name: WeChat notifications (demo)
schedule: "*/1 * * * *"            # minute-scale polling (notify-scan is always light; loosen as you like)
timezone: Asia/Shanghai
timeouts:
  fetch: 120
sources:
  - name: weixin-notify            # source name must carry the weixin- prefix (feed channel auto-detection)
    engine: wechat_local
    url: "https://weixin.qq.com"   # shape anchor only; the engine does zero HTTP — the endpoint is the local bridge
    empty_ok: true                 # key-missing skip / quiet-window zero events = a legal empty
    engine_options:
      wechat_local:
        channel: notify            # the notify channel: polling watermark, zero window capture, zero AX
        chat_whitelist:
          - "50438708676@chatroom" # a chat id passes straight through
          - Zhang San              # a display name: the engine resolves it to an id
        notify_self: false         # no notification for your own messages (default)
        read_timeout_seconds: 120  # per-scan bridge timeout (must be ≤ timeouts.fetch)
    rate_limit: {qps: 0.5, jitter: "2s", backoff: exponential, respect_robots: true}
    proxy: direct                  # zero HTTP; a local read-back
    retry: 1
watchlist:
  keywords: []
  mute: []
classify:
  builtin: false
  rules: []
dedup:
  key: "{url}"                     # #wxn-/#wxnr- server_id strong anchors; re-reads stay silent
enrich:
  enabled: false                   # red line: private content never leaves this machine (no LLM)
push:
  - channel: macos_notify          # local notification-center banners, zero network
storage:
  retention: 30d                   # privacy-cautious default
  vacuum: monthly
```

`chat_whitelist` takes chat ids (`wxid_xxx` / `xxx@chatroom` pass through) or
display names; **not declaring it means no notifications at all** (zero scans
outside the whitelist). The key is named `chat_whitelist`, not `sessions` —
the schema's credential baseline treats any session-bearing key as a
credential slot, and declaring `sessions` is structurally refused with a
pointer to the right name. Field semantics live in the
[schema reference](schema.md); category authoring in the
[plugin guide](write-a-plugin.md).

### 7.2 Polling watermark and recall recovery

Each round makes one bridge `localdb notify-scan` call (whitelist and
watermark ride stdin JSON; argv carries zero content): the session-baseline
watermark filters first (whitelisted conversations compare `{ts, unread}`) →
only candidate conversations get a targeted incremental query →
`message.new` events; the recall predicate (`local_type` 10002 / 10000 plus
a content marker) → `message.revoke` events, with original-text recovery on
a ladder: server_id direct lookup → time-neighborhood heuristics →
replacemsg text fallback → not recovered (three honest states, no invented
content). The first round only builds the baseline and emits nothing
(preventing a first-install barrage). Item anchors `#wxn-<server_id>` /
`#wxnr-<server_id>` are strongly idempotent — re-reads stay silent. Missing
keys mean the round skips without counting as failure, and the watermark
does not advance.

### 7.3 Notification delivery (macos_notify)

Delivery rides the `macos_notify` channel: local notification-center banners
with **zero credentials, zero endpoints, zero network** (notification bodies
never touch logs or the network). Merge discipline: new messages in the same
round from the same conversation collapse into one notification (title =
conversation name, subtitle = N new messages, body = the latest one as
"sender: preview", body capped at 200 characters, item titles at 80). Recall
events stand alone with three-state copy (recovered original / replacemsg
fallback / not recovered). While the desktop app is running it delivers as
the app itself (icon / grouping / click-to-front belong to MYIA); from the
CLI or without the shell, osascript fires directly (grouped under
"osascript"). The system notification center keeps notification history (a
local fact of life). Items also land in the local store + feed; `enrich` is
off and `watchlist` / `mute` empty — private content never leaves the
machine and never reaches an LLM. Recall history per conversation also has a
CLI:

```text
wechat-bridge localdb revoke-list <chat> --since 7d
```

## 8. Local-database HTTP API

### 8.1 Starting the server and auth

The bridge's localdb read-only surface served as a resident local HTTP
service for external systems, automation scripts, and LLM tooling (strictly
read-only):

```console
python -m wechat_bridge localdb http-token generate --json   # generate a token (rotates on regenerate)
python -m wechat_bridge localdb serve --json                 # foreground resident, 127.0.0.1:5032
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:5032/api/v1/health
```

An unconfigured token means every non-health request gets 401 (no
unprotected runs); `generate` / `clear` take effect immediately without a
restart (the config is re-read per request). Loopback is enforced: the server
binds only `127.0.0.1 | localhost | ::1`, and configuring a non-loopback
value such as `0.0.0.0` is refused at startup — exposing decrypted chat data
outward is a red line. The default port is 5032 (deliberately clear of the
chatlog family of tools).

### 8.2 Endpoints and the event stream

The read-only endpoint family (GET / POST unified; POST body params merge
into query params):

| Endpoint | Purpose |
|---|---|
| `/api/v1/health` | health and key tri-state (token-exempt) |
| `/api/v1/sessions` | session list |
| `/api/v1/messages` | time-window message query |
| `/api/v1/sessions/{chat_id}/messages` | ChatLab Pull watermark incremental fetch |
| `/api/v1/group-info` | group info (missing tables degrade to an empty structure) |
| `/api/v1/sns` | Moments feed / interaction notices |
| `/api/v1/media/<rel>` | media file delivery from the allowlisted directory |
| `/api/v1/push/messages` | SSE event stream (`message.new` / `message.revoke`) |

SSE: a 25s heartbeat; reconnecting with `Last-Event-ID` replays from a ring
buffer of ≤1000 events / 10 minutes. Event latency is on the order of the
scan interval (minutes; one scan per 60s by default) — for finer granularity
poll the Pull endpoint by watermark. Bridge error families map to 503 with
the same code names as the CLI (`key_unavailable` / `db_not_found` /
`decrypt_failed`). The full contract, the three token channels, and the
media-allowlist guards live in
[the local-database HTTP API](../wechat-http-api.md).

## 9. Read-only red lines

- **Zero writes to WeChat data**: every query reads decrypted copies in the
  data root's private area (modes 600 / 700; 24h TTL plus a capacity cap by
  default, rebuilt only when the source database updates) — WeChat-side
  files are never written.
- **Zero process interference (read chain)**: the pixel chain never steals
  focus, never synthesizes clicks, never activates the WeChat window; the
  notification scan uses zero window capture and zero AX, reading decrypted
  copies only.
- **Keys never leave the data root's private area**: `keys.json` is mode
  600; key acquisition and media decryption are outsourced wholesale
  (protocols in [the key provider](../wechat-keyprovider.md) and
  [the media decrypt provider](../wechat-mediadecrypt.md)) — MYIA product
  code contains zero key-acquisition and zero decryption implementations.
- **The one constrained network exemption**: Moments media origin fetch
  (`--with-media`, off by default; GET only, every URL from the database);
  every other bridge path makes zero network requests.
- **Read-backs never enter the intelligence pipeline**: workbench panels are
  display-only — no store, no dedup, no classification; the only road into
  the pipeline is an explicitly declared category (section 7).
- **Honest degradation**: missing keys `key_unavailable`, missing database
  `db_not_found`, failed decryption `decrypt_failed` — structured errors
  with fix hints, never a silent fake success.
