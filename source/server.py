#!/usr/bin/env python3
"""Universal Downloader — local web server.

Self-contained: everything lives in this folder.
    server.py            this file
    static/index.html    the web UI
    ffmpeg.exe           bundled (used for merging + MP3 conversion)
    cookies.txt          optional — drop yours here for logged-in sites (IG, etc.)
    server_state.json    auto-saved download history (survives restarts)
    downloads/           videos/ · audios/ · images/ · lives/

Run:  python server.py     then open  http://127.0.0.1:9898
Deps: pip install flask yt-dlp
"""

import atexit, concurrent.futures, datetime, hashlib, json, os, queue, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError as e:
    sys.exit(f"\nMissing dependency: {e}\nFix: pip install flask yt-dlp\n")

# ── Paths — next to the .exe when frozen, else next to this file ──────────────
if getattr(sys, "frozen", False):          # running as a PyInstaller bundle
    BASE = Path(sys.executable).resolve().parent          # writable data (next to exe)
    RES  = Path(getattr(sys, "_MEIPASS", BASE))           # bundled read-only resources
else:
    BASE = Path(__file__).resolve().parent
    RES  = BASE

# ── yt-dlp engine — prefer an EXTERNAL, updatable copy so the engine can be refreshed
#    without rebuilding the whole app (extractors break weekly). A `ytdlp/` folder next to
#    the app, if it holds a yt_dlp package, is put first on sys.path; the "⟳ Update engine"
#    button (pip --target ytdlp) fills it. Any failure falls back to the bundled copy, so a
#    bad/partial update can never brick the app — just delete `ytdlp/` to revert. ──
YTDLP_DIR = BASE / "ytdlp"
if (YTDLP_DIR / "yt_dlp").is_dir():
    sys.path.insert(0, str(YTDLP_DIR))
try:
    import yt_dlp
except Exception:                          # vendored copy broken? drop it and use the bundled one
    sys.path[:] = [p for p in sys.path if p != str(YTDLP_DIR)]
    for _m in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
        del sys.modules[_m]
    try:
        import yt_dlp
    except ImportError as e:
        sys.exit(f"\nMissing dependency: {e}\nFix: pip install flask yt-dlp\n")
# Serve the UI from disk when present (so UI tweaks don't need a rebuild): check
# next to the app, then source/, then the copy bundled inside the exe.
if (BASE / "static" / "index.html").exists():
    STATIC_DIR = BASE / "static"
elif (BASE / "source" / "static" / "index.html").exists():
    STATIC_DIR = BASE / "source" / "static"
else:
    STATIC_DIR = RES / "static"
DL_ROOT      = BASE / "downloads"
SUBDIRS      = {"video": "videos", "audio": "audios",
                "image": "images", "live":  "lives"}
ARCHIVE_FILE = BASE / "download_archive.txt"   # dedupe: records every downloaded ID
STATE_FILE   = BASE / "server_state.json"
VERIFY_CACHE_FILE = BASE / "verify_cache.json" # files already confirmed intact (skip re-checking)
COOKIES_FILE = BASE / "cookies.txt"
FFMPEG_EXE   = BASE / "ffmpeg.exe"
ARIA2_EXE    = BASE / "aria2c.exe"     # optional: multi-connection downloader (faster)

# ── Config — an optional config.json next to the app lets you tune without a rebuild ──
_DEFAULT_CFG = {
    "max_workers": 3,           # parallel downloads (rate-limited sites 403 if too high)
    "warmup_seconds": 20,       # delay before downloads start, to size-sort the queue first
    "download_timeout": 60,     # stalled-connection timeout, seconds
    "stall_timeout": 600,       # no-progress this long -> force-abort + retry (frees wedged workers)
    "request_spacing": 1.0,     # seconds between requests (avoids throttling)
    "concurrent_fragments": 4,  # parallel fragment fetches for HLS/DASH (YouTube/Twitch/m3u8) — big speedup, native, safe
    "max_size_gb": 2,           # skip / abort any video larger than this many GB (0 = no limit)
    # auto-pull LIVE cookies from a browser (fixes YouTube "confirm you're not a bot" + refreshes
    # site logins). "" = off (use cookies.txt only). e.g. "firefox"/"edge"/"chrome"/"brave".
    # NOTE: Chromium App-Bound Encryption can block Chrome/Edge here — Firefox is the most reliable.
    "cookies_from_browser": "",
    "max_finished_shown": 60,   # how many finished cards the UI keeps showing
    # titles containing any of these are skipped (not scraped, queued, or downloaded).
    # block_keywords = substring match; block_words = whole-word match (for short labels
    # like TS/CD that would false-positive as substrings, e.g. inside "tits").
    "block_keywords": ["transgender", "transsexual", "transvestite", "tranny",
                        "shemale", "she-male", "ladyboy", "lady boy", "femboy", "fem boy",
                        "tgirl", "t-girl", "futanari", "futa", "dickgirl", "sissy",
                        "crossdress", "cross-dress", "newhalf"],
    "block_words": ["ts", "cd", "tg", "ftm", "mtf", "trans"],
    # posters whose albums are never downloaded, regardless of title/tags — the only
    # reliable catch for accounts whose content carries no label (use the 🚫 button in the UI)
    "block_posters": [],
    "use_aria2": True,          # use bundled aria2c (multi-connection) when present = faster
    "verify_after_download": True,  # when the queue empties, scan finished files for corruption
    "verify_min_kb": 30,        # files smaller than this are treated as failed/junk
    "verify_deep": False,       # True = full decode (thorough, slow); False = container check (fast)
    "verify_auto_heal": True,   # delete corrupt files, clear their archive id, and re-download
    "verify_profiles": True,    # when idle, re-scrape each profile & re-download any missing posts
    "skip_image_only_albums": True,  # don't queue picture-only erome albums (no video -> they just fail)
    "max_jobs_retained": 800,   # cap finished/error cards kept in memory+state (oldest dropped)
    # ── Telegram push (optional) — send each finished download to a Telegram chat/group ──
    # Uses a USER account (Telethon/MTProto) so files up to 2 GB go through (bots cap at 50 MB).
    # Set up once by running  telegram_login.py  — it fills api_id/api_hash/chat and logs you in.
    "telegram_enabled": False,      # master switch (telegram_login.py flips this on)
    "telegram_api_id": "",          # from https://my.telegram.org
    "telegram_api_hash": "",        # from https://my.telegram.org
    "telegram_chat": "",            # group title, @username, or numeric id to send into
    "telegram_delete_after": True,  # delete the local file once it's safely uploaded (frees disk)
    # ── Mega downloads (free-tier bandwidth limit handling) ──
    "mega_auto_resume": True,       # on hitting the transfer quota, wait it out then resume
    "mega_stall_minutes": 3,        # no transfer progress this long mid-download = quota-stalled
    "mega_max_attempts": 24,        # give up after this many quota-wait cycles
    "mega_default_wait_minutes": 180,  # fallback wait if Mega doesn't state a time (it usually says ~3h)
    "port": 9898,
    "debug": False,             # write debug.log for troubleshooting
}
CONFIG_FILE = BASE / "config.json"
_cfg = dict(_DEFAULT_CFG)
try:
    _cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
except Exception:
    try: CONFIG_FILE.write_text(json.dumps(_DEFAULT_CFG, indent=2), encoding="utf-8")
    except Exception: pass

MAX_WORKERS    = int(_cfg["max_workers"])
WARMUP_SECS    = int(_cfg["warmup_seconds"])
SOCK_TIMEOUT   = int(_cfg["download_timeout"])
STALL_TIMEOUT  = int(_cfg.get("stall_timeout", 600))
REQ_SPACING    = float(_cfg["request_spacing"])
MAX_FIN_SHOWN  = int(_cfg["max_finished_shown"])
BLOCK_KEYWORDS = [str(k).lower() for k in (_cfg.get("block_keywords") or [])]
BLOCK_WORDS    = [str(k).lower() for k in (_cfg.get("block_words") or [])]
BLOCK_POSTERS  = {str(p).lower() for p in (_cfg.get("block_posters") or [])}
USE_ARIA2      = bool(_cfg.get("use_aria2", True))
FRAG_CONC      = max(1, int(_cfg.get("concurrent_fragments", 4)))
MAX_SIZE_BYTES = int(float(_cfg.get("max_size_gb", 0) or 0) * (1024 ** 3))   # 0 = no cap
DEFAULT_EST_BYTES = 50_000_000      # size guess for a not-yet-probed job (matches the prioritizer)

# ── Telegram push config ──
TG_ENABLED      = bool(_cfg.get("telegram_enabled", False))
TG_API_ID       = str(_cfg.get("telegram_api_id", "") or "").strip()
TG_API_HASH     = str(_cfg.get("telegram_api_hash", "") or "").strip()
TG_CHAT         = str(_cfg.get("telegram_chat", "") or "").strip()
TG_DELETE_AFTER = bool(_cfg.get("telegram_delete_after", True))

# ── Mega config ──
MEGA_AUTO_RESUME  = bool(_cfg.get("mega_auto_resume", True))
MEGA_STALL_SEC    = max(60, int(float(_cfg.get("mega_stall_minutes", 3)) * 60))
MEGA_MAX_ATTEMPTS = max(1, int(_cfg.get("mega_max_attempts", 24)))
MEGA_DEFAULT_WAIT = max(60, int(float(_cfg.get("mega_default_wait_minutes", 180)) * 60))
COOKIES_BROWSER = str(_cfg.get("cookies_from_browser", "") or "").strip().lower()
VERIFY_AFTER   = bool(_cfg.get("verify_after_download", True))
VERIFY_MIN_KB  = int(_cfg.get("verify_min_kb", 30))
VERIFY_DEEP    = bool(_cfg.get("verify_deep", False))
VERIFY_HEAL    = bool(_cfg.get("verify_auto_heal", True))
VERIFY_PROFS   = bool(_cfg.get("verify_profiles", True))
SKIP_IMAGE_ONLY = bool(_cfg.get("skip_image_only_albums", True))
MAX_JOBS_KEEP  = int(_cfg.get("max_jobs_retained", 800))
DEBUG          = bool(_cfg["debug"])

# Hide the console window when ffmpeg runs inside the windowed .exe build.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
HOST, PORT     = "127.0.0.1", int(_cfg["port"])

VERSION = "2026.07.18"          # shown in /api/info + the UI; bump on each rebuild

# ── Always-on log (app.log, rotates at 2 MB) — so a wedge is never a mystery again ──
LOG_FILE = BASE / "app.log"
_log_lock = threading.Lock()
def _log(msg):
    try:
        with _log_lock:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
                old = LOG_FILE.with_suffix(".log.1")
                if old.exists(): old.unlink()
                LOG_FILE.rename(old)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("%m-%d %H:%M:%S  ") + str(msg) + "\n")
    except Exception: pass

# Thread heartbeats: each background loop pings; /api/info shows seconds-since,
# so a silently-dead thread is visible instead of a mystery.
_beats: Dict[str, float] = {}
_beats_lock = threading.Lock()
def _beat(name):
    with _beats_lock:
        _beats[name] = time.time()
def _beats_snapshot() -> Dict[str, float]:
    with _beats_lock:
        return dict(_beats)

def _atomic_json(path: Path, obj):
    """Write JSON via tmp + atomic replace, so a crash mid-write can't corrupt the file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

for sub in SUBDIRS.values():
    (DL_ROOT / sub).mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "bmp"}

# ── CSRF guard for dangerous endpoints ─────────────────────────────────────────
# The server is localhost-only and unauthenticated, so ANY web page the user visits
# can POST to 127.0.0.1:9898. That's fine for /api/download (the whole point of the
# cross-site userscript bubble), but the control endpoints below must NOT be driveable
# by a random page — so they require a same-origin request (our own dashboard/launcher).
_ALLOWED_ORIGINS = (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")
_CSRF_PROTECTED  = {"/api/shutdown", "/api/pause", "/api/jobs/clear", "/api/blocklist",
                    "/api/block_poster", "/api/rescan", "/api/recheck", "/api/update_engine"}

@app.before_request
def _csrf_guard():
    p, m = request.path, request.method
    protected = (p in _CSRF_PROTECTED) or (m == "DELETE" and p.startswith("/api/jobs/"))
    if not protected:
        return None
    origin = request.headers.get("Origin", "")
    if origin and origin not in _ALLOWED_ORIGINS:    # cross-origin page -> reject
        return jsonify(error="forbidden (cross-origin)"), 403
    return None


# ── Job model ─────────────────────────────────────────────────────────────────
class Job:
    __slots__ = ("id","url","fmt","quality","audio_only","status","type",
                 "title","platform","thumb","progress","speed","eta",
                 "filename","error","items_done","items_total","created","uploader","tries","started",
                 "size_est","resume_at","tier")

    def __init__(self, jid, url, fmt, quality, audio_only, uploader=""):
        self.id          = jid
        self.url         = url
        self.fmt         = fmt
        self.quality     = quality
        self.audio_only  = audio_only
        self.uploader    = uploader     # poster name -> own download folder
        self.status      = "queued"     # queued | downloading | finished | error
        self.type        = "audio" if (audio_only or fmt == "mp3") else "video"
        self.title       = url
        self.platform    = ""
        self.thumb       = ""
        self.progress    = 0.0
        self.speed       = ""
        self.eta         = ""
        self.filename    = ""
        self.error       = ""
        self.items_done  = 0
        self.items_total = 0
        self.created     = time.time()
        self.tries       = 0            # retry counter for transient (rate-limit) failures
        self.started     = 0.0          # when this download began (for stuck detection)
        self.size_est    = 0            # estimated bytes (from the size probe) — for disk-fit checks
        self.resume_at   = 0.0          # Mega quota-wait: unix time to re-dispatch (0 = not waiting)
        self.tier        = 1            # priority tier: 0 = standalone post (jumps ahead), 1 = from a profile scan

    def to_dict(self):
        return {s: getattr(self, s) for s in self.__slots__}

    # Reduced schema persisted to disk (mirrors the reference app)
    def to_state(self):
        return dict(id=self.id, url=self.url, status=self.status, type=self.type,
                    thumb=self.thumb, title=self.title, uploader=self.uploader,
                    progress=f"{self.progress:.1f}", filename=self.filename,
                    error=self.error, tier=self.tier)


_jobs: "OrderedDict[str, Job]" = OrderedDict()
_lock = threading.Lock()
# staged jids awaiting a size probe — a PriorityQueue by (tier, seq) so standalone posts
# (tier 0) get probed BEFORE a big backlog of profile-scan jobs (tier 1), not stuck behind them.
_stage: queue.PriorityQueue = queue.PriorityQueue()
_stage_seq = 0
_pq: queue.PriorityQueue = queue.PriorityQueue()   # (tier, weight, seq, jid) — posts first, then smallest
_pq_seq = 0
_seq_lock = threading.Lock()

def _stage_put(jid):
    """Stage a jid for probing, ordered by its priority tier (0 = post, 1 = profile scan)."""
    global _stage_seq
    with _lock:
        j = _jobs.get(jid)
        tier = int(j.tier) if j else 1
    with _seq_lock:
        _stage_seq += 1; s = _stage_seq
    _stage.put((tier, s, jid))

# Brief startup warmup: let the prioritizers build a size-sorted buffer before
# workers start pulling, so the smallest jobs truly go first (not whatever probed first).
_warm_deadline = None
_warm_lock = threading.Lock()
def _note_staged():
    global _warm_deadline
    with _warm_lock:
        if _warm_deadline is None:
            _warm_deadline = time.time() + WARMUP_SECS

# Pause/stop controls: workers wait while paused; cancelled ids abort mid-download.
_run = threading.Event(); _run.set()    # set = running; cleared = paused
_cancel: set = set()
_cancel_lock = threading.Lock()

# In-flight ENUMERATIONS: scraping a profile/model/tag page for its videos, which happens
# in a background thread BEFORE any job exists. Surfaced in /api/jobs so the UI can show a
# live "scanning…" card — otherwise a pushed profile looks like nothing happened until the
# first real download appears.
_expanding: "OrderedDict[str, dict]" = OrderedDict()
_expanding_lock = threading.Lock()
_exp_seq = 0
def _exp_start(url, kind="scanning"):
    global _exp_seq
    with _expanding_lock:
        _exp_seq += 1
        tok = f"exp{_exp_seq}"
        _expanding[tok] = {"url": url, "kind": kind, "found": 0,
                           "note": "starting…", "started": time.time()}
    return tok
def _exp_update(tok, **kw):
    with _expanding_lock:
        if tok in _expanding: _expanding[tok].update(kw)
def _exp_done(tok):
    with _expanding_lock:
        _expanding.pop(tok, None)
def _expanding_snapshot():
    with _expanding_lock:
        return [dict(id=t, **v) for t, v in _expanding.items()]

# Whole-word matcher for short labels (TS, CD, …). \b stops "ts" matching inside "tits";
# the trailing \d* lets "ts4"/"cd2" match too. Rebuilt whenever the word list is edited.
def _rebuild_block_word_re():
    global _BLOCK_WORD_RE
    _BLOCK_WORD_RE = (re.compile(r"\b(?:" + "|".join(re.escape(w) for w in BLOCK_WORDS) + r")\d*\b", re.I)
                      if BLOCK_WORDS else None)
_BLOCK_WORD_RE = None
_rebuild_block_word_re()

def _is_blocked_title(title: str) -> bool:
    """True if a title looks like TS/Trans (etc.) content that should never be downloaded."""
    t = (title or "").lower()
    if not t:
        return False
    if any(k in t for k in BLOCK_KEYWORDS):          # substring labels (trans, shemale, …)
        return True
    if _BLOCK_WORD_RE and _BLOCK_WORD_RE.search(t):  # whole-word labels (ts, cd, tg, …)
        return True
    return False

def _is_blocked_poster(name: str) -> bool:
    """True if this poster is on the user's blocklist — their albums never download."""
    return bool(name) and name.strip().lower() in BLOCK_POSTERS

# Errors meaning the content is genuinely GONE (not a transient hiccup): we retry these
# only a few times, then drop + remember them so the profile rescan can't re-queue them.
# NB: "Unsupported URL" is deliberately NOT here — on erome that's usually a transient
# yt-dlp hiccup on a live album that succeeds on the next retry.
DEAD_MAX_TRIES = 2              # one fresh-extraction retry (rules out a stale CDN url), then drop
_DEAD_ERR_RE = re.compile(
    r"http error 4(?:04|10)|not found|no longer available|video is unavailable|"
    r"private (?:video|album)|been removed|account.*(?:closed|terminated|suspended)|"
    r"no (?:video|media) (?:formats? )?found", re.I)
def _is_dead_error(msg: str) -> bool:
    return bool(msg) and bool(_DEAD_ERR_RE.search(msg))

DEAD_FILE = BASE / "dead_albums.json"   # album ids confirmed gone -> never re-queued
_dead_albums: set = set()
def _load_dead():
    global _dead_albums
    try:    _dead_albums = set(json.loads(DEAD_FILE.read_text(encoding="utf-8")))
    except Exception: _dead_albums = set()
def _save_dead():
    try:    _atomic_json(DEAD_FILE, sorted(_dead_albums))
    except Exception: pass
def _mark_dead(url: str):
    aid = _album_id_from_url(url)
    if aid and aid not in _dead_albums:
        _dead_albums.add(aid); _save_dead(); _log(f"marked dead: {aid}")
_load_dead()

# URLs the user explicitly DELETED. Persisted + honored by _queue_job so the self-healing
# completeness/integrity scans can't silently re-queue something you just removed. Cleared
# for a URL when you deliberately re-submit it (so a re-add always works).
REMOVED_FILE = BASE / "removed.json"
_removed_urls: set = set()
_removed_lock = threading.Lock()
def _load_removed():
    global _removed_urls
    try:    _removed_urls = set(json.loads(REMOVED_FILE.read_text(encoding="utf-8")))
    except Exception: _removed_urls = set()
def _save_removed():
    with _removed_lock:
        try:    _atomic_json(REMOVED_FILE, sorted(_removed_urls))
        except Exception: pass
def _is_removed(url: str) -> bool:
    with _removed_lock:
        return bool(url) and url in _removed_urls
def _mark_removed(url: str):
    if not url:
        return
    with _removed_lock:
        if url in _removed_urls:
            return
        _removed_urls.add(url)
    _save_removed()
def _unmark_removed(url: str):
    with _removed_lock:
        if url not in _removed_urls:
            return
        _removed_urls.discard(url)
    _save_removed()
_load_removed()

# (per-item keyword/duplicate filtering now lives inside the download worker child —
#  see dl_worker_main's mfilter; the archive file handles dedup across processes.)


# ── Content-hash dedup: deletes files byte-identical to ones we already have ───
# (catches the "same clip, different id" case — e.g. reposted album items).
HASHES_FILE = BASE / "content_hashes.json"
_hashes: Dict[str, str] = {}        # md5 -> relative path of the file we keep
_hash_lock = threading.Lock()

def _load_hashes():
    global _hashes
    try:    _hashes = json.loads(HASHES_FILE.read_text(encoding="utf-8"))
    except Exception: _hashes = {}

def _save_hashes():
    try:    _atomic_json(HASHES_FILE, _hashes)
    except Exception: pass

def _quickhash(path):
    """Fast content fingerprint: file size + md5 of the first & last 64 KB.
       Avoids hashing whole large videos; collisions are astronomically unlikely for media."""
    sz = path.stat().st_size
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(65536))
        if sz > 131072:
            f.seek(-65536, os.SEEK_END); h.update(f.read(65536))
    return f"{sz}:{h.hexdigest()}"

def _dedup_new_files(folder=None) -> int:
    """Hash not-yet-seen files in `folder` (or whole tree); delete byte-identical dups.
       The hash registry is global, so a dup is caught even if its twin is elsewhere."""
    root = folder if folder is not None else DL_ROOT
    removed = 0
    with _hash_lock:
        known = set(_hashes.values())
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix == ".part":
                continue
            rel = str(f.relative_to(BASE))
            if rel in known:
                continue
            try:    digest = _quickhash(f)
            except Exception: continue
            keeper = _hashes.get(digest)
            if keeper and (BASE / keeper).exists() and (BASE / keeper) != f:
                try: f.unlink(); removed += 1          # byte-identical -> drop it
                except Exception: pass
            else:
                _hashes[digest] = rel; known.add(rel)  # first time we've seen this
        _save_hashes()
    return removed

_load_hashes()


# ── State persistence (debounced: bursts of changes coalesce into one disk write) ─
_state_dirty = threading.Event()
_persist_enabled = False            # writes are refused until _load_state() has run, so a
                                    # fresh process can never clobber the saved history

def _save_state():
    _state_dirty.set()              # cheap mark; the saver thread writes shortly after

def _write_state():
    if not _persist_enabled:
        return
    with _lock:
        items = [j.to_state() for j in _jobs.values()]
    try:
        _atomic_json(STATE_FILE, {"stats": {"total": len(items), "items": items}})
    except Exception as e:
        _log(f"write_state: {e}")

def _trim_jobs():
    """Cap memory + state growth: keep only the most recent MAX_JOBS_KEEP finished/error
       jobs (oldest dropped first). Active/queued jobs are never trimmed. _jobs is an
       insertion-ordered OrderedDict, so the oldest terminal jobs are at the front."""
    if MAX_JOBS_KEEP <= 0:
        return
    with _lock:
        done = [jid for jid, j in _jobs.items() if j.status in ("finished", "error")]
        excess = len(done) - MAX_JOBS_KEEP
        if excess > 0:
            for jid in done[:excess]:        # front = oldest
                _jobs.pop(jid, None)
            return excess
    return 0

def _state_saver():
    while True:
        _beat("saver")
        _state_dirty.wait()
        time.sleep(2.0)             # coalesce a burst of changes into a single write
        _state_dirty.clear()
        try:    _trim_jobs()        # keep memory + server_state.json from growing forever
        except Exception as e: _log(f"trim_jobs: {e}")
        _write_state()

atexit.register(_write_state)       # flush on normal exit (no-op until state was loaded)

def _load_state():
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    resume, seen_pending = [], set()
    for it in data.get("stats", {}).get("items", []):
        jid = it.get("id")
        if not jid:
            continue
        url = it.get("url", "")
        st  = it.get("status", "finished")
        # Collapse duplicate still-pending URLs so a queue can't resume twice.
        if st in ("queued", "downloading"):
            if url in seen_pending:
                continue
            seen_pending.add(url)
        j = Job(jid, url, "mp4", "best", it.get("type") == "audio",
                uploader=it.get("uploader", ""))
        j.type     = it.get("type", "video")
        j.title    = it.get("title", url)
        j.thumb    = it.get("thumb", "")
        j.filename = it.get("filename", "")
        j.tier     = int(it.get("tier", 1))     # keep standalone-post priority across restarts
        if st == "finished":
            j.status = "finished"; j.progress = 100.0
        elif st == "error":
            j.status = "error"
            j.error  = it.get("error") or "Interrupted (app was closed)."
        else:                                   # queued / downloading -> RESUME it
            j.status = "queued"; j.progress = 0.0
            resume.append(jid)
        _jobs[jid] = j
    for jid in resume:                          # re-queue unfinished work (gets re-prioritized)
        _stage_put(jid)
    if resume:
        _note_staged()


# ── yt-dlp option builder ─────────────────────────────────────────────────────
# Each thread gets its OWN copy of the cookies, so many parallel downloads never
# contend on (or corrupt) the real cookies.txt — yt-dlp never touches the original.
_cookie_local = threading.local()
_cookie_files = []                               # temp copies, cleaned up on exit

def _thread_cookiefile():
    if not COOKIES_FILE.exists():
        return None
    try:
        mtime = COOKIES_FILE.stat().st_mtime
        path  = getattr(_cookie_local, "path", None)
        if path and os.path.exists(path) and getattr(_cookie_local, "mtime", None) == mtime:
            return path                          # reuse this thread's copy
        if not path:
            fd, path = tempfile.mkstemp(prefix="udcookies_", suffix=".txt"); os.close(fd)
            _cookie_files.append(path)
        shutil.copy2(COOKIES_FILE, path)         # refresh if the user updated cookies.txt
        _cookie_local.path, _cookie_local.mtime = path, mtime
        return path
    except Exception:
        return str(COOKIES_FILE)                 # best-effort fallback

def _cleanup_cookie_files():
    for p in list(_cookie_files):
        try: os.remove(p)
        except Exception: pass
atexit.register(_cleanup_cookie_files)

def _base_opts(archive=True):
    o = dict(
        quiet            = True,
        no_warnings      = True,
        ignoreerrors     = True,
        noplaylist       = False,
    )
    if archive:                                 # skip already-downloaded IDs
        o["download_archive"] = str(ARCHIVE_FILE)
    if FFMPEG_EXE.exists():
        o["ffmpeg_location"] = str(FFMPEG_EXE)
    cf = _thread_cookiefile()
    if cf:
        o["cookiefile"] = cf
    if COOKIES_BROWSER:
        o["cookiesfrombrowser"] = (COOKIES_BROWSER,)   # live browser cookies (YouTube bot-gate fix)
    return o


# ── Profile expansion: turn a user's page into all their album links ──────────
# yt-dlp can't enumerate sites like erome's profile pages, so we scrape them.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def _http_get(url: str, tries: int = 3) -> str:
    """Fetch a page, retrying transient blips so one network hiccup on page 1 can't
       abort a whole profile scrape (which used to surface as a false 'couldn't read
       profile' error). 429s back off longer; the last failure is re-raised."""
    last = None
    for i in range(max(1, tries)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if i == tries - 1:
                break
            time.sleep((5 * (i + 1)) if "429" in str(e) else (1.5 * (i + 1)))
    raise last

def _safe_name(s: str) -> str:
    """Make a string safe to use as a Windows folder name."""
    s = re.sub(r'[\\/:*?"<>|]+', "", s or "").strip().strip(".")
    return s[:80]

def _is_profile(url: str) -> bool:
    # erome.com/<user> is a profile;  erome.com/a/<id> is a single album
    m = re.match(r"https?://(?:www\.)?erome\.com/([^/?#]+)", url or "")
    return bool(m) and m.group(1).lower() != "a"

_ALBUM_RE = re.compile(
    r'album-link"\s+href="https?://(?:www\.)?erome\.com/a/([A-Za-z0-9]+)"[^>]*>\s*'
    r'<img[^>]*\balt="([^"]*)"', re.I)
# each album card shows a video-count badge: <span class="album-videos">…N</span>.
# N == 0 means the album is pictures only (no video to download -> it would just fail).
_ALBUM_VIDCOUNT_RE = re.compile(r'class="album-videos"[^>]*>(?:.*?</svg>)?\s*(\d+)', re.I | re.S)

def _erome_video_counts(html: str) -> dict:
    """Map album id -> video count, read off each card's thumbnail badge. Missing == unknown
       (treated as 'has video' so we never skip an album we couldn't classify)."""
    counts = {}
    for m in re.finditer(r'/a/([A-Za-z0-9]+)"[^>]*>\s*<img[^>]*class="album-thumbnail', html):
        win = html[m.end():m.end() + 400]
        vm = _ALBUM_VIDCOUNT_RE.search(win)
        if vm:
            counts[m.group(1)] = int(vm.group(1))
    return counts

def _profile_albums(url: str, progress=None) -> List[str]:
    """Every album link for a user, across all pages — skipping titles that match a
       block keyword (e.g. #trans), and (by default) picture-only albums that have no
       video and would only fail on download."""
    if not _is_profile(url):
        return []
    base, seen, order = url.split("?")[0].rstrip("/"), set(), []
    for page in range(1, 500):
        try:
            html = _http_get(f"{base}?page={page}")
        except Exception:
            break
        pairs = _ALBUM_RE.findall(html)                      # (id, title) per album card
        if not pairs:                                        # layout fallback: ids only
            pairs = [(a, "") for a in re.findall(r"/a/([A-Za-z0-9]+)", html)]
        vids = _erome_video_counts(html) if SKIP_IMAGE_ONLY else {}
        new = [(a, t) for (a, t) in pairs if a not in seen]
        if not new:
            break                                            # no new albums -> past last page
        for a, t in new:
            seen.add(a)
            # drop on a blocked listing-title OR a cached "blocked by tags" verdict.
            # (the deep tag-scan below fills that cache as albums are processed, so over a
            #  cycle or two every TS/Trans album disappears from the scrape entirely.)
            if _is_blocked_title(t) or _album_block.get(a) is True:
                continue
            if SKIP_IMAGE_ONLY and vids.get(a, 1) == 0:      # pictures only (0 videos) -> skip
                continue
            order.append(a)
        if progress:                                         # live "scanning page N… M found"
            progress(page, len(order))
    return [f"https://www.erome.com/a/{a}" for a in order]


# ── ebonybaddies.com support ───────────────────────────────────────────────────
# This site sits behind a Cloudflare "managed challenge": plain urllib/requests (and
# yt-dlp's default networking) get 403'd on the TLS/JA3 fingerprint alone, even with a
# valid cf_clearance cookie + matching UA. curl_cffi's browser impersonation sends a real
# Chrome TLS handshake, which — together with the user's exported cookies.txt — passes.
# A /video/<slug> page embeds the real clip as <video id="video-onpage"> PLUS ~14 related
# preview <video> tags, so yt-dlp's generic extractor returns a 15-item playlist; the main
# clip is entry 1 (document order), hence playlist_items="1" on the download.
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _EB_IMPERSONATE = ImpersonateTarget.from_str("chrome")
except Exception:
    _EB_IMPERSONATE = None

_EB_HOST_RE  = re.compile(r"https?://(?:www\.)?ebonybaddies\.com/", re.I)
_EB_VIDEO_RE = re.compile(r"https?://(?:www\.)?ebonybaddies\.com/video/([^/?#]+)", re.I)
# listing pages we can enumerate into per-video jobs (like erome profiles)
_EB_LIST_RE  = re.compile(
    r"https?://(?:www\.)?ebonybaddies\.com/"
    r"(models?|pornstars?|categor(?:y|ies)|tags?|channels?|studios?)/([^/?#]+)", re.I)
_EB_VLINK_RE = re.compile(r'href="(/video/[^"#?]+/)"', re.I)

def _is_eb(url: str) -> bool:       return bool(_EB_HOST_RE.match(url or ""))
def _is_eb_video(url: str) -> bool: return bool(_EB_VIDEO_RE.match(url or ""))
def _is_eb_list(url: str) -> bool:  return bool(_EB_LIST_RE.match(url or ""))

# ── Mega + Dropbox ────────────────────────────────────────────────────────────
_MEGA_RE = re.compile(r"https?://(?:www\.)?mega(?:\.co)?\.nz/", re.I)
def _is_mega(url: str) -> bool: return bool(_MEGA_RE.match(url or ""))

def _mega_tool():
    """Locate an external Mega downloader. Prefer MEGAcmd's mega-get (official), then
       megatools. Checks the app folder, PATH, and MEGAcmd's default install dir.
       Returns (kind, exe_path) or (None, None)."""
    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        ("megacmd", BASE / "mega-get.exe"),
        ("megacmd", Path(la) / "MEGAcmd" / "mega-get.bat"),
        ("megacmd", Path(la) / "MEGAcmd" / "mega-get.exe"),
        ("megacmd", Path(pf) / "MEGAcmd" / "mega-get.bat"),
        ("megatools", BASE / "megatools.exe"),
    ]
    for kind, p in candidates:
        try:
            if p.exists(): return kind, str(p)
        except Exception: pass
    for kind, name in (("megacmd", "mega-get"), ("megatools", "megatools")):
        found = shutil.which(name)
        if found: return kind, found
    return None, None

# mega-get's over-quota message, e.g. "you can try again in 3 hours." / "in 45 minutes"
_MEGA_WAIT_RE  = re.compile(r"try again in\s+(\d+)\s*(second|minute|hour|day)s?", re.I)
_MEGA_UNIT_SEC = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
# the transfer-progress line: "TRANSFERRING ...(12345/67890 KB:  18.17 %)"
_MEGA_PROG_RE  = re.compile(r"\(\s*([\d.]+)\s*/\s*([\d.]+)\s*\w*\s*:\s*([\d.]+)\s*%\)")

def _mega_is_quota(text: str) -> bool:
    t = (text or "").lower()
    return ("bandwidth quota" in t or "transfer quota" in t
            or "reached your" in t or "transfer not started" in t)

def _mega_wait_secs(text: str):
    m = _MEGA_WAIT_RE.search(text or "")
    return int(m.group(1)) * _MEGA_UNIT_SEC[m.group(2).lower()] if m else None

def _mega_progress(text: str):
    """Return (transferred, percent) from the last TRANSFERRING line, or None."""
    last = None
    for m in _MEGA_PROG_RE.finditer(text or ""):
        last = m
    if not last:
        return None
    try:    return float(last.group(1)), float(last.group(3))
    except Exception: return None

def _fmt_dur(secs: int) -> str:
    secs = max(0, int(secs))
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    if h: return f"{h}h {m:02d}m"
    if m: return f"{m}m {s:02d}s"
    return f"{s}s"

_DROPBOX_RE = re.compile(r"https?://(?:www\.)?dropbox\.com/", re.I)
def _is_dropbox(url: str) -> bool: return bool(_DROPBOX_RE.match(url or ""))

def _dropbox_direct(url: str) -> str:
    """Turn a Dropbox share link into a direct-download link (works for ANY file type,
       not just the videos yt-dlp's extractor handles). dl=0 -> dl=1."""
    u = re.sub(r"([?&])dl=0(\b)", r"\1dl=1\2", url)
    if "dl=1" not in u:
        u += ("&" if "?" in u else "?") + "dl=1"
    return u

def _eb_list_name(url: str) -> str:
    m = _EB_LIST_RE.match(url or "")
    return m.group(2) if m else ""

def _eb_get(url: str, tries: int = 3) -> str:
    """Fetch an ebonybaddies page past Cloudflare with curl_cffi impersonation + the
       cookies.txt the user exported from a browser that already solved the challenge."""
    from curl_cffi import requests as _cr
    ck = {}
    if COOKIES_FILE.exists():
        try:
            import http.cookiejar
            cj = http.cookiejar.MozillaCookieJar(str(COOKIES_FILE))
            cj.load(ignore_discard=True, ignore_expires=True)
            ck = {c.name: c.value for c in cj if "ebonybaddies" in (c.domain or "")}
        except Exception:
            ck = {}
    last = None
    for i in range(max(1, tries)):
        try:
            r = _cr.get(url, cookies=ck, impersonate="chrome", timeout=30)
            if r.status_code == 200:
                return r.text
            last = Exception(f"HTTP {r.status_code}")
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise last or Exception("fetch failed")

# Each listing card carries JSON-LD: the video's "@id" (its URL) followed shortly by
# an "actor":{"name": "<creator>"}. The 400-char cap keeps a card without an actor
# (e.g. model pages) from grabbing the NEXT card's actor by mistake.
_EB_JSONLD_RE = re.compile(
    r'"@id":\s*"https?://(?:www\.)?ebonybaddies\.com(/video/[^"?#]+/)"'
    r'.{0,400}?"actor"\s*:\s*\{[^}]*?"name":\s*"([^"]+)"', re.I | re.S)

def _eb_creator_from_slug(video_path: str) -> str:
    """Fallback creator = the slug's first segment (model pages have no JSON-LD actor,
       but their slugs are '<model>-...'), so this still folders them under the model."""
    s = video_path.strip("/").rsplit("/", 1)[-1]
    return s.split("-")[0] if s else ""

def _post_creator(url: str) -> str:
    """Best-effort creator/poster for a SINGLE post, so it files under videos/<creator>/
       instead of plainly in videos/. erome: the album page's poster ('X's avatar'); ebony:
       the video's JSON-LD actor, falling back to the slug's model segment. A generic URL
       returns '' — yt-dlp's own uploader/channel is used for those."""
    try:
        aid = _album_id_from_url(url)
        if aid:                                          # erome album  /a/<id>
            html = _http_get(f"https://www.erome.com/a/{aid}")
            m = re.search(r'\balt="([^"]+)\'s avatar"', html)
            return m.group(1).strip() if m else ""
        m = _EB_VIDEO_RE.match(url)
        if m:                                            # ebonybaddies  /video/<slug>
            try:
                j = _EB_JSONLD_RE.search(_eb_get(url))
                if j:
                    return j.group(2).strip()
            except Exception:
                pass
            return _eb_creator_from_slug(m.group(1))
    except Exception:
        pass
    return ""

def _eb_list_videos(url: str, progress=None):
    """Every (video_url, creator) on a model/tag/category page, across all ?page=N.
       Creator comes from each card's JSON-LD actor.name (accurate on tag/category pages
       that mix creators); falls back to the slug's first segment, then the listing name."""
    if not _is_eb_list(url):
        return []
    listname = _eb_list_name(url)
    base, seen, order, fails = url.split("?")[0].rstrip("/"), set(), [], 0
    for page in range(1, 400):
        try:
            html = _eb_get(f"{base}/?page={page}" if page > 1 else f"{base}/")
            fails = 0
        except Exception:
            fails += 1                               # tolerate a transient page hiccup, but
            if fails >= 3:                            # 3 in a row (or past the last page) -> stop
                break
            continue
        creators = {}
        for vp, nm in _EB_JSONLD_RE.findall(html):
            creators.setdefault(vp, nm.strip())
        new = [l for l in _EB_VLINK_RE.findall(html) if l not in seen]
        if not new:
            break                                    # no new links -> past the last page
        for l in new:
            seen.add(l)
            creator = creators.get(l) or _eb_creator_from_slug(l) or listname
            order.append((f"https://ebonybaddies.com{l}", creator))
        if progress:                                 # live "scanning page N… M found"
            progress(page, len(order))
    return order

def _eb_expand_and_queue(url, fmt, quality, audio):
    """Background: scrape an ebonybaddies model/tag/category page and queue each video,
       filing each under its OWN creator's folder (per-video creator, so a tag download
       sorts across many creators instead of one shared tag folder)."""
    listname = _safe_name(_eb_list_name(url))
    tok = _exp_start(url, "ebonybaddies")
    try:
        pairs = _eb_list_videos(url, progress=lambda pg, n: _exp_update(
            tok, found=n, note=f"scanning page {pg}… {n} videos found"))
        if not pairs:
            jid = _new_jid()
            with _lock:
                j = Job(jid, url, fmt, quality, audio, uploader=listname)
                j.status, j.error = "error", ("Couldn't read that ebonybaddies page — "
                                              "cookies missing/expired? Re-export cookies.txt.")
                _jobs[jid] = j
            _save_state(); return
        _exp_update(tok, note=f"queuing {len(pairs)} videos…")
        for v, creator in pairs:
            _unmark_removed(v)                   # explicit model/tag re-submit re-enables deleted videos
            _queue_job(v, fmt, quality, audio, uploader=_safe_name(creator) or listname)
        _note_staged(); _save_state()
    finally:
        _exp_done(tok)


# ── Deep tag scan: open the album page itself and read its #hashtags ───────────
# A listing title can be clean ("Sexy Compilation") while the album is tagged #trans
# / #shemale / #ts. This fetches the album page and screens its real title, tags, and
# per-video captions. Verdicts are cached per album id, so it's one request per album, ever.
ALBUM_FILTER_FILE = BASE / "album_filter_cache.json"
_album_block: Dict[str, bool] = {}
_album_block_lock = threading.Lock()

def _load_album_block():
    global _album_block
    try:    _album_block = json.loads(ALBUM_FILTER_FILE.read_text(encoding="utf-8"))
    except Exception: _album_block = {}

def _save_album_block():
    try:    _atomic_json(ALBUM_FILTER_FILE, _album_block)
    except Exception: pass

def _album_texts(html: str) -> str:
    """Only THIS album's own text: its real title + its #hashtags. Deliberately scoped —
       we must NOT read erome's global nav (it has a 'TRANS' category link on every page)
       or the 'More posts' section (other albums' captions), or everything false-blocks."""
    texts = []
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html, re.I)
    if m: texts.append(m.group(1))
    for mt in re.finditer(r'<a class="album-tag"[^>]*?(?:q=([^"&\']+))?"[^>]*>\s*([^<]+?)\s*</a>', html, re.I):
        if mt.group(1): texts.append(urllib.parse.unquote_plus(mt.group(1)))
        if mt.group(2): texts.append(mt.group(2))
    return " \n ".join(texts)

def _album_blocked_by_tags(album_id: str, html: str = None) -> bool:
    """True if an album's own title/#tags look like TS/Trans (etc.). Cached per id.
       On rate-limit/failure it returns False WITHOUT caching, so it's re-checked later
       (better to re-check than to permanently mis-cache a verdict we couldn't compute)."""
    with _album_block_lock:
        if album_id in _album_block:
            return _album_block[album_id]
    if html is None:
        for attempt in range(3):                       # ride out a 429 with backoff
            try:
                html = _http_get(f"https://www.erome.com/a/{album_id}")
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    time.sleep(5 * (attempt + 1)); continue
                return False                           # give up -> don't block, don't cache
    blocked = _is_blocked_title(_album_texts(html))
    if not blocked:                               # also: who POSTED it? (catches unlabeled albums)
        m = re.search(r'\balt="([^"]+)\'s avatar"', html)
        if m and _is_blocked_poster(m.group(1)):
            blocked = True
    with _album_block_lock:
        _album_block[album_id] = blocked
        _save_album_block()
    return blocked

def _album_id_from_url(url: str):
    m = re.search(r"/a/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else None

_load_album_block()


# ── Profile totals: learn each profile's TOTAL album count (for done/total) ────
TOTALS_FILE = BASE / "profile_totals.json"
_totals: Dict[str, int] = {}
_totals_lock = threading.Lock()
_totals_seen: Dict[str, float] = {}

def _load_totals():
    global _totals
    try:    _totals = json.loads(TOTALS_FILE.read_text(encoding="utf-8"))
    except Exception: _totals = {}

def _save_totals():
    try:    _atomic_json(TOTALS_FILE, _totals)
    except Exception: pass

_load_totals()

def _disk_album_ids(folder):
    """Distinct album ids that have at least one file in a profile folder."""
    ids = set()
    try:
        for f in folder.iterdir():
            if f.is_file() and f.suffix != ".part":
                m = re.search(r"\[([A-Za-z0-9]+)(?:-\d+)?\]", f.name)
                if m: ids.add(m.group(1))
    except Exception: pass
    return ids

def _totals_filler():
    """Background: gently learn each profile's total album count (each at most every 6h)."""
    while True:
        time.sleep(45)
        _beat("totals")
        try:
            now, target = time.time(), None
            for sub in SUBDIRS.values():
                base = DL_ROOT / sub
                if not base.is_dir(): continue
                for d in base.iterdir():
                    if d.is_dir() and now - _totals_seen.get(d.name, 0) > 21600:
                        target = d.name; break
                if target: break
            if not target: continue
            _totals_seen[target] = now
            albums = _profile_albums(f"https://www.erome.com/{target}")
            if albums:
                with _totals_lock:
                    _totals[target] = len(set(u.rsplit("/", 1)[-1] for u in albums))
                _save_totals()
        except Exception: pass

_id_lock = threading.Lock(); _id_n = 0
def _new_jid() -> str:
    global _id_n
    with _id_lock:
        _id_n += 1
        return f"{int(time.time()*1000)}{_id_n:03d}"

def _queue_job(url, fmt, quality, audio, uploader="", tier=1):
    """Queue one job unless this URL is already pending. Returns id or None.
       tier 0 = a standalone post you submitted (served before profile-scan jobs);
       tier 1 = queued by a profile/model/tag scan."""
    if _is_blocked_poster(uploader):
        return None                                  # blocked poster -> never queued
    if _album_id_from_url(url) in _dead_albums:
        return None                                  # content confirmed gone -> don't re-queue
    if _is_removed(url):
        return None                                  # user deleted it -> don't auto-re-queue
    with _lock:
        for j in _jobs.values():
            if j.url == url and j.status in ("queued", "downloading"):
                return None
    jid = _new_jid()
    with _lock:
        job = Job(jid, url, fmt, quality, audio, uploader=uploader)
        job.tier = tier
        _jobs[jid] = job
    _stage_put(jid); _note_staged()
    return jid

def _bump_priority(url):
    """A URL re-submitted while it's already pending -> FOCUS on it: bump the existing job to
       the top tier and re-stage it. No new job is created, so there's never duplicate content;
       the re-staged copy is harmless because _download only runs a job while it's 'queued'.
       Returns the existing job's id, or None if it isn't currently queued/downloading."""
    restage = jid = None
    with _lock:
        j = next((x for x in _jobs.values()
                  if x.url == url and x.status in ("queued", "downloading")), None)
        if j:
            jid = j.id
            if j.status == "queued" and j.tier != 0:
                j.tier = 0; restage = jid          # move it to the front of the line
    if restage:
        _stage_put(restage)
    return jid

def _expand_and_queue(url, fmt, quality, audio):
    """Background: scrape a profile and queue each album (archive skips old ones)."""
    m = re.match(r"https?://(?:www\.)?erome\.com/([^/?#]+)", url)
    uploader = m.group(1) if m else ""          # the poster's name -> own folder
    if _is_blocked_poster(uploader):
        jid = _new_jid()
        with _lock:
            j = Job(jid, url, fmt, quality, audio, uploader=uploader)
            j.status, j.error = "error", "Poster is on your blocklist."
            _jobs[jid] = j
        _save_state(); return
    tok = _exp_start(url, "erome")
    try:
        albums = _profile_albums(url, progress=lambda pg, n: _exp_update(
            tok, found=n, note=f"scanning page {pg}… {n} albums found"))
        if not albums:
            jid = _new_jid()
            with _lock:
                j = Job(jid, url, fmt, quality, audio, uploader=uploader)
                j.status, j.error = "error", "Couldn't read that profile (login required or unsupported)."
                _jobs[jid] = j
            _save_state(); return
        if uploader:                            # remember this profile's total (free, reuses the scrape)
            with _totals_lock:
                _totals[uploader] = len(albums)
            _save_totals(); _totals_seen[uploader] = time.time()
        # Skip albums already logged in the archive or present on disk — a re-run then only
        # queues genuinely NEW posts instead of thousands of jobs that would just skip.
        have = _archive_album_ids() | (_disk_album_ids_all(uploader) if uploader else set())
        fresh = [a for a in albums if a.rsplit("/", 1)[-1] not in have]
        skipped = len(albums) - len(fresh)
        _exp_update(tok, note=(f"queuing {len(fresh)} new album(s)"
                               + (f" · skipped {skipped} already downloaded" if skipped else "")))
        for album in fresh:
            _unmark_removed(album)               # explicit profile re-submit re-enables deleted albums
            _queue_job(album, fmt, quality, audio, uploader=uploader)
            _save_state()
    finally:
        _exp_done(tok)


# ── Prioritizer + worker pool ─────────────────────────────────────────────────
# Quick/small jobs run first; heavy ones sink to the back of the queue.
def _probe(url):
    """Cheaply estimate (bytes, count, title, thumb): size-sort + show info before download."""
    try:
        with yt_dlp.YoutubeDL({**_base_opts(archive=False), "extract_flat": "in_playlist",
                               "skip_download": True, "socket_timeout": 15}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return 50_000_000, 0, "", ""
    if not info:
        return 50_000_000, 0, "", ""
    entries = info.get("entries")
    items = [e for e in entries if e] if entries is not None else [info]
    total = sum((e.get("filesize") or e.get("filesize_approx") or 0) for e in items)
    n = len(items)
    weight = total if total else max(1, n) * 20_000_000   # fallback ~20MB/item
    return weight, n, (info.get("title") or ""), (info.get("thumbnail") or "")

def _prioritizer():
    global _pq_seq
    while True:
        _st_tier, _st_seq, jid = _stage.get()      # tier-ordered: posts probed before scan jobs
        weight = 50_000_000
        try:
            with _lock:
                job = _jobs.get(jid)
            if job:
                try:
                    # Hard 25-second cap on the metadata probe — prevents a slow/stalled
                    # erome connection from hanging the entire prioritizer pipeline.
                    weight, n, title, thumb = _probe_pool.submit(_probe, job.url).result(timeout=25)
                    if n: job.items_total = n
                    if title and (not job.title or job.title == job.url): job.title = title
                    if thumb and not job.thumb: job.thumb = thumb
                    # As soon as we learn the real title: if it's TS/Trans (etc.), drop it now
                    # instead of queueing it for download.
                    if _is_blocked_title(job.title):
                        with _lock:
                            _jobs.pop(jid, None)
                        _save_state(); _stage.task_done(); continue
                    _save_state()
                except Exception:
                    pass          # timeout or probe error — use default weight, still queued
            # NOTE: the deep #tag scan runs at DOWNLOAD time (see _download), not here —
            # doing it for every queued album at once floods erome with requests (HTTP 429).
            # The download-time gate is naturally paced by the worker pool, and its verdict
            # is cached so the resolver can then purge any siblings of a blocked album.
        except Exception:
            pass
        with _lock:
            j = _jobs.get(jid)
            if j: j.size_est = weight              # remember the estimate for disk-fit checks
            tier = int(j.tier) if j else 1         # 0 = standalone post -> served before scan jobs
        with _seq_lock:
            _pq_seq += 1; seq = _pq_seq
        # order: tier first (posts before profile scans), then weight (smallest first), then seq (FIFO)
        _pq.put((tier, weight, seq, jid))
        _stage.task_done()

def _worker():
    while True:
        # warmup gate: wait for the size-sorted buffer so the smallest go first
        while True:
            with _warm_lock:
                dl = _warm_deadline
            if dl is not None and (_stage.empty() or time.time() >= dl):
                break
            time.sleep(0.3)
        _run.wait()                       # block here while paused (before touching the queue)
        _tier, _weight, _seq, jid = _pq.get()
        # Re-check AFTER dequeuing: a worker can sit blocked inside _pq.get() when the queue
        # is huge (the prioritizer feeds it gradually). If the user paused during that wait,
        # gate here so the just-pulled job isn't downloaded until we resume. Without this,
        # pause "leaks" — workers keep starting new downloads while supposedly paused.
        _run.wait()
        try:
            _download(jid)
        except Exception as exc:
            _log(f"worker error {jid}: {exc}")
            with _lock:
                j = _jobs.get(jid)
            if j:
                j.status = "error"; j.error = str(exc)
            _save_state()
        finally:
            with _cancel_lock:
                _cancel.discard(jid)
            _pq.task_done()


# ── Telegram push: send each finished download to a chat, then (optionally) delete it ──
# One background thread owns a Telethon client on its own asyncio loop. Downloads enqueue a
# (path, caption) tuple; the worker uploads with the user account (2 GB limit) and, on a
# confirmed success, removes the local file so the drive doesn't fill up.
_tg_queue: queue.Queue = queue.Queue()
_tg_state = {"configured": TG_ENABLED, "ready": False, "sent": 0, "failed": 0,
             "pending": 0, "chat": "", "error": "", "last": ""}
TG_SESSION = BASE / "telegram"          # -> telegram.session (SQLite, shared with the login script)
_TG_PLACEHOLDERS = ("already downloaded", "skipped")

def _tg_enqueue(path, caption=""):
    """Queue a finished file for upload (no-op unless Telegram is enabled)."""
    if not TG_ENABLED or not path:
        return
    p = Path(path)
    name = p.name.lower()
    if any(ph in name for ph in _TG_PLACEHOLDERS):     # 'skipped'/'already downloaded' aren't real files
        return
    _tg_queue.put((str(p), caption or p.stem))
    _tg_state["pending"] = _tg_queue.qsize()

async def _tg_resolve_chat(client, chat):
    """Find the target chat by numeric id, @username, or exact title (case-insensitive)."""
    if not chat:
        return None
    for attempt in (int, str):                          # try id first, then username/title
        try:
            return await client.get_entity(attempt(chat))
        except Exception:
            pass
    try:
        async for d in client.iter_dialogs():
            if (d.name or "").strip().lower() == chat.strip().lower():
                return d.entity
    except Exception:
        pass
    return None

def _telegram_worker():
    """Background uploader: connects once, then drains _tg_queue forever."""
    try:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError
    except Exception as e:
        _tg_state["error"] = f"Telethon not available: {e}"; _log(f"telegram: {e}"); return
    if not (TG_API_ID and TG_API_HASH):
        _tg_state["error"] = "missing api_id / api_hash — run telegram_login.py"; return
    import asyncio
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)

    async def _run():
        client = TelegramClient(str(TG_SESSION), int(TG_API_ID), TG_API_HASH)
        try:
            await client.connect()
        except Exception as e:
            _tg_state["error"] = f"connect failed: {e}"; _log(f"telegram connect: {e}"); return
        if not await client.is_user_authorized():
            _tg_state["error"] = "not logged in — run telegram_login.py once"
            _log("telegram: not logged in"); await client.disconnect(); return
        entity = await _tg_resolve_chat(client, TG_CHAT)
        if entity is None:
            _tg_state["error"] = f"chat not found: {TG_CHAT!r} — check telegram_chat in config.json"
            _log(f"telegram: chat not found {TG_CHAT!r}"); await client.disconnect(); return
        _tg_state.update(ready=True, error="",
                         chat=(getattr(entity, "title", None) or getattr(entity, "username", None) or TG_CHAT))
        _log(f"telegram: ready -> sending to {_tg_state['chat']!r}")

        while True:
            path, caption = await loop.run_in_executor(None, _tg_queue.get)   # block off the event loop
            _tg_state["pending"] = _tg_queue.qsize()
            p = Path(path)
            if not p.exists():
                continue
            sent = False
            for tries in range(3):
                try:
                    await client.send_file(entity, str(p), caption=caption[:1000],
                                           supports_streaming=True, part_size_kb=512,
                                           force_document=False)
                    sent = True
                    break
                except FloodWaitError as fw:
                    _log(f"telegram flood-wait {fw.seconds}s"); await asyncio.sleep(fw.seconds + 2)
                except Exception as e:
                    _tg_state["error"] = str(e)[:200]; _log(f"telegram send fail: {e}")
                    await asyncio.sleep(5)
            if sent:
                _tg_state["sent"] += 1; _tg_state["last"] = p.name; _tg_state["error"] = ""
                if TG_DELETE_AFTER:
                    try: p.unlink()
                    except Exception as e: _log(f"telegram: uploaded but couldn't delete {p.name}: {e}")
                _log(f"telegram: sent {p.name}")
            else:
                _tg_state["failed"] += 1                # leave the file on disk for a later manual retry

    try:
        loop.run_until_complete(_run())
    except Exception as e:
        _tg_state["error"] = str(e)[:200]; _log(f"telegram worker crashed: {e}")


# ── Conflict resolver: a self-healing watchdog that keeps the queue moving ─────
# Runs forever. Re-queues failed jobs (transient errors like rate-limits) and purges
# blocked content that slipped into the queue — so the app recovers on its own.
RESOLVER_MAX_TRIES = 8

def _resolver():
    while True:
        time.sleep(60)
        _beat("resolver")
        try:
            now, fix, purge, dead, resume = time.time(), [], [], [], []
            with _lock:
                for jid, j in _jobs.items():
                    # Mega quota-wait: the download isn't holding a worker while it waits.
                    # Re-dispatch once the stated reset time passes; else refresh the countdown.
                    if j.resume_at and j.status == "downloading":
                        if now >= j.resume_at:
                            # back to 'queued' so it passes _download's claim gate on re-dispatch
                            j.resume_at = 0.0; j.status = "queued"; resume.append(jid)
                        else:
                            j.eta = "resume in " + _fmt_dur(int(j.resume_at - now))
                        continue
                    # GUARD: drop anything blocked — by title, by a cached tag verdict,
                    # or by poster. Purges content that slipped in via a re-queue.
                    if j.status in ("queued", "downloading") and (
                            _is_blocked_title(j.title)
                            or _is_blocked_poster(j.uploader)
                            or _album_block.get(_album_id_from_url(j.url)) is True):
                        purge.append(jid); continue
                    if j.status == "error":
                        # dead content (404/gone/private) gets only a few tries, then is
                        # dropped + remembered; everything else retries the full 8 times.
                        deadish = _is_dead_error(j.error)
                        cap = DEAD_MAX_TRIES if deadish else RESOLVER_MAX_TRIES
                        if j.tries >= cap:
                            if deadish:
                                dead.append(jid)        # gone for good -> remove + remember
                            continue                    # otherwise leave it visible, stop retrying
                        # dead content: one quick (30s) confirming retry; transient: patient backoff
                        delay = 30 if deadish else 60 * (j.tries + 1)
                        if now - (j.started or 0) > delay:
                            j.tries += 1; j.status = "queued"; j.error = ""
                            fix.append(jid)
                    # (stalled downloads are killed by the monitor in _download — a child
                    #  process can always be killed, unlike a wedged thread.)
                    # re-kick jobs stuck in queued for too long (probe may have been hanging)
                    elif (j.status == "queued" and j.started and j.tries > 0
                          and now - j.started > 60 * (j.tries + 1) + 300
                          and j.tries < RESOLVER_MAX_TRIES):
                        fix.append(jid)                                         # re-add to stage
            for jid in purge:
                with _cancel_lock:
                    _cancel.add(jid)                 # abort it if a worker is mid-download
                with _lock:
                    _jobs.pop(jid, None)
            for jid in dead:
                with _lock:
                    jd = _jobs.pop(jid, None)
                if jd: _mark_dead(jd.url)             # won't be re-queued by the profile rescan
            for jid in fix + resume:
                _stage_put(jid)
            orphans = _prune_work_orphans()          # sweep stale _work temp files
            if fix or purge or dead or resume:
                _log(f"resolver: retried {len(fix)}, purged {len(purge)}, "
                     f"dropped-dead {len(dead)}, mega-resume {len(resume)}, work-pruned {orphans}")
                _note_staged() if (fix or resume) else None
                _save_state()
        except Exception as e:
            _log(f"resolver error: {e}")


# ── Integrity scan: find & heal corrupt downloads ─────────────────────────────
# After the queue empties (or on demand) every finished file is verified playable.
# Truncated/incomplete downloads — the common failure — have an unreadable container,
# so a fast header check catches them. Corrupt files are deleted, their archive id is
# cleared, and the album is re-queued so the missing item downloads again cleanly.
_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".flv"}
_AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".aac", ".wav", ".flac", ".ogg"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MEDIA_EXTS = _VIDEO_EXTS | _AUDIO_EXTS | _IMAGE_EXTS

VERIFY_HEAL_MAX = 2                          # re-download an album at most this many times/session
_verify_cache: Dict[str, str] = {}          # relpath -> "size:mtime" of files confirmed intact
_verify_lock = threading.Lock()
_verify_state = {"running": False, "checked": 0, "corrupt": 0, "requeued": 0,
                 "partials": 0, "freed": 0, "last_run": 0.0, "summary": "", "corrupt_files": []}

# Leftover temp files from a killed/canceled/crashed download — never a finished item.
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp", ".download", ".crdownload")
def _is_partial_file(name: str) -> bool:
    n = name.lower()
    return n.endswith(_PARTIAL_SUFFIXES) or ".part-frag" in n or ".part-" in n

def _sweep_partials(active_dirs: set) -> tuple:
    """Delete stale partial/temp files left by interrupted downloads (cancel, stall, crash).
       Skips files touched in the last 2 min (an in-flight download writes its .part live) and
       anything under a folder that currently has an active job. Returns (count, bytes_freed)."""
    cutoff = time.time() - 120
    count = freed = 0
    try:
        for f in DL_ROOT.rglob("*"):
            try:
                if not f.is_file() or not _is_partial_file(f.name):
                    continue
                if str(f.parent) in active_dirs:          # a job is writing in this folder
                    continue
                st = f.stat()
                if st.st_mtime > cutoff:                   # likely still being written -> leave it
                    continue
                sz = st.st_size
                f.unlink()
                count += 1; freed += sz
            except Exception:
                continue
    except Exception:
        pass
    return count, freed
_verify_heal_count: Dict[str, int] = {}     # albumid -> times re-queued (infinite-loop guard)
_ARCHIVE_ID_RE = re.compile(r"\[([A-Za-z0-9]+-\d+)\]")   # [albumid-index] in the filename

def _load_verify_cache():
    global _verify_cache
    try:    _verify_cache = json.loads(VERIFY_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception: _verify_cache = {}

def _save_verify_cache():
    try:    _atomic_json(VERIFY_CACHE_FILE, _verify_cache)
    except Exception: pass

_load_verify_cache()

def _file_sig(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"

def _ffcheck(path: Path, deep: bool):
    """Run ffmpeg once. Returns True=intact, False=definitely broken, None=inconclusive.
       'Inconclusive' (timeout / subprocess error) must NEVER cause a delete."""
    ext = path.suffix.lower()
    try:
        if ext in _IMAGE_EXTS or deep:
            # full decode — proves every frame reads (catches mid-file corruption too)
            r = subprocess.run([str(FFMPEG_EXE), "-v", "error", "-i", str(path), "-f", "null", "-"],
                               capture_output=True, text=True,
                               timeout=900 if deep else 90, creationflags=_NO_WINDOW)
            return True if (r.returncode == 0 and not r.stderr.strip()) else False
        # fast path — a readable container/streams (catches truncation & missing moov)
        r = subprocess.run([str(FFMPEG_EXE), "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True, timeout=45, creationflags=_NO_WINDOW)
        out = r.stderr or ""
        if ("Duration:" in out and "Duration: N/A" not in out) or ("Stream #" in out):
            return True                           # playable
        # Only call it broken on an EXPLICIT corruption signature. A bare non-empty stderr
        # (a warning on an odd-but-playable file) is inconclusive -> keep, not delete. This
        # avoids the false-delete/re-download churn seen on borderline files under load.
        if re.search(r"moov atom not found|invalid data|truncat|corrupt|"
                     r"could not find codec|does not contain any stream|"
                     r"end of file|partial file|invalid argument", out, re.I):
            return False
        return None                               # no usable verdict -> can't tell -> keep
    except subprocess.TimeoutExpired:
        return None                               # busy/slow -> inconclusive, retry later
    except Exception:
        return None

def _file_is_intact(path: Path, deep: bool = False) -> bool:
    """True if the file is a complete, readable media file. Only returns False when
       ffmpeg AFFIRMATIVELY reports the file is broken (or it's a sub-min-size stub) —
       a transient timeout under load is retried, then given the benefit of the doubt."""
    try:    sz = path.stat().st_size
    except Exception: return True                 # can't stat -> never delete
    if sz < VERIFY_MIN_KB * 1024:
        return False                              # empty / error-page stub = definitely junk
    if not FFMPEG_EXE.exists():
        return True                               # no verifier -> trust the size check
    v = _ffcheck(path, deep)
    if v is None:                                 # inconclusive -> one fresh retry
        time.sleep(0.4)
        v = _ffcheck(path, deep)
    return True if v is None else v               # still unsure -> KEEP (never delete unverifiable)

def _remove_archive_ids(ids: set):
    """Drop the given 'albumid-index' tokens from download_archive.txt so they re-download."""
    if not ids or not ARCHIVE_FILE.exists():
        return
    try:
        lines = ARCHIVE_FILE.read_text(encoding="utf-8").splitlines()
        kept  = [ln for ln in lines if ln.strip().split(" ")[-1] not in ids]
        # atomic: write a temp file then os.replace, so a crash mid-write can't truncate
        # or corrupt the dedup ledger (a corrupt archive => mass re-downloads).
        tmp = ARCHIVE_FILE.with_suffix(ARCHIVE_FILE.suffix + ".tmp")
        tmp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
        os.replace(tmp, ARCHIVE_FILE)
    except Exception:
        pass

def _integrity_scan(deep=None, heal=None) -> dict:
    """Verify finished media; delete + re-download anything corrupt. Returns a summary."""
    if deep is None: deep = VERIFY_DEEP
    if heal is None: heal = VERIFY_HEAL
    with _verify_lock:
        if _verify_state["running"]:
            return {"already_running": True}
        _verify_state.update(running=True, checked=0, corrupt=0, requeued=0,
                             partials=0, freed=0, corrupt_files=[])
    checked = corrupt = requeued = parts = freed = 0
    bad_ids: set = set()                 # archive ids to clear
    requeue: Dict[str, str] = {}         # album url -> uploader (folder)
    bad_names: list = []                 # filenames flagged (for the UI / log)
    try:
        with _lock:                      # never touch files tied to in-flight work
            active = {j.url for j in _jobs.values() if j.status in ("downloading", "queued")}
            active_dirs = {str((DL_ROOT / SUBDIRS.get(j.type, "videos") /
                                _safe_name(j.uploader)) if j.uploader else DL_ROOT / SUBDIRS.get(j.type, "videos"))
                           for j in _jobs.values() if j.status == "downloading"}
        # ── sweep leftover partials from canceled/interrupted downloads (half-a-page cancels) ──
        parts, freed = _sweep_partials(active_dirs)
        with _verify_lock:
            _verify_state.update(partials=parts, freed=freed)
        for f in DL_ROOT.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in _MEDIA_EXTS:
                continue
            rel = str(f.relative_to(BASE))
            try:    sig = _file_sig(f)
            except Exception: continue
            if _verify_cache.get(rel) == sig:
                continue                 # unchanged & already verified -> skip (fast re-scan)
            m     = _ARCHIVE_ID_RE.search(f.name)
            album = ("https://www.erome.com/a/" + m.group(1).rsplit("-", 1)[0]) if m else None
            if album and album in active:
                continue                 # its album is downloading right now
            checked += 1
            if checked % 15 == 0:        # keep the live progress counter moving
                with _verify_lock:
                    _verify_state.update(checked=checked, corrupt=corrupt)
            if _file_is_intact(f, deep=deep):
                _verify_cache[rel] = sig
                continue
            # ── corrupt ──
            corrupt += 1
            if len(bad_names) < 50: bad_names.append(f.name)
            uploader = (f.parent.name if f.parent.parent.name in SUBDIRS.values() else "")
            if heal:
                if m: bad_ids.add(m.group(1))
                try:    f.unlink()
                except Exception: pass
                _verify_cache.pop(rel, None)
                if album:
                    aid = m.group(1).rsplit("-", 1)[0]
                    if _verify_heal_count.get(aid, 0) < VERIFY_HEAL_MAX:
                        requeue[album] = uploader
            with _verify_lock:
                _verify_state.update(checked=checked, corrupt=corrupt, corrupt_files=list(bad_names))
        if heal and bad_ids:
            _remove_archive_ids(bad_ids)
        if heal:
            for album, uploader in requeue.items():
                aid = album.rsplit("/", 1)[-1]
                _verify_heal_count[aid] = _verify_heal_count.get(aid, 0) + 1
                if _queue_job(album, "mp4", "best", False, uploader=uploader):
                    requeued += 1
            if requeued:
                _note_staged()
        _save_verify_cache()
    finally:
        summary = f"checked {checked}, corrupt {corrupt}, re-downloading {requeued}"
        if parts:
            summary += f", cleaned {parts} partial{'s' if parts != 1 else ''} ({freed/1048576:.0f} MB)"
        with _verify_lock:
            _verify_state.update(running=False, checked=checked, corrupt=corrupt,
                                 requeued=requeued, partials=parts, freed=freed,
                                 last_run=time.time(), summary=summary, corrupt_files=list(bad_names))
        if corrupt or parts:
            _save_state()
    return {"checked": checked, "corrupt": corrupt, "requeued": requeued,
            "partials": parts, "freed": freed, "files": bad_names}


# ── Completeness scan: make sure every post on a profile was actually downloaded ──
# Re-scrapes each profile, diffs the site's album list against what's on disk + in the
# archive, and re-queues any whole posts that are missing — so nothing is left behind.
COMPLETE_THROTTLE = 600                      # don't re-scrape the same profile more often than this
_complete_lock = threading.Lock()
_complete_state = {"running": False, "profiles": 0, "missing": 0, "requeued": 0,
                   "last_run": 0.0, "summary": "", "detail": []}
_complete_seen: Dict[str, float] = {}        # uploader -> last scrape time (throttle)
_complete_requeued: set = set()              # album ids re-queued via completeness (loop guard)

def _all_profiles() -> list:
    """Every uploader that has a download folder, across all media types."""
    names = set()
    for sub in SUBDIRS.values():
        base = DL_ROOT / sub
        if base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and not _is_blocked_poster(d.name):
                    names.add(d.name)
    return sorted(names)

def _archive_album_ids() -> set:
    """Distinct album ids recorded as downloaded in the archive (strip the -index)."""
    ids = set()
    try:
        for ln in ARCHIVE_FILE.read_text(encoding="utf-8").splitlines():
            tok = ln.strip().split(" ")[-1]
            if tok:
                ids.add(tok.rsplit("-", 1)[0])
    except Exception:
        pass
    return ids

def _disk_album_ids_all(uploader: str) -> set:
    """Album ids that have at least one file on disk for this uploader (any media type)."""
    ids = set()
    for sub in SUBDIRS.values():
        folder = DL_ROOT / sub / uploader
        if folder.is_dir():
            ids |= _disk_album_ids(folder)
    return ids

def _completeness_scan(profiles=None, force=False, requeue=None) -> dict:
    """Re-scrape profiles; re-queue any posts missing from disk+archive. Returns a summary."""
    if requeue is None: requeue = VERIFY_HEAL
    if profiles is None: profiles = _all_profiles()
    with _complete_lock:
        if _complete_state["running"]:
            return {"already_running": True}
        _complete_state.update(running=True, profiles=0, missing=0, requeued=0, detail=[])
    now = time.time()
    have_archive = _archive_album_ids()
    n_prof = total_missing = total_requeued = 0
    detail = []
    try:
        for up in profiles:
            if not force and now - _complete_seen.get(up, 0) < COMPLETE_THROTTLE:
                continue                                  # scraped recently -> skip (rate-limit)
            site = _profile_albums(f"https://www.erome.com/{up}")
            _complete_seen[up] = time.time()
            if not site:
                continue                                  # couldn't read profile -> skip
            site_ids = [u.rsplit("/", 1)[-1] for u in site]
            have     = have_archive | _disk_album_ids_all(up)
            missing  = [a for a in site_ids if a not in have
                        and not _is_removed(f"https://www.erome.com/a/{a}")]   # skip user-deleted
            with _totals_lock:                            # refresh done/total for the library view
                _totals[up] = len(site_ids)
            _save_totals()
            n_prof += 1
            if missing:
                total_missing += len(missing)
                detail.append({"profile": up, "total": len(site_ids),
                               "have": len(site_ids) - len(missing), "missing": len(missing)})
                if requeue:
                    for aid in missing:
                        if aid in _complete_requeued:     # don't re-queue the same post twice/session
                            continue
                        _complete_requeued.add(aid)
                        if _queue_job(f"https://www.erome.com/a/{aid}", "mp4", "best",
                                      False, uploader=up):
                            total_requeued += 1
            with _complete_lock:
                _complete_state.update(profiles=n_prof, missing=total_missing,
                                       requeued=total_requeued, detail=list(detail))
        if total_requeued:
            _note_staged(); _save_state()
    finally:
        summary = f"{n_prof} profiles, {total_missing} missing post(s), re-downloading {total_requeued}"
        with _complete_lock:
            _complete_state.update(running=False, last_run=time.time(),
                                   summary=summary, detail=list(detail))
    return {"profiles": n_prof, "missing": total_missing, "requeued": total_requeued, "detail": detail}


def _integrity_watch():
    """When the queue goes busy -> idle (downloads done): verify files aren't corrupt,
       then re-scrape each profile to make sure no posts are missing."""
    was_busy = False
    while True:
        time.sleep(20)
        _beat("integrity")
        if not (VERIFY_AFTER or VERIFY_PROFS):
            was_busy = False
            continue
        with _lock:
            busy = any(j.status in ("downloading", "queued") for j in _jobs.values())
        if was_busy and not busy and _run.is_set():
            time.sleep(8)                # let the last file finish flushing to disk
            with _lock:
                still = any(j.status in ("downloading", "queued") for j in _jobs.values())
            if not still:
                if VERIFY_AFTER:
                    try:    _integrity_scan()
                    except Exception: pass
                if VERIFY_PROFS:
                    try:    _completeness_scan()          # throttled per-profile
                    except Exception: pass
        was_busy = busy


def _tag_sweeper():
    """Gently open one queued album's page at a time and purge it if its #tags are TS/Trans.
       Paced (~1 request / 3s) so it never floods erome — the opposite of the burst that
       got us rate-limited. Verdicts are cached; the download-time gate is the backstop."""
    while True:
        time.sleep(3)                                  # ~20 checks/min — safe under erome's limit
        _beat("tag_sweeper")
        if not _run.is_set():
            continue
        target = None
        with _lock:
            for jid, j in _jobs.items():
                if j.status == "queued":
                    aid = _album_id_from_url(j.url)
                    if aid and aid not in _album_block:        # only albums not yet tag-checked
                        target = (jid, aid); break
        if not target:
            continue
        jid, aid = target
        try:
            if _album_blocked_by_tags(aid):            # fetch + cache (handles 429 backoff)
                with _cancel_lock:
                    _cancel.add(jid)
                with _lock:
                    _jobs.pop(jid, None)
                _save_state()
        except Exception:
            pass


# ── Subprocess download workers ───────────────────────────────────────────────
# Every download runs in a CHILD PROCESS, not a thread. A thread wedged inside a native
# socket/SSL call can never be killed (the cause of both multi-day freezes); a process
# always can. The child reports progress as JSON lines in a file; the parent monitors it
# and force-kills the whole process tree if the download stalls or the user cancels.
WORK_DIR = BASE / "_work"
WORK_DIR.mkdir(exist_ok=True)

def _prune_workdir():
    cut = time.time() - 3 * 86400              # keep a few days of error logs for diagnosis
    for f in WORK_DIR.glob("*"):
        try:
            if f.stat().st_mtime < cut: f.unlink()
        except Exception: pass

def _prune_work_orphans():
    """Delete _work temp files (err_/spec_/prog_/mega_) whose job is no longer tracked.
       Runs each resolver cycle so these don't pile up over a long session (an active
       job keeps its jid in _jobs, so its live spec_/prog_ files are never touched)."""
    try:
        with _lock:
            live = set(_jobs.keys())
        removed = 0
        for p in WORK_DIR.glob("*"):
            for prefix in ("err_", "spec_", "prog_", "mega_"):
                if p.name.startswith(prefix):
                    if p.stem[len(prefix):] not in live:
                        try: p.unlink(); removed += 1
                        except Exception: pass
                    break
        return removed
    except Exception:
        return 0

def _kill_tree(pid: int):
    """Kill a worker AND its children (PyInstaller bootloader + python + ffmpeg/aria2)."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=15, creationflags=_NO_WINDOW)
    except Exception:
        try: os.kill(pid, 9)
        except Exception: pass

# ── Orphan-worker guard ───────────────────────────────────────────────────────
# Downloads run in child processes. Two ways they could be orphaned: (1) the app exits
# while a download is running (graceful shutdown OR force-kill/crash), (2) the parent dies
# before the cancel path can kill the child. We defend on BOTH sides:
#   parent → track live workers, kill them on every graceful exit path (api_shutdown, atexit)
#   child  → poll whether the parent is still alive and self-terminate its whole tree if not
# The child-side watch is the backstop that also covers force-kill/crash (the parent can't).
_active_procs = set()
_active_procs_lock = threading.Lock()
def _track_worker(p):
    with _active_procs_lock: _active_procs.add(p)
def _untrack_worker(p):
    with _active_procs_lock: _active_procs.discard(p)
def _kill_all_workers():
    with _active_procs_lock:
        procs = list(_active_procs); _active_procs.clear()
    for p in procs:
        try: _kill_tree(p.pid)
        except Exception: pass

def _pid_alive(pid) -> bool:
    """Does this PID belong to a running process? Windows-safe — never terminates it
       (os.kill(pid,0) on Windows would call TerminateProcess, so we must NOT use it)."""
    if not pid:
        return True                       # unknown parent -> assume alive (never self-kill)
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False               # can't open -> process is gone
            code = ctypes.c_ulong()
            k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return code.value == 259       # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(int(pid), 0); return True
    except ProcessLookupError: return False
    except PermissionError:    return True
    except Exception:          return True

def _watch_parent(ppid):
    """CHILD daemon thread: if the parent app disappears, kill our own process tree
       (self + any ffmpeg/aria2 we spawned) so a downloader never keeps running headless."""
    while True:
        time.sleep(3)
        if not _pid_alive(ppid):
            _kill_tree(os.getpid())        # taskkill /T on self reaps our children too
            os._exit(1)                    # belt-and-suspenders if taskkill was blocked

atexit.register(_kill_all_workers)         # normal exit / Ctrl+C (api_shutdown's os._exit calls it explicitly)

def _worker_cmd(spec_path):
    if getattr(sys, "frozen", False):           # the exe re-invokes itself in worker mode
        return [sys.executable, "--dlworker", str(spec_path)]
    return [sys.executable, str(Path(__file__).resolve()), "--dlworker", str(spec_path)]

def _apply_progress(job, ev):
    """Fold one child progress event into the job. Returns the 'end' event when seen."""
    if ev.get("e") == "end":
        return ev
    if ev.get("title") and (not job.title or job.title == job.url): job.title = ev["title"]
    if ev.get("thumb") and not job.thumb: job.thumb = ev["thumb"]
    if ev.get("n") and not job.items_total:
        try: job.items_total = int(ev["n"])
        except Exception: pass
    if ev.get("fn"): job.filename = ev["fn"]
    s = ev.get("s")
    if s == "downloading":
        job.speed, job.eta = ev.get("spd", ""), ev.get("eta", "")
        if job.items_total <= 1:
            try: job.progress = float(ev.get("pct") or 0)
            except Exception: pass
    elif s == "finished":
        try: job.items_done = max(job.items_done, int(ev.get("done") or 0))
        except Exception: pass
        job.progress = (round(job.items_done / job.items_total * 100, 1)
                        if job.items_total > 1 else 100.0)
    return None

def _tail_file(path, n=200):
    try:
        txt = Path(path).read_text(encoding="utf-8", errors="replace").strip()
        return txt[-n:] if txt else ""
    except Exception:
        return ""


def dl_worker_main(spec_path: str) -> int:
    """Entry point of the --dlworker CHILD process: run ONE download per the spec file,
       appending progress as JSON lines to spec['progress']. The parent owns the queue,
       state, and retries — this process only downloads, so killing it is always safe."""
    # windowed-exe children have no console; give Python real streams so nothing crashes
    if sys.stdout is None: sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None: sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except Exception:
        return 4
    prog_path = spec["progress"]
    ppid = spec.get("parent_pid")
    if ppid:                                       # orphan guard: die if the parent app is gone
        threading.Thread(target=_watch_parent, args=(ppid,), daemon=True).start()
    last_emit = [0.0]
    done_n    = [0]

    def emit(d):
        try:
            with open(prog_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        except Exception: pass

    def hook(d):
        s, now = d.get("status"), time.time()
        if s == "downloading" and now - last_emit[0] < 0.6:
            return                                   # throttle the progress spam
        last_emit[0] = now
        info = d.get("info_dict") or {}
        ev = {"e": "p", "s": s,
              "pct": (d.get("_percent_str") or "").strip().rstrip("%"),
              "spd": (d.get("_speed_str") or "").strip(),
              "eta": (d.get("_eta_str") or "").strip(),
              "fn":  os.path.basename(d.get("filename") or "")}
        if info.get("title"):     ev["title"] = info["title"]
        if info.get("thumbnail"): ev["thumb"] = info["thumbnail"]
        n = info.get("playlist_count") or info.get("n_entries")
        if n: ev["n"] = n
        if s == "finished":
            done_n[0] += 1
            ev["done"] = done_n[0]
        emit(ev)

    def pp_hook(d):
        if d.get("status") == "finished":
            fp = (d.get("info_dict") or {}).get("filepath", "")
            if fp: emit({"e": "p", "s": "pp", "fn": os.path.basename(fp)})

    # keyword filter inside the child (same rules as the parent)
    kws   = [str(k).lower() for k in spec.get("block_keywords") or []]
    words = [str(w).lower() for w in spec.get("block_words") or []]
    wre   = (re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\d*\b", re.I)
             if words else None)
    claimed = set()
    maxb = int(spec.get("max_filesize") or 0)
    def mfilter(info, *, incomplete=False):
        if incomplete: return None
        t = (info.get("title") or "").lower()
        if t and (any(k in t for k in kws) or (wre and wre.search(t))):
            return "blocked keyword (filtered)"
        if maxb:                                   # known size already over the cap -> skip cleanly
            sz = info.get("filesize") or info.get("filesize_approx")
            if sz and sz > maxb:
                gb = maxb / (1024 ** 3)
                return f"over the {gb:.0f} GB size limit ({sz/(1024**3):.1f} GB) — skipped"
        vid = info.get("id")
        if vid:
            key = (info.get("extractor") or "") + ":" + str(vid)
            if key in claimed:
                return "duplicate in this batch"
            claimed.add(key)
        return None

    cookies_tmp = None
    try:
        opts = dict(quiet=True, no_warnings=True, ignoreerrors=True,
                    noplaylist=bool(spec.get("noplaylist", False)),
                    format=spec["format"], outtmpl=spec["outtmpl"],
                    progress_hooks=[hook], postprocessor_hooks=[pp_hook],
                    postprocessors=spec.get("post") or [],
                    match_filter=mfilter,
                    extractor_retries=5, retries=10,
                    socket_timeout=spec.get("socket_timeout", 60),
                    sleep_interval_requests=spec.get("spacing", 1.0),
                    windowsfilenames=True, trim_file_name=120, extract_flat=False)
        if spec.get("archive"): opts["download_archive"] = spec["archive"]
        if spec.get("ffmpeg"):  opts["ffmpeg_location"]  = spec["ffmpeg"]
        if spec.get("impersonate"):
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget
                opts["impersonate"] = ImpersonateTarget.from_str(spec["impersonate"])
            except Exception: pass
        if spec.get("playlist_items"):
            opts["playlist_items"] = spec["playlist_items"]
        if spec.get("frag_concurrency"):
            # parallel fragment fetches for HLS/DASH; a no-op on single-file (direct mp4) sources,
            # so it speeds up YouTube/Twitch/m3u8 without touching erome/ebonybaddies throughput.
            opts["concurrent_fragment_downloads"] = int(spec["frag_concurrency"])
        if spec.get("max_filesize"):
            # abort a download once its (known) size exceeds the cap; the match_filter below
            # also skips it up-front when the size is known before the download even starts.
            opts["max_filesize"] = int(spec["max_filesize"])
        if spec.get("cookies_browser"):
            opts["cookiesfrombrowser"] = (spec["cookies_browser"],)   # live browser cookies
        if spec.get("cookies") and os.path.exists(spec["cookies"]):
            fd, cookies_tmp = tempfile.mkstemp(prefix="udck_", suffix=".txt"); os.close(fd)
            shutil.copy2(spec["cookies"], cookies_tmp)    # own copy: never corrupt the original
            opts["cookiefile"] = cookies_tmp
        if spec.get("aria2"):
            opts["external_downloader"] = spec["aria2"]
            opts["external_downloader_args"] = {"aria2c": ["-x16", "-s16", "-k", "1M",
                                                            "--summary-interval=0",
                                                            "--console-log-level=warn"]}
        # capture yt-dlp's "larger than max-filesize" notice so an over-size skip can be
        # reported clearly instead of looking like a silent success with no file.
        class _SizeLog:
            reason = ""
            def _scan(self, m):
                m = str(m)
                if "max-filesize" in m.lower() or "larger than max" in m.lower():
                    self.reason = "skipped — over the size limit"
            def debug(self, m):   self._scan(m)
            def info(self, m):    self._scan(m)
            def warning(self, m): self._scan(m)
            def error(self, m):   pass
        _slog = _SizeLog()
        opts["logger"] = _slog
        with yt_dlp.YoutubeDL(opts) as ydl:
            ret = ydl.download([spec["url"]])
        emit({"e": "end", "ret": int(ret or 0), "done": done_n[0], "skipped": _slog.reason})
        return 0 if (ret or 0) == 0 else 3
    except Exception as exc:
        emit({"e": "end", "ret": -1, "err": str(exc)[:300], "done": done_n[0]})
        return 3
    finally:
        if cookies_tmp:
            try: os.remove(cookies_tmp)
            except Exception: pass


# Thread pool for _probe() calls with hard timeout (prevents prioritizer from hanging
# on slow/unresponsive servers — erome in particular can stall indefinitely).
_probe_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(2, min(4, MAX_WORKERS // 3)), thread_name_prefix="prober")


def _download_mega(job, jid):
    """Run ONE Mega download attempt via MEGAcmd's mega-get (or megatools). Runs as a tracked,
       killable child. On the free-tier bandwidth quota it does NOT block the worker: it stamps
       job.resume_at with Mega's stated reset time and returns — the resolver re-dispatches the
       job when that time passes (which resumes via `-m`, skipping files already downloaded)."""
    kind, tool = _mega_tool()
    if not tool:
        with _lock:
            if jid in _jobs:
                job.status = "error"
                job.error  = ("Mega needs the free MEGAcmd tool. Install it from "
                              "https://mega.io/cmd (or drop megatools.exe next to the app), "
                              "then retry.")
        _save_state(); _log("mega: no tool found"); return

    target = DL_ROOT / SUBDIRS["video"] / "Mega"
    target.mkdir(parents=True, exist_ok=True)
    job.uploader = job.uploader or "Mega"
    def _snapshot():                          # recursive: also catches Mega FOLDER links
        return {str(p.relative_to(target)) for p in target.rglob("*")
                if p.is_file() and p.suffix.lower() not in (".tmp", ".part")}
    before = _snapshot()

    def _cancelled():
        with _lock:
            return (jid not in _jobs) or (jid in _cancel)

    # attempt counter lives on the job so it survives re-dispatches across the quota waits
    attempt = int(job.tries or 0) + 1
    job.tries = attempt
    if MEGA_AUTO_RESUME and attempt > MEGA_MAX_ATTEMPTS:
        with _lock:
            if jid in _jobs:
                job.status, job.resume_at = "error", 0.0
                job.error  = (f"Mega: still bandwidth-limited after {MEGA_MAX_ATTEMPTS} resume "
                              "attempts — try again later, or upgrade / log in for more quota.")
        _save_state(); _log(f"mega gave up {jid} after {MEGA_MAX_ATTEMPTS} attempts"); return

    if kind == "megatools":
        cmd = [tool, "dl", "--no-progress", "--path", str(target), job.url]
    elif tool.lower().endswith(".bat"):                    # mega-get.bat -> run through cmd
        cmd = ["cmd", "/c", tool, "-m", job.url, str(target)]   # -m merges = resume, skips existing
    else:
        cmd = [tool, "-m", job.url, str(target)]

    log_path = WORK_DIR / f"mega_{jid}.log"
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                                creationflags=_NO_WINDOW, cwd=str(target))
    except Exception as exc:
        logf.close()
        with _lock:
            if jid in _jobs: job.status, job.error = "error", f"couldn't start Mega tool: {exc}"
        _save_state(); return
    _track_worker(proc)
    _log(f"mega start {jid} via {kind} (attempt {attempt}/{MEGA_MAX_ATTEMPTS})")
    with _lock:
        if jid in _jobs: job.speed, job.eta, job.resume_at = "Mega…", "", 0.0

    # ── monitor this attempt: quota message, live progress, or a stall ──
    last_prog, last_change, outcome, wait_secs = None, time.time(), None, None
    while True:
        rc = proc.poll()
        if _cancelled():
            _kill_tree(proc.pid); _untrack_worker(proc); logf.close()
            try: log_path.unlink()
            except Exception: pass
            _log(f"mega cancelled {jid}"); return
        txt = _tail_file(log_path, 4000)
        if _mega_is_quota(txt):
            wait_secs = _mega_wait_secs(txt); _kill_tree(proc.pid); outcome = "quota"; break
        prog = _mega_progress(txt)
        if prog and prog[0] != last_prog:
            last_prog = prog[0]; last_change = time.time()
            with _lock:
                if jid in _jobs: job.speed = f"Mega {prog[1]:.0f}%"
        if rc is not None:
            outcome = "done"; break                        # tool exited on its own
        if time.time() - last_change > MEGA_STALL_SEC:      # wedged — usually a silent mid-transfer quota
            _kill_tree(proc.pid); outcome = "stall"; break
        time.sleep(2)

    _untrack_worker(proc); logf.close()
    final = _tail_file(log_path, 4000)
    if _mega_is_quota(final):                               # covers a fast quota-exit race
        outcome = "quota"; wait_secs = wait_secs or _mega_wait_secs(final)

    # ── quota / stall: stamp a resume time and FREE the worker (resolver re-dispatches) ──
    if outcome in ("quota", "stall") and MEGA_AUTO_RESUME:
        secs = wait_secs if wait_secs else MEGA_DEFAULT_WAIT
        with _lock:
            if jid in _jobs:
                job.status   = "downloading"               # stays visible with a countdown
                job.resume_at = time.time() + secs + 90     # +buffer past the stated reset
                job.speed    = "⏳ Mega quota"
                job.eta      = "resume in " + _fmt_dur(secs)
        try: log_path.unlink()
        except Exception: pass
        _save_state()
        _log(f"mega {jid}: quota — resume in {_fmt_dur(secs)} (attempt {attempt}); worker freed")
        return

    if outcome != "done" or proc.returncode != 0:          # genuine failure (or quota w/ resume off)
        with _lock:
            if jid in _jobs:
                job.status, job.resume_at = "error", 0.0
                job.error = (final or "Mega download failed.")[:300]
        _save_state(); return

    # ── success: enforce the size cap on new files, then finish ──
    new_files = sorted((target / r) for r in (_snapshot() - before))
    if MAX_SIZE_BYTES:
        kept = []
        for p in new_files:
            try: sz = p.stat().st_size
            except Exception: sz = 0
            if sz > MAX_SIZE_BYTES:
                try: p.unlink(); _log(f"mega: removed over-limit {p.name} ({sz/(1024**3):.1f} GB)")
                except Exception: pass
            else:
                kept.append(p)
        new_files = kept
    with _lock:
        if jid not in _jobs:
            return
        job.status, job.progress = "finished", 100.0
        job.speed, job.eta, job.resume_at, job.tries = "", "", 0.0, 0
        if new_files:
            job.filename   = new_files[0].name
            job.title      = new_files[0].stem if (not job.title or job.title == job.url) else job.title
            job.items_done = len(new_files)
        else:
            job.filename = "already downloaded — skipped"
    try: log_path.unlink()
    except Exception: pass
    if new_files and TG_ENABLED:                           # note: Mega+TG-delete only safe once fully done
        for p in new_files:
            _tg_enqueue(p, caption=f"Mega · {p.stem}")
    _log(f"mega end {jid} status=finished files={len(new_files)}")
    _save_state()


def _download(jid: str):
    # Atomically CLAIM the job: only a 'queued' job is ours to run, and we flip it to
    # 'downloading' under the lock. If a second (re-staged / priority-bumped) copy of the same
    # jid reaches another worker, it finds the job no longer 'queued' and bails — so re-pushing
    # a URL can never download it twice. (Mega quota-waits set themselves back to 'queued'
    # before re-dispatch, so they still pass this gate.)
    with _lock:
        job = _jobs.get(jid)
        if not job or job.status != "queued":
            return
        job.status  = "downloading"
        job.started = time.time()
    # Final gate: blocked poster, blocked title, or blocked-by-tags album never downloads.
    # (The tag verdict is cached from the sweeper's scan, so usually no extra request here.)
    aid = _album_id_from_url(job.url)
    if (_is_blocked_poster(job.uploader) or _is_blocked_title(job.title)
            or (aid and _album_blocked_by_tags(aid))):
        with _lock:
            _jobs.pop(jid, None)               # blocked content -> never downloads
        _log(f"dl blocked {jid} {job.title[:50]!r}")
        _save_state()
        return
    _save_state()

    if _is_mega(job.url):                    # Mega needs its own tool (yt-dlp can't decrypt it)
        _download_mega(job, jid)
        return

    # ── Metadata pre-pass — ONLY when we don't already know the poster.
    # Profile jobs already have job.uploader (+ title/thumb/count from the probe), so we skip
    # this extra request. Runs in the probe pool with a hard cap so it can't wedge the worker.
    is_live = False
    ext_hint = ""
    meta_uploader = ""
    if not job.uploader:
        def _prepass():
            o = {**_base_opts(archive=False), "extract_flat": True}
            if _is_eb(job.url) and _EB_IMPERSONATE is not None:
                o["impersonate"] = _EB_IMPERSONATE          # pass Cloudflare on the meta pre-pass too
                if _is_eb_video(job.url):
                    o["playlist_items"] = "1"               # main clip only (ignore related previews)
            with yt_dlp.YoutubeDL(o) as ydl:
                return ydl.extract_info(job.url, download=False)
        try:
            info = _probe_pool.submit(_prepass).result(timeout=45)
            if info:
                job.title     = info.get("title") or job.url
                job.platform  = info.get("extractor_key", "") or info.get("extractor", "")
                job.thumb     = info.get("thumbnail", "") or ""
                meta_uploader = info.get("uploader") or info.get("channel") or ""
                is_live       = bool(info.get("is_live"))
                ext_hint      = (info.get("ext") or "").lower()
                entries = info.get("entries")
                if entries is not None:
                    try:    job.items_total = len(list(entries))
                    except: job.items_total = 0
                else:
                    job.items_total = 1
        except Exception:
            pass

    # A single post yt-dlp couldn't name a creator for (erome albums / ebonybaddies videos
    # rarely expose 'uploader') -> resolve the poster so it still files under videos/<creator>/
    # instead of plainly in videos/. Bounded via the probe pool so it can't wedge the worker.
    if not job.uploader and not meta_uploader:
        try:
            meta_uploader = _probe_pool.submit(_post_creator, job.url).result(timeout=30) or ""
        except Exception:
            meta_uploader = ""

    # ── Decide category (drives the icon + the fallback folder) ──
    if job.audio_only or job.fmt == "mp3":
        job.type = "audio"
    elif is_live:
        job.type = "live"
    elif ext_hint in IMAGE_EXTS:
        job.type = "image"
    else:
        job.type = "video"

    # ── Save into  downloads/<type>/<poster>/  (poster folder when we know it) ──
    uploader = _safe_name(job.uploader or meta_uploader)
    if _is_blocked_poster(uploader):               # poster learned in the pre-pass -> re-check
        with _lock:
            _jobs.pop(jid, None)
        _save_state()
        return
    base = DL_ROOT / SUBDIRS[job.type]               # videos / audios / images / lives
    if uploader:
        job.uploader = uploader
        target    = base / uploader                  # e.g. downloads/videos/Junnko/
        name_tmpl = "%(title)s [%(id)s].%(ext)s"
    else:
        target    = base                             # no poster -> straight in the type folder
        # 'Uploader - ' prefix only when the uploader is actually known (no more "NA - ")
        name_tmpl = "%(uploader&{} - |)s%(title)s [%(id)s].%(ext)s"
    target.mkdir(parents=True, exist_ok=True)
    _save_state()

    # ── Format selection ──
    if job.type == "audio":
        fmt  = "bestaudio/best"
        post = [{"key": "FFmpegExtractAudio",
                 "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        q = job.quality
        if q == "best":
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            fmt = (f"bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]"
                   f"/best[height<={q}][ext=mp4]/best[height<={q}]")
        post = []

    # ── Spawn the worker CHILD PROCESS and monitor its progress file ──
    spec = {
        # Dropbox: force the direct-download form so ANY file type downloads, not just
        # the videos yt-dlp's Dropbox extractor handles.
        "url": (_dropbox_direct(job.url) if _is_dropbox(job.url) else job.url),
        "format": fmt, "outtmpl": str(target / name_tmpl), "post": post,
        "archive": str(ARCHIVE_FILE),
        "ffmpeg":  str(FFMPEG_EXE)  if FFMPEG_EXE.exists()  else None,
        "cookies": str(COOKIES_FILE) if COOKIES_FILE.exists() else None,
        "cookies_browser": COOKIES_BROWSER or None,
        "frag_concurrency": FRAG_CONC,
        # aria2 (multi-connection) only when present AND not erome — its CDN returns exit-code 3
        # under aria2's parallel range requests. Safe to enable for direct-file hosts like ebonybaddies.
        "aria2":   str(ARIA2_EXE)   if (USE_ARIA2 and ARIA2_EXE.exists()
                                        and "erome.com" not in job.url.lower()) else None,
        "socket_timeout": SOCK_TIMEOUT, "spacing": REQ_SPACING,
        "block_keywords": BLOCK_KEYWORDS, "block_words": BLOCK_WORDS,
        "progress": str(WORK_DIR / f"prog_{jid}.jsonl"),
        "parent_pid": os.getpid(),        # child self-terminates if this app dies (orphan guard)
        "max_filesize": MAX_SIZE_BYTES or None,   # skip/abort videos over the size cap
        "max_size_gb": (MAX_SIZE_BYTES / (1024**3)) if MAX_SIZE_BYTES else 0,
        # ebonybaddies: browser-TLS impersonation clears Cloudflare; item 1 = the main clip.
        "impersonate":    "chrome" if _is_eb(job.url) else None,
        "playlist_items": "1"      if _is_eb_video(job.url) else None,
        # A SINGLE post = just that post. noplaylist stops a lone video from dragging in the
        # whole playlist/channel it belongs to. Kept OFF for erome albums (the album's own
        # media IS the post → grab all of it) and ebonybaddies (handled by playlist_items).
        # Pure playlist/channel URLs are unaffected by noplaylist, so bulk grabs still work.
        "noplaylist":     not (bool(_album_id_from_url(job.url)) or _is_eb(job.url)),
    }
    spec_path = WORK_DIR / f"spec_{jid}.json"
    prog_path = Path(spec["progress"])
    err_path  = WORK_DIR / f"err_{jid}.log"
    try: prog_path.unlink()
    except Exception: pass
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    def _tidy(keep_err=False):
        for p in (spec_path, prog_path) + (() if keep_err else (err_path,)):
            try: p.unlink()
            except Exception: pass

    _log(f"dl start {jid} {(job.title or job.url)[:60]!r}")
    errf = open(err_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(_worker_cmd(spec_path),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=errf, creationflags=_NO_WINDOW, cwd=str(BASE))
    except Exception as exc:
        errf.close(); _tidy()
        job.status, job.error = "error", f"couldn't start download worker: {exc}"
        _save_state(); return
    _track_worker(proc)                    # registry -> killed on app shutdown (no orphans)

    last_evt, offset, end_ev = time.time(), 0, None
    dl_files = []                                   # basenames of every file this job completed (for Telegram)
    while True:
        rc = proc.poll()
        try:                                       # drain any new progress lines
            with open(prog_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset); chunk = f.read(); offset = f.tell()
        except FileNotFoundError:
            chunk = ""
        if chunk:
            for line in chunk.splitlines():
                try:    ev = json.loads(line)
                except Exception: continue
                last_evt = time.time()
                if ev.get("fn") and ev.get("s") in ("finished", "pp") and ev["fn"] not in dl_files:
                    dl_files.append(ev["fn"])       # a file finished downloading / post-processing
                end_ev = _apply_progress(job, ev) or end_ev
            _save_state()
        with _lock:
            gone = jid not in _jobs
        if gone or jid in _cancel:                 # user cancelled / job purged -> kill the tree
            _kill_tree(proc.pid); _untrack_worker(proc); errf.close(); _tidy()
            _log(f"dl cancelled {jid}")
            return
        if rc is not None:                         # exited; we already drained after the poll
            break
        if time.time() - last_evt > STALL_TIMEOUT:  # zero progress for too long -> kill + retry
            _kill_tree(proc.pid); _untrack_worker(proc); errf.close(); _tidy(keep_err=True)
            job.status, job.error = "error", "stalled — retrying automatically"
            _log(f"dl STALL-KILLED {jid} after {STALL_TIMEOUT}s of no progress")
            _save_state()
            return                                  # resolver re-queues it with backoff
        time.sleep(0.7)
    errf.close()
    _untrack_worker(proc)              # worker exited on its own -> drop from the registry

    _dedup_new_files(target)           # drop byte-identical copies just produced (this folder)

    end_ev = end_ev or {}
    failed = (proc.returncode != 0 and job.items_done == 0)
    err_msg = ((end_ev.get("err") or _tail_file(err_path)
                or "Download failed — link forbidden, unsupported, private, or no media found.")
               if failed else "")
    # Guard against resurrecting a job that was purged/cancelled while it was downloading
    # (resolver purge, tag-sweeper, DELETE, block-poster). Without this, the unconditional
    # terminal write below could flip a just-removed job back to 'finished' and re-persist it.
    if jid in _cancel:
        _tidy(keep_err=failed); _log(f"dl end {jid} (cancelled — not persisted)"); return
    with _lock:
        if jid not in _jobs:
            _tidy(keep_err=failed); _log(f"dl end {jid} (job gone — not persisted)"); return
        if failed:
            job.status, job.error = "error", err_msg
        else:
            job.status, job.progress = "finished", 100.0
            if job.items_done == 0 and not job.filename:
                # 'finished' (not 'error') so the resolver won't retry-loop an over-size skip;
                # the message tells the user WHY nothing was saved.
                job.filename = end_ev.get("skipped") or "already downloaded — skipped"
    _tidy(keep_err=failed)
    # Telegram push: hand each real file that just landed to the uploader (it deletes on success).
    if TG_ENABLED and not failed:
        names = dl_files or ([job.filename] if job.filename else [])
        for fn in names:
            fp = target / fn
            if fp.exists() and fp.is_file():
                _tg_enqueue(fp, caption=f"{job.uploader + ' · ' if job.uploader else ''}{Path(fn).stem}")
    _log(f"dl end {jid} status={job.status} items={job.items_done}/{job.items_total}")
    _save_state()


# ── Startup: load saved state FIRST, then start the background threads ─────────
# Called once by the launcher / __main__ — and never by --dlworker children, so a
# worker process can't clobber saved state or double-run the scans.
_bg_started = False

# Threads here are timed loops that beat() every cycle (<=60s) and are safe to restart
# (idempotent). The watchdog respawns any whose heartbeat goes stale or whose thread
# died on an unexpected error — so a single crashed loop can't silently degrade the app.
_thread_registry: Dict[str, object] = {}

def _spawn(name, fn, register=True):
    if register:
        _thread_registry[name] = fn
    threading.Thread(target=fn, daemon=True, name=name).start()

def _watchdog():
    """Respawn a registered background thread if its heartbeat is stale or it has died.
       Excludes event/queue-blocking threads (saver, prioritizer, workers) which look
       'idle' by design — only the timed loops (cycle <=60s) are watched."""
    THRESH = 300                                   # 5 min with no beat => genuinely dead/wedged
    while True:
        time.sleep(60)
        _beat("watchdog")
        now = time.time()
        live = {t.name for t in threading.enumerate() if t.is_alive()}
        for name, fn in list(_thread_registry.items()):
            age = now - _beats.get(name, now)
            if name not in live or age > THRESH:
                _log(f"watchdog: restarting '{name}' (alive={name in live}, beat {age:.0f}s ago)")
                try:    _spawn(name, fn)
                except Exception as e: _log(f"watchdog respawn {name}: {e}")

def start_background():
    global _bg_started, _persist_enabled
    if _bg_started:
        return
    _bg_started = True
    _prune_workdir()
    _load_state()
    _persist_enabled = True            # only NOW may anything write server_state.json
    # event/queue-blocking threads: not watchdog-registered (idle == blocked by design)
    _spawn("saver", _state_saver, register=False)
    _spawn("prioritizer", _prioritizer, register=False)            # one prober — low request rate
    for i in range(MAX_WORKERS):
        _spawn(f"worker-{i}", _worker, register=False)
    # timed-loop threads: watchdog-registered (respawned if they die)
    _spawn("resolver", _resolver)              # the conflict resolver
    _spawn("totals", _totals_filler)           # learns profile totals
    _spawn("integrity", _integrity_watch)      # corrupt-file scans when idle
    _spawn("tag_sweeper", _tag_sweeper)        # purges TS/Trans by #tags
    _spawn("watchdog", _watchdog, register=False)   # respawns any dead registered thread
    if TG_ENABLED:
        _spawn("telegram", _telegram_worker, register=False)   # pushes finished files to Telegram
    _log(f"started v{VERSION} — restored {len(_jobs)} jobs")


# ── API routes ────────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return send_from_directory(str(STATIC_DIR), "index.html")

@app.route("/userscript.user.js")
def userscript():
    """Serve the Tampermonkey userscript so it can auto-update itself: the script's
       @updateURL/@downloadURL point here, so bumping its @version (whenever it changes)
       makes Tampermonkey pull the new copy on its next update check. Install once by
       visiting this URL; every future edit propagates automatically."""
    for d in (BASE, BASE.parent, RES):              # frozen: next to exe; source: repo root (parent)
        f = d / "userscript.user.js"
        if f.exists():
            resp = send_from_directory(str(d), "userscript.user.js")
            resp.headers["Content-Type"] = "text/javascript; charset=utf-8"
            return resp
    return ("userscript.user.js not found — keep it next to the app.", 404)

@app.route("/api/download", methods=["POST"])
def api_download():
    d   = request.get_json(silent=True) or {}
    url = (d.get("url") or "").strip()
    if not url:
        return jsonify(error="No URL provided"), 400
    fmt     = d.get("format", "mp4")
    quality = d.get("quality", "best")
    audio   = bool(d.get("audio_only", False))
    _unmark_removed(url)                          # a deliberate re-submit un-does an earlier delete
    # A user/profile page -> scrape it into album jobs in the background.
    if _is_profile(url):
        threading.Thread(target=_expand_and_queue,
                         args=(url, fmt, quality, audio), daemon=True).start()
        return jsonify(expanding=True)
    # An ebonybaddies model/listing page -> scrape it into per-video jobs.
    if _is_eb_list(url):
        threading.Thread(target=_eb_expand_and_queue,
                         args=(url, fmt, quality, audio), daemon=True).start()
        return jsonify(expanding=True)
    jid = _queue_job(url, fmt, quality, audio, tier=0)   # standalone post -> jumps ahead of scans
    if jid:
        _save_state()
        return jsonify(id=jid)
    # Not queued as new. If it's already here (pushed twice), FOCUS on it instead of duplicating.
    existing = _bump_priority(url)
    _save_state()
    if existing:
        return jsonify(bumped=True, id=existing)
    return jsonify(duplicate=True)

def _disk_report(need):
    """Free space on the download drive vs. `need` (estimated bytes still to download),
       so the UI can warn before the queue runs the drive out of room."""
    try:
        target = DL_ROOT if DL_ROOT.exists() else BASE
        du = shutil.disk_usage(str(target))
    except Exception:
        return None
    drive = (os.path.splitdrive(str(target))[0] or "").upper()  # e.g. "D:"
    return dict(free=du.free, total=du.total, used=du.used,
                needed=need, fits=(need <= du.free), drive=drive)

@app.route("/api/jobs")
def api_jobs():
    with _lock:
        allj = list(_jobs.values())
    # ONE pass over all jobs: status counts, the capped display slices, and the disk-need
    # estimate. Separate passes here got expensive with very large queues (10k+ jobs / poll).
    counts = {"downloading": 0, "queued": 0, "finished": 0, "error": 0, "total": len(allj)}
    dl, qd, rest, need = [], [], [], 0
    for j in allj:
        counts[j.status] = counts.get(j.status, 0) + 1
        if j.status == "downloading":
            dl.append(j)
            est = j.size_est or DEFAULT_EST_BYTES
            need += int(est * max(0.0, 1.0 - (j.progress or 0) / 100.0))   # part not yet on disk
        elif j.status == "queued":
            if len(qd) < 200: qd.append(j)                 # display cap (count stays exact above)
            need += j.size_est or DEFAULT_EST_BYTES
        else:                                              # finished / error (capped by _trim_jobs)
            rest.append(j)
    shown = dl + qd + rest[-MAX_FIN_SHOWN:][::-1]           # most-recent finished/error first
    return jsonify(jobs=[j.to_dict() for j in shown], counts=counts,
                   space=_disk_report(need), expanding=_expanding_snapshot())

@app.route("/api/jobs/<jid>", methods=["DELETE"])
def api_del(jid):
    with _lock:
        j = _jobs.get(jid)
        url = j.url if j else None
    with _cancel_lock:
        _cancel.add(jid)                 # if it's mid-download, abort it
    with _lock:
        _jobs.pop(jid, None)
    if url:
        _mark_removed(url)               # remember it so the self-healing scans can't re-queue it
    _save_state()
    return jsonify(ok=True)

@app.route("/api/pause", methods=["POST"])
def api_pause():
    if _run.is_set(): _run.clear()       # pause: workers stop pulling new jobs
    else:             _run.set()         # resume
    return jsonify(paused=not _run.is_set())

@app.route("/api/jobs/clear", methods=["POST"])
def api_clear():
    with _lock:
        gone = [k for k, v in _jobs.items() if v.status in ("finished", "error")]
        for k in gone:
            del _jobs[k]
    _save_state()
    return jsonify(cleared=len(gone))

@app.route("/api/jobs/cancel_all", methods=["POST"])
def api_cancel_all():
    """Stop everything in flight and drop everything still queued. Finished and
       failed jobs are left alone (use Clear Done / Retry Failed for those)."""
    with _lock:
        targets = [jid for jid, j in _jobs.items()
                   if j.status in ("queued", "downloading")]
    with _cancel_lock:
        _cancel.update(targets)          # kill any in-flight child; skip queued pickups
    with _lock:
        for jid in targets:
            _jobs.pop(jid, None)
    # NOTE: Cancel All just CLEARS the queue (re-runnable) — it does NOT mark items removed.
    # Only the per-card ✕ Delete marks a URL removed (permanent). This lets you cancel-all,
    # clear, and re-run to pull only new content without cancelled items being blocked.
    _save_state()
    return jsonify(cancelled=len(targets))

@app.route("/api/jobs/retry_failed", methods=["POST"])
def api_retry_failed():
    """Requeue every failed job for a fresh attempt (resets its retry counter)."""
    requeued = []
    with _lock:
        for jid, j in _jobs.items():
            if j.status == "error":
                j.status, j.error, j.tries = "queued", "", 0
                j.progress, j.started = 0.0, 0.0
                requeued.append(jid)
    for jid in requeued:
        _stage_put(jid)
    if requeued:
        _note_staged()
    _save_state()
    return jsonify(requeued=len(requeued))

@app.route("/api/jobs/<jid>/retry", methods=["POST"])
def api_retry_one(jid):
    """Requeue a single failed job."""
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return jsonify(error="not found"), 404
        j.status, j.error, j.tries = "queued", "", 0
        j.progress, j.started = 0.0, 0.0
    _stage_put(jid); _note_staged()
    _save_state()
    return jsonify(ok=True)

@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Inspect a URL WITHOUT downloading: list a profile/channel/playlist's
       uploads and report how many items there are, so the user can verify
       before grabbing everything."""
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url:
        return jsonify(error="No URL provided"), 400
    # Profile page -> list the user's albums (yt-dlp can't enumerate these).
    if _is_profile(url):
        albums = _profile_albums(url)
        user = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        items = [dict(title=f"Album {u.rsplit('/',1)[-1]}", url=u, thumb="", duration=None)
                 for u in albums]
        return jsonify(kind="list", count=len(items), uploader=user,
                       title=f"{user} — albums", platform="Erome", items=items[:2000])
    # ebonybaddies model/listing page -> list its videos.
    if _is_eb_list(url):
        pairs = _eb_list_videos(url)                  # [(video_url, creator), ...]
        name = _eb_list_name(url)
        items = [dict(title=f"{creator} — " + u.rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
                      url=u, thumb="", duration=None) for (u, creator) in pairs]
        return jsonify(kind="list", count=len(items), uploader=name,
                       title=f"{name} — videos", platform="EbonyBaddies", items=items[:2000])
    try:
        with yt_dlp.YoutubeDL({**_base_opts(archive=False),
                               "extract_flat": "in_playlist",
                               "skip_download": True}) as ydl:   # scan everything
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify(error=str(e)[:200])
    if not info:
        return jsonify(error="Could not read that URL.")

    def thumb_of(e):
        if e.get("thumbnail"): return e["thumbnail"]
        th = e.get("thumbnails") or []
        return th[-1]["url"] if th and th[-1].get("url") else ""

    def url_of(e):
        u = e.get("webpage_url") or e.get("url") or ""
        if u.startswith("http"): return u
        vid = e.get("id", "")
        ie  = (e.get("ie_key") or info.get("extractor_key") or "").lower()
        if vid and "youtube" in ie: return f"https://www.youtube.com/watch?v={vid}"
        return u or vid

    entries = info.get("entries")
    items = []
    if entries is not None:
        for e in entries:
            if not e: continue
            items.append(dict(title=e.get("title") or e.get("id") or "untitled",
                              url=url_of(e), thumb=thumb_of(e),
                              duration=e.get("duration")))
        kind = "list"
    else:
        items.append(dict(title=info.get("title") or url,
                          url=info.get("webpage_url") or url,
                          thumb=thumb_of(info), duration=info.get("duration")))
        kind = "single"

    return jsonify(kind=kind, count=len(items),
                   uploader=(info.get("uploader") or info.get("channel")
                             or info.get("title") or ""),
                   title=info.get("title") or "",
                   platform=info.get("extractor_key", "") or info.get("extractor", ""),
                   items=items[:2000])    # display cap only; count is the true total

@app.route("/api/library")
def api_library():
    """Per-profile counts so you can track how many videos each profile has."""
    items = []
    for typ, sub in SUBDIRS.items():                # video / audio / image / live
        base = DL_ROOT / sub
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir():
                cnt = sum(1 for f in d.iterdir() if f.is_file() and f.suffix != ".part")
                items.append({"type": typ, "name": d.name, "count": cnt,
                              "albums_done": len(_disk_album_ids(d)),
                              "albums_total": _totals.get(d.name)})
    items.sort(key=lambda x: -x["count"])
    return jsonify(items)

@app.route("/api/folder", methods=["POST"])
def api_folder():
    d   = request.get_json(silent=True) or {}
    sub = d.get("type", ""); prof = d.get("profile", "")
    if sub in SUBDIRS:
        target = DL_ROOT / SUBDIRS[sub]
        if prof and (target / prof).is_dir():       # open a specific profile folder
            target = target / prof
    else:
        target = DL_ROOT
    p = str(target.resolve())
    try:
        if sys.platform == "win32":      os.startfile(p)            # noqa
        elif sys.platform == "darwin":   subprocess.Popen(["open", p])
        else:                            subprocess.Popen(["xdg-open", p])
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)

@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    """Manually scan finished files for corruption (and re-download any that are broken)."""
    with _verify_lock:
        if _verify_state["running"]:
            return jsonify(already_running=True)
    d    = request.get_json(silent=True) or {}
    deep = bool(d.get("deep", VERIFY_DEEP))     # optional thorough full-decode check
    threading.Thread(target=_integrity_scan, kwargs={"deep": deep}, daemon=True).start()
    return jsonify(started=True, deep=deep)

@app.route("/api/recheck", methods=["POST"])
def api_recheck():
    """Manually re-scrape every profile and re-download any posts that are missing."""
    with _complete_lock:
        if _complete_state["running"]:
            return jsonify(already_running=True)
    # manual run forces a fresh scrape (ignores the per-profile throttle)
    threading.Thread(target=_completeness_scan, kwargs={"force": True}, daemon=True).start()
    return jsonify(started=True)

@app.route("/api/block_poster", methods=["POST"])
def api_block_poster():
    """Add a poster to the blocklist; purge their queued/active jobs immediately."""
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify(error="No poster name"), 400
    BLOCK_POSTERS.add(name.lower())
    _cfg["block_posters"] = sorted(BLOCK_POSTERS)
    try:    _atomic_json(CONFIG_FILE, _cfg)       # persists across restarts/rebuilds
    except Exception: pass
    purged = []
    with _lock:
        for jid, j in list(_jobs.items()):
            if j.status in ("queued", "downloading") and _is_blocked_poster(j.uploader):
                purged.append(jid)
                del _jobs[jid]
    for jid in purged:
        with _cancel_lock:
            _cancel.add(jid)                      # kills any in-flight download of theirs
    _save_state()
    _log(f"blocked poster {name!r} — purged {len(purged)} job(s)")
    return jsonify(ok=True, purged=len(purged), posters=sorted(BLOCK_POSTERS))

def _blocklist_snapshot():
    return dict(keywords=sorted(BLOCK_KEYWORDS), words=sorted(BLOCK_WORDS),
                posters=sorted(BLOCK_POSTERS))

@app.route("/api/blocklist", methods=["GET", "POST"])
def api_blocklist():
    """View and edit the title/poster blocklist live (no restart). GET returns all three
       lists; POST {action:add|remove, kind:keyword|word|poster, value} edits one entry,
       persists to config.json, and (on add) purges anything queued that now matches."""
    if request.method == "GET":
        return jsonify(**_blocklist_snapshot())
    d      = request.get_json(silent=True) or {}
    action = (d.get("action") or "").strip().lower()
    kind   = (d.get("kind")   or "").strip().lower()
    value  = (d.get("value")  or "").strip().lower()
    if action not in ("add", "remove") or kind not in ("keyword", "word", "poster") or not value:
        return jsonify(error="bad request"), 400
    if kind == "keyword":
        if action == "add" and value not in BLOCK_KEYWORDS: BLOCK_KEYWORDS.append(value)
        elif action == "remove" and value in BLOCK_KEYWORDS: BLOCK_KEYWORDS.remove(value)
        _cfg["block_keywords"] = sorted(BLOCK_KEYWORDS)
    elif kind == "word":
        if action == "add" and value not in BLOCK_WORDS: BLOCK_WORDS.append(value)
        elif action == "remove" and value in BLOCK_WORDS: BLOCK_WORDS.remove(value)
        _cfg["block_words"] = sorted(BLOCK_WORDS)
        _rebuild_block_word_re()                       # whole-word matcher must be rebuilt
    else:  # poster
        if action == "add": BLOCK_POSTERS.add(value)
        else:               BLOCK_POSTERS.discard(value)
        _cfg["block_posters"] = sorted(BLOCK_POSTERS)
    try:    _atomic_json(CONFIG_FILE, _cfg)             # persists across restarts
    except Exception: pass
    purged = 0
    if action == "add":                                # drop anything already queued that now matches
        victims = []
        with _lock:
            for jid, j in list(_jobs.items()):
                if j.status in ("queued", "downloading") and (
                        _is_blocked_title(j.title) or _is_blocked_poster(j.uploader)):
                    victims.append(jid)
        for jid in victims:
            with _cancel_lock: _cancel.add(jid)
            with _lock:        _jobs.pop(jid, None)
        purged = len(victims)
        if purged: _save_state()
    _log(f"blocklist {action} {kind} {value!r} (purged {purged})")
    return jsonify(ok=True, purged=purged, **_blocklist_snapshot())

def _ytdlp_age():
    """(version string, days old). A bundled PyInstaller yt-dlp is pinned at build time and
       goes stale — extractors break within weeks — so the UI surfaces its age as a nudge."""
    ver = getattr(getattr(yt_dlp, "version", None), "__version__", "") or ""
    try:
        y, m, d = (int(x) for x in ver.split(".")[:3])
        return ver, (datetime.date.today() - datetime.date(y, m, d)).days
    except Exception:
        return ver, None

@app.route("/api/info")
def api_info():
    with _verify_lock:
        verify = dict(_verify_state)
    with _complete_lock:
        complete = dict(_complete_state)
    now = time.time()
    yv, yage = _ytdlp_age()
    return jsonify(
        version  = VERSION,
        ytdlp_version = yv,
        ytdlp_age_days = yage,
        cookies_browser = COOKIES_BROWSER or "",
        ffmpeg   = FFMPEG_EXE.exists(),
        cookies  = COOKIES_FILE.exists(),
        base     = str(BASE),
        paused   = not _run.is_set(),
        aria2    = bool(USE_ARIA2 and ARIA2_EXE.exists()),
        verify   = verify,                    # {running, checked, corrupt, requeued, summary, ...}
        complete = complete,                  # {running, profiles, missing, requeued, summary, detail}
        tag_blocked = sum(1 for v in _album_block.values() if v),  # TS/Trans albums caught by #tags
        posters_blocked = sorted(BLOCK_POSTERS),
        threads  = {k: round(now - v) for k, v in _beats_snapshot().items()},  # heartbeat ages (s)
        telegram = dict(_tg_state, enabled=TG_ENABLED, delete_after=TG_DELETE_AFTER),  # push status
        app      = "universal_downloader",    # signature: launcher only hands off to our own app
    )

def _find_python():
    """A real Python interpreter to run pip with. From source, this process IS one;
       from the frozen exe, sys.executable is the exe, so look one up on PATH."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    for c in ("python", "python3", "py"):
        p = shutil.which(c)
        if p:
            return p
    return None

@app.route("/api/update_engine", methods=["POST"])
def api_update_engine():
    """One-click yt-dlp refresh: pip-install the latest yt_dlp into the external `ytdlp/`
       folder (which shadows the bundled copy on next launch). --no-deps so it reuses the
       app's bundled deps (curl_cffi etc.) and can't drag in conflicting versions."""
    py = _find_python()
    if not py:
        return jsonify(ok=False, error="No system Python found on PATH. Install Python (or run "
                       "the app from source) to enable one-click engine updates."), 200
    try:
        YTDLP_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [py, "-m", "pip", "install", "--target", str(YTDLP_DIR),
               "--upgrade", "--no-deps", "yt-dlp"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="Update timed out (network?). Try again."), 200
    except Exception as e:
        return jsonify(ok=False, error=f"Update failed to start: {e}"), 200
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    if r.returncode == 0:
        m = re.search(r"Successfully installed yt[-_]dlp-([0-9.]+)", out)
        if m:
            _log(f"engine updated -> yt-dlp {m.group(1)}")
            return jsonify(ok=True, version=m.group(1),
                           message=f"Updated to yt-dlp {m.group(1)} — restart the app to load it.")
        return jsonify(ok=True, message="yt-dlp is already up to date.")
    return jsonify(ok=False, error=(out.strip()[-280:] or "pip failed")), 200

@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    # lets a fresh launch cleanly take over; block cross-site CSRF (allow our own page + the launcher)
    origin = request.headers.get("Origin", "")
    allowed = (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")
    if origin and origin not in allowed:
        return jsonify(error="forbidden"), 403
    _write_state()                  # flush now (the debounced saver may be mid-wait)
    _kill_all_workers()             # kill any in-flight downloaders so none orphan past os._exit
    _cleanup_cookie_files()         # os._exit skips atexit, so clean up here
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify(ok=True)


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--dlworker" in sys.argv:        # child process mode: run ONE download, then exit
        sys.exit(dl_worker_main(sys.argv[sys.argv.index("--dlworker") + 1]))
    import webbrowser
    start_background()
    url = f"http://{HOST}:{PORT}"
    print(f"\n  Universal Downloader v{VERSION}  --  {url}")
    print(f"  folder : {BASE}")
    print(f"  ffmpeg : {'bundled OK' if FFMPEG_EXE.exists() else 'NOT FOUND (PATH only)'}")
    print(f"  cookies: {'loaded' if COOKIES_FILE.exists() else 'none (anonymous)'}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
