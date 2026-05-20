"""
Admin GUI for the US Manufacturing Investment Tracker.

A simple desktop UI to:
  - Run the daily ingest pipeline (in the background)
  - Review pending records (approve / reject / edit)
  - See counts of pending / approved / rejected records
  - Copy the git-push command when ready to publish

Run with:
    python scripts/admin.py
Or double-click admin.bat at the project root.
"""
import json
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PENDING_PATH = DATA_DIR / "pending.json"
APPROVED_PATH = DATA_DIR / "investments.json"
REJECTED_PATH = DATA_DIR / "rejected.json"
RUN_DAILY = ROOT / "scripts" / "run_daily.py"

# Three separate commands (Windows cmd doesn't support &&). Joined with newlines
# so pasting into a terminal runs them in sequence.
GIT_PUSH_CMD = 'git add data/\ngit commit -m "Daily review"\ngit push'


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_amount(record):
    if not record.get("amount_disclosed") or record.get("amount_usd") is None:
        return "Undisclosed"
    usd = record["amount_usd"]
    if usd >= 1_000_000_000:
        return f"${usd/1_000_000_000:.2f}B"
    if usd >= 1_000_000:
        return f"${usd/1_000_000:.0f}M"
    return f"${usd:,}"


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class AdminApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Investment Tracker - Admin")
        self.geometry("1100x720")
        self.minsize(900, 600)

        # apply a slightly nicer theme
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass

        # state
        self.pending = []
        self.approved = []
        self.rejected = []
        self.selected_index = None
        self.log_queue = queue.Queue()
        self.pipeline_running = False

        self._build_ui()
        self._reload_all()

        # poll the log queue
        self.after(120, self._drain_log_queue)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        self._build_header()
        self._build_tabs()
        self._build_footer()

    def _build_header(self):
        bar = ttk.Frame(self, padding=(12, 8))
        bar.pack(fill="x")

        self.count_var = tk.StringVar(value="Pending: 0   Approved: 0   Rejected: 0")
        ttk.Label(bar, textvariable=self.count_var, font=("Segoe UI", 11, "bold")).pack(side="left")

        self.run_btn = ttk.Button(bar, text="Run Pipeline", command=self._run_pipeline)
        self.run_btn.pack(side="right", padx=(4, 0))

        ttk.Button(bar, text="Refresh", command=self._reload_all).pack(side="right", padx=(4, 0))

    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.notebook = nb

        # Review tab
        review = ttk.Frame(nb)
        nb.add(review, text="Review queue")
        self._build_review_tab(review)

        # Log tab
        logf = ttk.Frame(nb)
        nb.add(logf, text="Pipeline log")
        self._build_log_tab(logf)

    def _build_review_tab(self, parent):
        # left: list of pending; right: details + actions
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text="Pending records").pack(anchor="w")

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        self.pending_list = tk.Listbox(list_frame, activestyle="dotbox", exportselection=False)
        self.pending_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=self.pending_list.yview)
        scroll.pack(side="right", fill="y")
        self.pending_list.configure(yscrollcommand=scroll.set)
        self.pending_list.bind("<<ListboxSelect>>", self._on_select)

        right = ttk.Frame(paned, padding=8)
        paned.add(right, weight=3)

        self.detail_text = tk.Text(right, wrap="word", font=("Consolas", 10), height=20)
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.configure(state="disabled")

        btn_row = ttk.Frame(right)
        btn_row.pack(fill="x", pady=(8, 0))
        self.approve_btn = ttk.Button(btn_row, text="Approve", command=self._approve, state="disabled")
        self.approve_btn.pack(side="left", padx=2)
        self.reject_btn = ttk.Button(btn_row, text="Reject", command=self._reject, state="disabled")
        self.reject_btn.pack(side="left", padx=2)
        self.edit_btn = ttk.Button(btn_row, text="Edit JSON", command=self._edit, state="disabled")
        self.edit_btn.pack(side="left", padx=2)
        self.open_btn = ttk.Button(btn_row, text="Open source", command=self._open_source, state="disabled")
        self.open_btn.pack(side="left", padx=2)

    def _build_log_tab(self, parent):
        self.log_text = tk.Text(parent, wrap="word", font=("Consolas", 9), background="#0d1117", foreground="#e6edf3", insertbackground="#e6edf3")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(12, 4, 12, 10))
        bar.pack(fill="x", side="bottom")

        ttk.Label(bar, text="When done reviewing, publish with:").pack(anchor="w")
        row = ttk.Frame(bar)
        row.pack(fill="x", pady=(2, 0))

        self.git_text = tk.Text(row, font=("Consolas", 10), height=3, wrap="none",
                                background="#f5f5f5")
        self.git_text.insert("1.0", GIT_PUSH_CMD)
        self.git_text.configure(state="disabled")
        self.git_text.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Copy", command=self._copy_git_cmd).pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _reload_all(self):
        self.pending = load_json(PENDING_PATH, [])
        self.approved = load_json(APPROVED_PATH, [])
        self.rejected = load_json(REJECTED_PATH, [])
        self._refresh_counts()
        self._refresh_pending_list()

    def _refresh_counts(self):
        self.count_var.set(
            f"Pending: {len(self.pending)}   "
            f"Approved: {len(self.approved)}   "
            f"Rejected: {len(self.rejected)}"
        )

    def _refresh_pending_list(self):
        self.pending_list.delete(0, "end")
        for r in self.pending:
            label = f"{r['company']['name']}  -  {r.get('industry', '?')}  -  {r['location'].get('city') or r['location'].get('state')}"
            self.pending_list.insert("end", label)
        self.selected_index = None
        self._render_detail(None)
        self._set_action_buttons_state(False)

    # ------------------------------------------------------------------
    # Selection / detail rendering
    # ------------------------------------------------------------------
    def _on_select(self, _evt):
        sel = self.pending_list.curselection()
        if not sel:
            return
        i = sel[0]
        self.selected_index = i
        self._render_detail(self.pending[i])
        self._set_action_buttons_state(True)

    def _render_detail(self, r):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        if r is None:
            self.detail_text.configure(state="disabled")
            return
        lines = []
        lines.append(f"Company:    {r['company']['name']}  ({r['company'].get('hq_country') or '?'})")
        lines.append(f"Industry:   {r.get('industry', '?')}")
        lines.append(f"Type:       {r.get('investment_type', '?')}")
        lines.append(f"Amount:     {format_amount(r)}")
        loc = r["location"]
        lines.append(f"Location:   {loc.get('city') or '(no city)'}, {loc.get('state')}  ({loc.get('precision')})")
        lat, lon = loc.get('lat'), loc.get('lon')
        if lat is not None and lon is not None:
            lines.append(f"            lat/lon: {lat:.4f}, {lon:.4f}")
        lines.append(f"Announced:  {r['dates']['announced']}")
        exp = r["dates"]
        if exp.get("expected_start") or exp.get("expected_completion"):
            lines.append(f"Expected:   start={exp.get('expected_start')}   complete={exp.get('expected_completion')}")
        lines.append("")
        lines.append("Description:")
        lines.append(f"  {r.get('description', '')}")
        lines.append("")
        sources = r.get("sources", [])
        if sources:
            lines.append("Sources:")
            for s in sources:
                lines.append(f"  - {s.get('publication')}: {s.get('url')}")
            lines.append("")
        # Suppliers section intentionally hidden. Data may still exist on
        # records but is no longer displayed.
        lines.append(f"ID: {r['id']}")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")

    def _set_action_buttons_state(self, on):
        s = "normal" if on else "disabled"
        for b in (self.approve_btn, self.reject_btn, self.edit_btn, self.open_btn):
            b.configure(state=s)

    # ------------------------------------------------------------------
    # Actions: approve / reject / edit / open
    # ------------------------------------------------------------------
    def _approve(self):
        if self.selected_index is None:
            return
        r = self.pending.pop(self.selected_index)
        r["review"] = {"status": "approved", "reviewed_at": now_iso()}
        self.approved.append(r)
        self._persist_all()
        self._reload_all()

    def _reject(self):
        if self.selected_index is None:
            return
        r = self.pending.pop(self.selected_index)
        r["review"] = {"status": "rejected", "reviewed_at": now_iso()}
        self.rejected.append(r)
        self._persist_all()
        self._reload_all()

    def _edit(self):
        if self.selected_index is None:
            return
        r = self.pending[self.selected_index]
        EditDialog(self, r, on_save=self._on_edit_save)

    def _on_edit_save(self, updated):
        if self.selected_index is None:
            return
        self.pending[self.selected_index] = updated
        save_json(PENDING_PATH, self.pending)
        self._render_detail(updated)

    def _open_source(self):
        if self.selected_index is None:
            return
        r = self.pending[self.selected_index]
        sources = r.get("sources", [])
        if not sources:
            messagebox.showinfo("No source", "This record has no source URL.")
            return
        url = sources[0]["url"]
        import webbrowser
        webbrowser.open(url, new=2)

    def _persist_all(self):
        save_json(PENDING_PATH, self.pending)
        save_json(APPROVED_PATH, self.approved)
        save_json(REJECTED_PATH, self.rejected)

    # ------------------------------------------------------------------
    # Pipeline run (background thread)
    # ------------------------------------------------------------------
    def _run_pipeline(self):
        if self.pipeline_running:
            return
        self.pipeline_running = True
        self.run_btn.configure(state="disabled", text="Running...")
        self.notebook.select(1)  # jump to Log tab

        self._log_clear()
        self._log("Starting pipeline...\n\n")

        t = threading.Thread(target=self._pipeline_thread, daemon=True)
        t.start()

    def _pipeline_thread(self):
        try:
            proc = subprocess.Popen(
                [sys.executable, str(RUN_DAILY)],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log_queue.put(line)
            proc.wait()
            self.log_queue.put(f"\n[pipeline exited with code {proc.returncode}]\n")
        except Exception as e:
            self.log_queue.put(f"\n[ERROR launching pipeline: {e}]\n")
        finally:
            self.log_queue.put("__DONE__")

    def _drain_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item == "__DONE__":
                    self.pipeline_running = False
                    self.run_btn.configure(state="normal", text="Run Pipeline")
                    self._reload_all()
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.after(120, self._drain_log_queue)

    def _log_clear(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Copy git command
    # ------------------------------------------------------------------
    def _copy_git_cmd(self):
        self.clipboard_clear()
        self.clipboard_append(GIT_PUSH_CMD)
        # brief visual feedback by changing button text - not implemented here
        messagebox.showinfo("Copied", "git command copied to clipboard.")


# ---------------------------------------------------------------------------
# Edit dialog
# ---------------------------------------------------------------------------
class EditDialog(tk.Toplevel):
    def __init__(self, master, record, on_save):
        super().__init__(master)
        self.title(f"Edit - {record['company']['name']}")
        self.geometry("700x600")
        self.on_save = on_save

        ttk.Label(self, text="Edit the JSON below, then click Save. Invalid JSON will be rejected.", padding=8).pack(anchor="w")

        self.text = tk.Text(self, wrap="none", font=("Consolas", 10))
        self.text.pack(fill="both", expand=True, padx=8)
        self.text.insert("1.0", json.dumps(record, indent=2, ensure_ascii=False))

        btn_row = ttk.Frame(self, padding=8)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Save", command=self._save).pack(side="right", padx=4)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right")

    def _save(self):
        try:
            data = json.loads(self.text.get("1.0", "end"))
        except json.JSONDecodeError as e:
            messagebox.showerror("Invalid JSON", f"Could not parse: {e}")
            return
        self.on_save(data)
        self.destroy()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = AdminApp()
    app.mainloop()
