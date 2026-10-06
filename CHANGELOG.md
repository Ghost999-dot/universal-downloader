# Changelog

All notable changes to **Universal Downloader** (the app, the web UI, and the browser userscript).

The userscript version is tracked in its `@version` header; the current release is **1.9.1**.
Dates are `YYYY-MM-DD`.

---

## [1.9.1] — 2026-10-06 — Quality picker + site coverage

### Userscript

- **Download quality picker.** New Tampermonkey menu command **🎚 Download quality** cycles through
  `1080 → 720 → 480 → 360 → best` and persists the choice (`GM_setValue`). Every grab — grabber sites
  *and* X/Twitter — now sends the chosen `quality` to the app, which caps the yt-dlp format accordingly
  (`height<=<q>`).
- **Default quality is now 1080**, falling back to 720 or lower automatically when 1080 isn't available.
- **Grabber allow-list now matches the originals.** Folded in the full site list from the MagicPH grabber
  plus Pure Fun's Pornhub `.org` mirrors, so the one script covers everything the separate scripts did:
  erome, ebonybaddies, pornhub(+premium) `.com`/`.org`, youporn(gay), redtube, tube8, thumbzilla,
  onlyfans, xhamster, xnxx, xvideos, 91porn, hqporner, spankbang, porntrex, analdin, porn00, sxyprn,
  eporner, youjizz. A one-time migration (`allow_migrated_v3`) folds these into any saved custom list
  without clobbering it.
  - **Safety note:** only the *domain lists and safe features* were taken from those scripts. None of the
    third-party scripts' bundled analytics/affiliate/redirect code (e.g. Pure Fun's `OverseaNavigation`
    adware) was copied.

### Backend

- **eporner downloads no longer get skipped.** The global `max_filesize` cap was silently aborting long
  videos (e.g. a 79-minute file past the 2 GB cap). Tier-0 (single-post) grabs and eporner are now
  exempt from both the pre-download size cap and the post-download size-based deletion.
- **Profile album counter fixed.** `_disk_album_ids` was counting distinct albums by an `[id]` filename
  tag, but many files are saved as `Title (N).mp4` with no id — so big profiles read as "1". It now
  groups by album *title* (stripping the trailing ` (N)` index and ` [id]` tag). Example: CumDolls went
  from a reported 1 → 285.

---

## [1.7.0] — 2026-09-27 — Per-profile counter + TWITTER foldering

### Web UI

- **Per-profile completion counter** on download cards: a centered pill (e.g. `6 / 675`) showing how many
  of a creator's albums are done versus the profile's total. Works on active downloads too, and patches
  live via `updateCard`.
- Counter reads **disk vs. online profile total**: `done` = distinct albums already on disk under
  `downloads/videos/<creator>` (cached ~20s); `total` = the creator's online profile count, filled in the
  background. The overall progress bar uses a session-peak denominator so it stops jumping backward.
- **Destination folder** shown on every card — a right-aligned `📁 videos/<creator>` in the card title
  row — so you can see where each file is going before it lands.

### Backend

- **All X/Twitter media now routes into one `videos/TWITTER/` folder**, regardless of author.
- **X downloads are named after the post** (author + tweet text, sanitized) with an `[id]` suffix for
  uniqueness, instead of a generic media id.
- Ran a one-time cleanup sweep consolidating the previously scattered Twitter files into `TWITTER/`.
- `profile_done.json` added to `.gitignore` (runtime state).

---

## [1.6.1] — 2026-09-26 — X/Twitter via XEnhancer capture, routed to the app

### Userscript

- **Folded in XEnhancer's safe features** (MIT, credited in-code) as a `TwitterExtras` module:
  - **Absolute-timestamp reformatting** on tweets, with a **"Time format settings"** menu dialog to pick
    the format.
  - **Simplify (narrow-feed) mode**, toggled from a menu command (`x_simplify_mode`).
- **X/Twitter downloads now use passive XHR capture.** A hook on `XMLHttpRequest` (`hookXHR` /
  `extractMedia`) scans X's own JSON responses for `extended_entities` media and stashes them in a
  `mediaMap` (with author name, handle, and tweet text). Clicking a download button resolves media
  capture-first, with a GraphQL `fetchJson` fallback.
- **Captured media is sent to the app** (`POST /api/download` with a `title`), not saved via the browser —
  so X media flows through the same queue, foldering, naming, and integrity-verification pipeline as
  everything else. (This is the key difference from stock XEnhancer, which saves in-browser.)

---

## [1.5.x] — 2026-09-26 — Destination folders + docs

### Web UI

- Download cards show the **destination folder**; moved it to the right side of the card title row for a
  cleaner layout.

### Docs

- Added README screenshots. All are **public-safe mocks** — real app chrome with placeholder cards
  (including an X-sourced card); no real creators, thumbnails, or URLs.

### Fixes

- `.gitignore`: moved inline `# ...` comments onto their own lines — a trailing comment was becoming part
  of the ignore pattern and breaking it.

---

## [1.0.0] — 2026-09-26 — Initial public release

First public source release of **Universal Downloader**: a local Flask + yt-dlp media downloader with a
companion browser userscript.

- **Local download server** on `http://127.0.0.1:9898` that takes a URL and downloads it with yt-dlp
  (video / audio / images), foldering and naming by creator.
- **Kill-able child-process workers** with a configurable pool (`max_workers`) and **priority tiers**
  (single posts jump ahead of large profile scrapes).
- **Browser userscript** (`userscript.user.js`): a floating ⬇ grab bubble, modifier + right-click to send
  video/audio/image, and `Alt+Shift+D` to toggle a site — plus one-click download buttons on X/Twitter.
- **Smart queue extras:** pre-queue disk-space fit check, dedup handling, title keyword + whole-word
  blocklists, per-poster blocklist, picture-only-album skipping, and post-download **integrity
  verification** with auto-heal.
- **Optional Telegram push** (Telethon user session) to mirror finished files to a chat.
- Ships as a single **PyInstaller** `--onefile --windowed` exe.

> **Privacy:** this repo is **app source only**. Runtime data (`config.json`, `server_state.json`,
> `cookies.txt`, `telegram.session`, the `downloads/` folder, etc.) is git-ignored and never published.
> Start from `config.example.json`.
