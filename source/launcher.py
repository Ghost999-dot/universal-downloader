#!/usr/bin/env python3
"""Universal Downloader — desktop control panel.

A small dark app window: START / STOP the local server, see the status light,
and open the UI in your browser. This is the "own app" entry point and is also
what gets packaged into the standalone .exe.

Run:  python launcher.py      (or double-click  Start Universal Downloader.bat)
"""

import sys, threading, time, webbrowser

import server   # the Flask app + config live here

# Worker mode: the exe re-invokes ITSELF with --dlworker to run one download in a
# kill-able child process. Handle it before any GUI setup and exit when done.
if "--dlworker" in sys.argv:
    sys.exit(server.dl_worker_main(sys.argv[sys.argv.index("--dlworker") + 1]))

import tkinter as tk
from werkzeug.serving import make_server

# ── Palette (matches the web UI) ──────────────────────────────────────────────
BG, CARD, HDR = "#0c0c0c", "#131313", "#161616"
BORDER        = "#1e1e1e"
TEXT, MUTED   = "#e0e0e0", "#777777"
DIM           = "#3a3a3a"
GREEN, GREEND = "#a855f7", "#9333ea"   # purple accent (name kept for brevity)
RED, REDD     = "#ef4444", "#b91c1c"
BTN, BTNHOV   = "#1a1a1a", "#222222"

URL = f"http://{server.HOST}:{server.PORT}"


class ServerThread(threading.Thread):
    """Runs the Flask app and can be shut down cleanly."""
    def __init__(self):
        super().__init__(daemon=True)
        self._srv = make_server(server.HOST, server.PORT, server.app, threaded=True)
    def run(self):     self._srv.serve_forever()
    def shutdown(self): self._srv.shutdown()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Universal Downloader v{server.VERSION} — Local Server")
        self.configure(bg=BG)
        try:                                            # window/taskbar icon
            ico = server.RES / "icon.ico"
            if ico.exists(): self.iconbitmap(str(ico))
        except Exception: pass
        self.geometry("440x320"); self.resizable(False, False)
        self._srv = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        # Auto-start so it's usable immediately; button can stop/restart.
        self.after(300, self._start)

    # ── UI ──
    def _build(self):
        tk.Frame(self, bg="#1a1a1a", height=3).pack(fill=tk.X)

        hdr = tk.Frame(self, bg=BG); hdr.pack(fill=tk.X, padx=22, pady=(20, 6))
        badge = tk.Canvas(hdr, width=34, height=34, bg=BG, highlightthickness=0)
        badge.pack(side=tk.LEFT)
        badge.create_rectangle(0, 0, 34, 34, fill=GREEN, outline="")
        badge.create_text(17, 16, text="⬇", fill="#fff", font=("Segoe UI", 15, "bold"))
        tk.Label(hdr, text="  Universal Downloader", bg=BG, fg="#fff",
                 font=("Segoe UI Semibold", 15)).pack(side=tk.LEFT)

        tk.Label(self, text="Local media downloader server", bg=BG, fg=DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=24)

        # Status card
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill=tk.X, padx=22, pady=16)
        row = tk.Frame(card, bg=CARD); row.pack(fill=tk.X, padx=16, pady=14)
        self._dot = tk.Canvas(row, width=12, height=12, bg=CARD, highlightthickness=0)
        self._dot.pack(side=tk.LEFT)
        self._dot_id = self._dot.create_oval(1, 1, 11, 11, fill=DIM, outline="")
        self._status = tk.Label(row, text="  Stopped", bg=CARD, fg=MUTED,
                                font=("Segoe UI", 11)); self._status.pack(side=tk.LEFT)
        self._urllbl = tk.Label(card, text=URL, bg=CARD, fg=DIM,
                                font=("Consolas", 10)); self._urllbl.pack(anchor="w", padx=18, pady=(0, 12))

        # Big toggle
        self._toggle = tk.Button(self, text="■  STOP SERVER", command=self._toggle_srv,
                                 bg=RED, fg="#fff", activebackground=REDD, activeforeground="#fff",
                                 relief=tk.FLAT, bd=0, cursor="hand2",
                                 font=("Segoe UI Semibold", 12), pady=11)
        self._toggle.pack(fill=tk.X, padx=22)

        # Secondary buttons
        sec = tk.Frame(self, bg=BG); sec.pack(fill=tk.X, padx=22, pady=12)
        self._open = self._mkbtn(sec, "🌐  Open in Browser", lambda: webbrowser.open(URL))
        self._open.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        self._mkbtn(sec, "📁  Downloads", self._open_folder).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        # Footer env line
        ff = "ffmpeg ✓" if server.FFMPEG_EXE.exists() else "ffmpeg ✕ (HD/MP3 limited)"
        ck = "cookies ✓" if server.COOKIES_FILE.exists() else "cookies — (anonymous)"
        tk.Label(self, text=f"{ff}      {ck}", bg=BG, fg=DIM,
                 font=("Segoe UI", 9)).pack(side=tk.BOTTOM, pady=(0, 10))

    def _mkbtn(self, p, text, cmd):
        b = tk.Button(p, text=text, command=cmd, bg=BTN, fg=TEXT,
                      activebackground=BTNHOV, activeforeground="#fff",
                      relief=tk.FLAT, bd=0, cursor="hand2", font=("Segoe UI", 10), pady=8)
        b.bind("<Enter>", lambda _: b.configure(bg=BTNHOV))
        b.bind("<Leave>", lambda _: b.configure(bg=BTN))
        return b

    # ── Server control ──
    def _toggle_srv(self):
        self._stop() if self._srv else self._start()

    def _start(self):
        if self._srv: return
        try:
            srv = ServerThread()                 # binds the port
        except OSError:
            # the port is busy — only take it over if it's OUR app (not a stranger on 9898)
            import urllib.request, json as _json
            try:
                info = _json.loads(urllib.request.urlopen(URL + "/api/info", timeout=3).read())
            except Exception:
                info = {}
            if info.get("app") != "universal_downloader":
                webbrowser.open(URL); return     # someone else owns the port -> leave it alone
            try:
                urllib.request.urlopen(
                    urllib.request.Request(URL + "/api/shutdown", method="POST"), timeout=3)
            except Exception:
                pass
            time.sleep(1.5)
            try:
                srv = ServerThread()             # retry now that it's freed
            except OSError:
                webbrowser.open(URL); return     # couldn't take over -> just open the existing UI
        server.start_background()                # load saved state FIRST, then start the threads
        self._srv = srv; self._srv.start()
        self._dot.itemconfigure(self._dot_id, fill=GREEN)
        self._status.configure(text="  Running", fg=GREEN)
        self._urllbl.configure(fg=MUTED)
        self._toggle.configure(text="■  STOP SERVER", bg=RED, activebackground=REDD)
        self._open.configure(state="normal")
        webbrowser.open(URL)

    def _stop(self):
        if not self._srv: return
        self._srv.shutdown(); self._srv = None
        self._dot.itemconfigure(self._dot_id, fill=DIM)
        self._status.configure(text="  Stopped", fg=MUTED)
        self._urllbl.configure(fg=DIM)
        self._toggle.configure(text="▶  START SERVER", bg=GREEN, activebackground=GREEND)

    def _open_folder(self):
        try:    import os; os.startfile(str(server.DL_ROOT.resolve()))   # noqa
        except Exception: pass

    def _close(self):
        try:
            if self._srv: self._srv.shutdown()
        finally:
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
