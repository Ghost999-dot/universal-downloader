"""Regression tests for the pure logic in server.py — the filtering, parsing, and
job-trimming that reliability depends on.

Run:
    cd "D:\\Coding thingy2\\UNI\\source"
    python -m pytest test_server.py -v

Import strategy: server.py runs setup at import time (creates downloads/ + _work/ next
to itself, loads JSON caches). To avoid polluting source/, we copy server.py into a
temp dir and import the COPY there, so all side-effect files land in the temp dir.
Tests are deterministic — no network, no real downloads, no time/random dependence.
"""
import importlib.util, os, shutil, sys, tempfile
import pytest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")


@pytest.fixture(scope="module")
def srv():
    tmp = tempfile.mkdtemp(prefix="udtest_")
    dst = os.path.join(tmp, "server.py")
    shutil.copy2(SRC, dst)
    spec = importlib.util.spec_from_file_location("udserver_test", dst)
    m = importlib.util.module_from_spec(spec)
    sys.modules["udserver_test"] = m
    spec.loader.exec_module(m)            # module-level setup runs inside tmp
    m._persist_enabled = False           # never write state during tests
    yield m
    shutil.rmtree(tmp, ignore_errors=True)


# ── content filter: keyword + whole-word (the TS/"tits" false-positive boundary) ──
@pytest.mark.parametrize("title,blocked", [
    ("TS compilation", True),
    ("trans girl", True),
    ("shemale", True),
    ("hot ts4", True),            # whole-word + trailing digit
    ("femboy fun", True),
    ("nice tits", False),         # 'ts' inside 'tits' must NOT match
    ("cute cats", False),
    ("low costs", False),
    ("beach vacation", False),
    ("", False),
])
def test_is_blocked_title(srv, title, blocked):
    assert srv._is_blocked_title(title) is blocked


# ── dead-vs-transient error classification ──
@pytest.mark.parametrize("msg,dead", [
    ("HTTP Error 404: Not Found", True),
    ("This private video is unavailable", True),
    ("account has been suspended", True),
    ("Unsupported URL: https://...", False),   # transient on erome live albums
    ("Connection timed out", False),
    ("", False),
])
def test_is_dead_error(srv, msg, dead):
    assert srv._is_dead_error(msg) is dead


# ── url / name helpers ──
def test_album_id_from_url(srv):
    assert srv._album_id_from_url("https://www.erome.com/a/AbC123") == "AbC123"
    assert srv._album_id_from_url("not-a-url") is None

def test_is_profile(srv):
    assert srv._is_profile("https://www.erome.com/someuser") is True
    assert srv._is_profile("https://www.erome.com/a/AbC123") is False

def test_safe_name(srv):
    assert srv._safe_name('a/b:c*?"<>|d') == "abcd"
    assert srv._safe_name("...name...") == "name"
    assert len(srv._safe_name("x" * 200)) <= 80


# ── deep tag scan scoping: og:title + album-tags are read ──
def test_album_texts_reads_own_title_and_tags(srv):
    html = ('<meta property="og:title" content="Sunny Beach Set">'
            '<a class="album-tag" href="/search?q=bikini">bikini</a>')
    txt = srv._album_texts(html)
    assert "Sunny Beach Set" in txt
    assert "bikini" in txt


# ── job trimming: cap finished/error, never drop active/queued ──
def test_trim_jobs_caps_finished_keeps_queued(srv):
    srv._jobs.clear()
    srv.MAX_JOBS_KEEP = 50
    for i in range(60):
        j = srv.Job(f"f{i:03d}", f"http://x/{i}", "mp4", "best", False)
        j.status = "finished"
        srv._jobs[j.id] = j
    q = srv.Job("Q1", "http://q/1", "mp4", "best", False); q.status = "queued"
    srv._jobs["Q1"] = q
    removed = srv._trim_jobs()
    finished = [j for j in srv._jobs.values() if j.status == "finished"]
    queued   = [j for j in srv._jobs.values() if j.status == "queued"]
    assert removed == 10
    assert len(finished) == 50
    assert len(queued) == 1
    assert "f000" not in srv._jobs        # oldest dropped first
    assert "f059" in srv._jobs            # newest retained
    srv._jobs.clear()


# ── hardening wiring guards (cheap structural checks) ──
def test_http_get_has_retry(srv):
    import inspect
    assert "tries" in inspect.signature(srv._http_get).parameters

def test_csrf_protected_set(srv):
    assert "/api/shutdown" in srv._CSRF_PROTECTED
    assert "/api/pause" in srv._CSRF_PROTECTED
    assert "/api/download" not in srv._CSRF_PROTECTED   # must stay open for the userscript
