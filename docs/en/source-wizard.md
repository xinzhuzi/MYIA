# Source wizard

> The **New category** button on the desktop **Sources** screen (源管理) opens a
> five-step wizard: fill in a form, try one fetch, and land a valid category
> YAML. It is for the user who sees a web list worth monitoring but does not
> want to learn YAML. The output is shaped exactly like a hand-written config
> (field-by-field semantics in the [schema reference](schema.md)); the
> full-power authoring workflow lives in [Write a Plugin](write-a-plugin.md);
> desktop app installation in [Getting Started](getting-started.md).

## What it is, and who it is for

- **In one sentence: a form wizard that generates YAML.** Five steps (basics →
  source info → extraction rules → preview & save → test run) produce
  `config/<id>.yaml`: wizard-generate → schema-validate → save → one real
  fetch to verify, all inside the screen.
- **Fits**: zero YAML knowledge, monitoring a server-rendered plain web list
  (the test: right-click → view page source in a browser and the list content
  is visible there); also veterans bootstrapping fast — generate the
  collection skeleton, then jump to the **YAML config** screen (YAML配置) to
  add watchlist / push.
- **Does not fit** (the wizard says so and points to the YAML config screen):
  sources behind login, scroll-loading or JSON APIs; RSS; POST; multi-source
  categories; advanced source knobs such as rate limiting and proxies — the
  full value surface is the `sources` section of the
  [schema reference](schema.md).
- **Shape**: a multi-step dialog inside the Sources screen (no new route, no
  sidebar change); the wizard header carries an "edit YAML directly" link
  deep-linking to the YAML config screen.

## The five steps, one by one

### Step 1: Basics

Answers "what is this config called, and how often does it fetch":

| Field | What to fill |
|---|---|
| Category id | Also the file name: lowercase letters/digits/`-`/`_`, starting with a letter or digit, 1-64 chars. The saved file is `config/<id>.yaml`; after saving the id is locked — restarting with a new id is the only way to change it. |
| Category name | Display name, free-form (Chinese fine), up to 64 chars. |
| Fetch frequency | Four presets (daily at 10:00 / hourly / every 6 hours / Mondays at 9:00) or a custom five-field cron — left to right "minute hour day month weekday", e.g. `0 9 * * *` = daily at 9. |

- **Same-name precheck**: while you type the id, the wizard checks the config
  directory live; an existing `<id>.yaml` draws a warning ("saving will be
  refused; pick another id, or edit the existing file in the YAML config
  screen"). The precheck does not block typing — the backend check at save
  time is the final gate.
- The id rule is one and the same for the CLI and the YAML config screen (one
  shared regex); there is no "the wizard accepts it, elsewhere refuses" split.

### Step 2: Source info

- **Source name**: the default `main` just works (a category can gain more
  sources later; the wizard builds the first one).
- **Page URL**: a full URL starting with http:// or https://.
- **Fetch method**: fixed to "read the page source" (the `static_html`
  engine), for server-rendered plain web lists; other engines are not exposed
  in V1 — finish the wizard and hand-write them in the YAML config screen.
- **Paging** (default off; only when the list spans more than one page):
  - no paging: fetch the first page only;
  - page number in the URL: the address must contain the `{page}` placeholder
    (replaced by successive page numbers while fetching), plus "max pages"
    (default 5, 1-10000);
  - follow a "next page" link: the CSS selector of the next-page button
    (copied the same way as step 3), plus the same max-pages field.
- **User-Agent (optional)**: pre-filled with a common Chrome string; clearing
  it = the request carries no such header.

### Step 3: Extraction rules

In plain words: "first frame the repeating whole row on the page (one product
row, say), then say which part of each row is the title and which is the
link."

- **Item selector (item)**: one CSS selector. Where to copy it: in the
  browser, right-click the element → Inspect → right-click the highlighted
  node → Copy → Copy selector, then paste it here.
- **Fields (what to take from each row)**: rows can be added and removed; the
  `title` (title) and `url` (link) rows are pre-seeded; the "take the link
  address" toggle on the `url` row grabs the link target instead of the text
  (emitting the `h3 a@href` form). Field names must start with an English
  letter, followed by letters/digits/`_`/`-`.
- **Gates** (must pass before Next): item required; at least one field; the
  `url` field is mandatory (each item's link — the dedup foundation; without
  it the config refuses to load); duplicate field names are blocked.

### Step 4: Preview and save

- The top shows the exact YAML about to be written (read-only preview), with
  a plain summary underneath (category / frequency / URL / paging / field
  count).
- **The save order is pinned**: a schema-validation dry run first — any
  error-level finding stays on this step and is shown as-is, and nothing is
  saved; only a clean pass writes the file. The first save creates the file;
  later saves after "back to edit" carry an optimistic lock (a file changed
  in another window is refused, never silently overwritten), and every
  re-save leaves a `.bak` backup.
- When save is refused, the error code shows together with one plain-language
  line; common ones: `mtime_conflict` (the file changed in another window —
  close the wizard and compare in the YAML config screen, or restart with a
  new id), `duplicate_category_id` (the id is taken by another category
  file), `invalid_file_stem` (bad file name), `category_invalid` (validation
  failed; nothing was written).

### Step 5: Test run (try one fetch)

- After a successful save the wizard **fetches once for real** (60-second
  timeout) and embeds the summary: how many items came back, which engine
  actually ran, and a field-value table of the first five samples — seeing
  the real titles and links is how you trust the config. "Details" opens the
  same test-result dialog the Sources screen uses.
- Not happy → "back to edit" returns to step 3 (the form is fully retained);
  re-save (`.bak` kept automatically) and test again, until it is right.
- Happy → the done panel: the saved path, a "complete push and the rest"
  deep-link into the YAML config screen with this file preselected, and
  "finish" which closes the wizard and refreshes the sources list.
- **Close guard**: with any input (or a saved-but-unfinished state), closing
  the wizard asks first; the saved file is never reclaimed — it is your
  work; delete it in the YAML config screen if you do not want it.

## The output: one category YAML

The one and only artifact is a category YAML under `config/`, shaped exactly
like a hand-written file or one drafted by "new" in the YAML config screen
(that path starts from the minimal template; the wizard goes straight to a
runnable collection config). The full form with paging and User-Agent below —
the `headers` / `pagination` blocks simply do not appear when not chosen, the
generator output is byte-locked by unit tests, and the generator writes the
Chinese header comments shown here:

```yaml
# 由建源向导生成:普通网页列表采集(每天到点抓一页/几页,提取列表字段)。
# 只配了采集;关键词过滤、分类、推送等下游配置请到「YAML配置」补全。
id: gpu-watch
name: 显卡行情观察
schedule: "0 10 * * *"
sources:
  - name: main
    engine: static_html
    url: "https://example.com/vga/{page}.html"
    method: GET
    headers:              # 浏览器标识;不需要可整节删掉
      User-Agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    pagination:           # 翻页:网址里的 {page} 依次换成页码
      mode: template
      max_pages: 5
    extract:              # 提取:item 框住重复的整条,fields 取每条里的字段
      type: list
      item: "div.list-item"
      fields:
        title: "h3 a"
        url: "h3 a@href"
        price: "span.price-type"
```

- **Collection only**: the first two comment lines, the done panel, and this
  page all point at the same thing — `watchlist` / `classify` / `push` and
  the other downstream sections are not generated; complete them in the YAML
  config screen. Two defaults are worth knowing: push defaults to an empty
  list (nothing is pushed until configured); `classify.builtin` defaults on,
  and items matching none of the seven built-in categories are dropped at the
  classify stage — for a custom-domain list, turn `classify.builtin` off or
  add rules, exactly like the minimal example in
  [Write a Plugin](write-a-plugin.md).
- **One file is the only truth**: the wizard keeps no second "project file";
  closing it discards the form state, and every re-save leaves a `.bak`. The
  wizard and the YAML config screen edit the same file — hand off in either
  direction at any time.
- The fuller L2 writing (rate limiting / proxy / trend baseline) is
  `config/gpu-prices.example.yaml`(随源码发布,本门面仓不含).

## Three ways to create a source: hand-written, editor, or wizard

| Path | Fits | Validation & artifact | Boundary |
|---|---|---|---|
| Hand-written YAML (CLI workflow) | Full control: any engine, multi-source, POST, proxies, all 12 downstream sections | Your own file; checked by `myssia test` / `myssia doctor` | Requires YAML; the workflow is [Write a Plugin](write-a-plugin.md) |
| YAML config screen | Sources the wizard cannot cover (RSS / JSON API / login / multi-source), and editing any existing config | Edits any category file under config in-app; validates before save, auto `.bak`; "new" drafts from the minimal template | Requires reading YAML; field semantics in the [schema reference](schema.md) |
| Source wizard (this page) | Plain web lists, zero YAML knowledge, minutes to start | Form → generate → validate → save → test in one closed loop; artifact identical in shape to the above | V1 locks the `static_html` list trio (list + fields + paging); anything beyond is pointed to the editor |

All three roads lead to the same artifact — one category YAML under `config/`
— and hand off to one another: the wizard lays the foundation, the editor
completes the downstream, and later page redesigns send selector fixes back
to the editor. V1 is deliberately **a form wizard that generates YAML**; the
full Octoparse-style "page preview + click-to-pick" form is the long-term
item below.

## Long-term roadmap (V2+, not promised)

These directions sit on the long-term roadmap; the current version commits to
no timeline for any of them:

- **Page preview + click-to-pick**: an embedded page preview inside the
  wizard where clicking elements generates selectors, replacing hand-copied
  CSS selectors;
- **Auto-detect start**: fetch the first page once, guess item / fields /
  paging automatically, and let the human confirm;
- **AI-built sources**: say the requirement in one sentence, AI drafts the
  whole config;
- **Reverse import**: load an existing YAML back into the wizard's form and
  keep editing.
