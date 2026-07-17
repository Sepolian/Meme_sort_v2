from __future__ import annotations

import json
import os
import threading
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import END, BOTH, DISABLED, NORMAL, StringVar, Tk, filedialog, messagebox, ttk

from .launcher import default_library_root, resolve_preferred_port
from .native_shell import pick_folder
from .webapp import run_web_app


@dataclass
class DesktopServerState:
    library_root: Path
    host: str
    port: int
    url: str


class DesktopAppShell:
    def __init__(
        self,
        library_root: str | None = None,
        autostart_ui: bool = False,
        import_source: str | None = None,
        open_ui_on_ready: bool = True,
    ) -> None:
        self.root = Tk()
        self.root.title("MemeSort")
        self.root.geometry("880x680")
        self.root.minsize(760, 560)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._server_thread: threading.Thread | None = None
        self._server_ready_event = threading.Event()
        self._server_payload: DesktopServerState | None = None
        self._server_error: Exception | None = None
        self._pending_post_action = None
        self._open_ui_on_ready = open_ui_on_ready

        initial_library_root = (
            Path(library_root).expanduser().resolve()
            if library_root is not None
            else default_library_root().resolve()
        )
        self.library_var = StringVar(value=str(initial_library_root))
        self.import_source_var = StringVar(value=import_source or "")
        self.status_var = StringVar(value="Not running")
        self.url_var = StringVar(value="No local UI yet")
        self.setup_summary_var = StringVar(value="Setup progress becomes available after the local UI starts.")
        self.setup_next_step_var = StringVar(value="Start the local UI to inspect the first-run guide.")
        self.runtime_detail_var = StringVar(value="No runtime selection loaded yet.")
        self.model_detail_var = StringVar(value="No model source discovered yet.")

        self._build_ui()
        self._append_log("Ready. Choose a library root or keep the default AppData location.")
        if autostart_ui:
            action = self._start_server_and_import if import_source else self._start_server
            self.root.after(100, action)

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill=BOTH, expand=True)
        frame.columnconfigure(0, weight=1)

        title = ttk.Label(frame, text="MemeSort", font=("Segoe UI", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")

        subtitle = ttk.Label(
            frame,
            text=(
                "Thin local Windows shell for the worker-backed app. "
                "Use it to choose the managed library location, start the local UI, and inspect setup diagnostics."
            ),
            wraplength=680,
            justify="left",
        )
        subtitle.grid(row=1, column=0, sticky="ew", pady=(8, 18))

        library_label = ttk.Label(frame, text="Library Root")
        library_label.grid(row=2, column=0, sticky="w")

        picker = ttk.Frame(frame)
        picker.grid(row=3, column=0, sticky="ew", pady=(6, 18))
        picker.columnconfigure(0, weight=1)

        self.library_entry = ttk.Entry(picker, textvariable=self.library_var)
        self.library_entry.grid(row=0, column=0, sticky="ew")

        browse_btn = ttk.Button(picker, text="Browse", command=self._choose_library_root)
        browse_btn.grid(row=0, column=1, padx=(10, 0))

        import_label = ttk.Label(frame, text="First Import Source Folder")
        import_label.grid(row=4, column=0, sticky="w")

        import_picker = ttk.Frame(frame)
        import_picker.grid(row=5, column=0, sticky="ew", pady=(6, 18))
        import_picker.columnconfigure(0, weight=1)

        self.import_entry = ttk.Entry(import_picker, textvariable=self.import_source_var)
        self.import_entry.grid(row=0, column=0, sticky="ew")

        import_browse_btn = ttk.Button(import_picker, text="Browse", command=self._choose_import_source)
        import_browse_btn.grid(row=0, column=1, padx=(10, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, sticky="w", pady=(0, 18))

        self.start_btn = ttk.Button(actions, text="Start Local UI", command=self._start_server)
        self.start_btn.grid(row=0, column=0)

        self.start_import_btn = ttk.Button(
            actions,
            text="Start and Import",
            command=self._start_server_and_import,
        )
        self.start_import_btn.grid(row=0, column=1, padx=(10, 0))

        self.open_btn = ttk.Button(actions, text="Open UI", command=self._open_ui, state=DISABLED)
        self.open_btn.grid(row=0, column=2, padx=(10, 0))

        self.logs_btn = ttk.Button(actions, text="Open Logs Folder", command=self._open_logs_dir, state=DISABLED)
        self.logs_btn.grid(row=0, column=3, padx=(10, 0))

        self.copy_btn = ttk.Button(actions, text="Copy URL", command=self._copy_url, state=DISABLED)
        self.copy_btn.grid(row=0, column=4, padx=(10, 0))

        status_frame = ttk.LabelFrame(frame, text="Runtime Status", padding=14)
        status_frame.grid(row=7, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)

        ttk.Label(status_frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.url_var, foreground="#555555").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Label(
            status_frame,
            textvariable=self.setup_summary_var,
            foreground="#555555",
            wraplength=640,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            status_frame,
            textvariable=self.setup_next_step_var,
            foreground="#555555",
            wraplength=640,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            status_frame,
            textvariable=self.runtime_detail_var,
            foreground="#555555",
            wraplength=640,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            status_frame,
            textvariable=self.model_detail_var,
            foreground="#555555",
            wraplength=640,
            justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(6, 0))

        setup_frame = ttk.LabelFrame(frame, text="Setup Diagnostics", padding=14)
        setup_frame.grid(row=8, column=0, sticky="nsew", pady=(18, 0))
        setup_frame.columnconfigure(0, weight=1)
        frame.rowconfigure(8, weight=1)

        self.setup_tree = ttk.Treeview(setup_frame, columns=("status", "detail"), show="headings", height=8)
        self.setup_tree.heading("status", text="Status")
        self.setup_tree.heading("detail", text="Detail")
        self.setup_tree.column("status", width=120, anchor="w")
        self.setup_tree.column("detail", width=620, anchor="w")
        self.setup_tree.pack(fill=BOTH, expand=True)

        log_frame = ttk.LabelFrame(frame, text="Launcher Log", padding=14)
        log_frame.grid(row=9, column=0, sticky="nsew", pady=(18, 0))
        frame.rowconfigure(9, weight=1)

        self.log_text = ttk.Treeview(log_frame, columns=("message",), show="tree", selectmode="none", height=10)
        self.log_text.pack(fill=BOTH, expand=True)

    def _append_log(self, message: str) -> None:
        self.log_text.insert("", END, text=message)
        children = self.log_text.get_children("")
        if children:
            self.log_text.see(children[-1])

    def _choose_library_root(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose MemeSort library root",
            initialdir=self.library_var.get() or str(default_library_root()),
            mustexist=False,
        )
        if selected:
            self.library_var.set(selected)

    def _choose_import_source(self) -> None:
        initial_dir = self.import_source_var.get() or str(Path.home())
        try:
            selected = pick_folder(
                title="Choose a folder to import into MemeSort",
                initial_path=initial_dir,
            )
        except Exception as exc:
            messagebox.showerror("Folder Picker Error", str(exc))
            self._append_log(f"Import source picker failed: {exc}")
            return
        if selected:
            self.import_source_var.set(selected)

    def _start_server(self) -> None:
        self._start_server_with_post_action(None)

    def _start_server_and_import(self) -> None:
        import_source = self.import_source_var.get().strip()
        if not import_source:
            messagebox.showerror("Missing Import Folder", "Choose a source folder before using Start and Import.")
            return
        self._start_server_with_post_action(lambda: self._trigger_import_and_index(import_source))

    def _start_server_with_post_action(self, post_action) -> None:
        if self._server_thread is not None and self._server_thread.is_alive():
            self._append_log("Local UI is already running.")
            if post_action is not None:
                self.root.after(100, post_action)
            return

        library_root = Path(self.library_var.get()).expanduser().resolve()
        library_root.mkdir(parents=True, exist_ok=True)
        port = resolve_preferred_port("127.0.0.1", 8765)

        self._server_ready_event.clear()
        self._server_payload = None
        self._server_error = None
        self.status_var.set("Starting local UI...")
        self.url_var.set("Waiting for worker-backed web shell")
        self.start_btn.configure(state=DISABLED)
        self.start_import_btn.configure(state=DISABLED)
        self._append_log(f"Starting local UI for library root: {library_root}")
        self._pending_post_action = post_action

        self._server_thread = threading.Thread(
            target=self._run_server_thread,
            args=(library_root, port),
            name="MemeSortDesktopServer",
            daemon=True,
        )
        self._server_thread.start()
        self.root.after(200, self._poll_server_ready)

    def _run_server_thread(self, library_root: Path, port: int) -> None:
        try:
            run_web_app(
                str(library_root),
                host="127.0.0.1",
                port=port,
                on_started=self._on_server_started,
            )
        except Exception as exc:
            self._server_error = exc
            self._server_ready_event.set()

    def _on_server_started(self, payload: dict[str, object]) -> None:
        self._server_payload = DesktopServerState(
            library_root=Path(str(payload["library_root"])),
            host=str(payload["host"]),
            port=int(payload["port"]),
            url=str(payload["url"]),
        )
        self._server_ready_event.set()

    def _poll_server_ready(self) -> None:
        if not self._server_ready_event.is_set():
            self.root.after(200, self._poll_server_ready)
            return

        if self._server_error is not None:
            self.status_var.set("Failed to start local UI")
            self.url_var.set(str(self._server_error))
            self.start_btn.configure(state=NORMAL)
            self.start_import_btn.configure(state=NORMAL)
            self._append_log(f"Startup failed: {self._server_error}")
            return

        payload = self._server_payload
        if payload is None:
            self.status_var.set("Startup state unavailable")
            self.start_btn.configure(state=NORMAL)
            self.start_import_btn.configure(state=NORMAL)
            self._append_log("Startup failed: server did not report a URL.")
            return

        self.status_var.set("Running")
        self.url_var.set(payload.url)
        self.start_import_btn.configure(state=NORMAL)
        self.open_btn.configure(state=NORMAL)
        self.logs_btn.configure(state=NORMAL)
        self.copy_btn.configure(state=NORMAL)
        self._append_log(f"Local UI running at {payload.url}")
        self._append_log(f"Logs directory: {payload.library_root / 'logs'}")
        if self._open_ui_on_ready:
            self.root.after(300, self._open_ui)
        self.root.after(500, self._refresh_setup_summary)
        if getattr(self, "_pending_post_action", None) is not None:
            action = self._pending_post_action
            self._pending_post_action = None
            self.root.after(600, action)

    def _trigger_import_and_index(self, import_source: str) -> None:
        payload = self._server_payload
        if payload is None:
            return
        self._append_log(f"Importing source folder and starting indexing: {import_source}")
        thread = threading.Thread(
            target=self._import_and_index_worker,
            args=(payload.url, import_source),
            name="MemeSortDesktopImport",
            daemon=True,
        )
        thread.start()

    def _refresh_setup_summary(self) -> None:
        payload = self._server_payload
        if payload is None:
            return
        thread = threading.Thread(
            target=self._fetch_setup_summary_worker,
            args=(payload.url,),
            name="MemeSortSetupSummary",
            daemon=True,
        )
        thread.start()

    def _fetch_setup_summary_worker(self, base_url: str) -> None:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/api/state", timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.root.after(0, lambda: self._apply_setup_state(payload))
        except Exception as exc:
            self.root.after(0, lambda: self.setup_summary_var.set(f"Unable to refresh setup summary: {exc}"))

    def _apply_setup_state(self, payload: dict[str, object]) -> None:
        setup_state = payload.get("setup_state", {}) if isinstance(payload, dict) else {}
        if not isinstance(setup_state, dict):
            setup_state = {}
        checklist = setup_state.get("checklist", [])
        if not isinstance(checklist, list) or not checklist:
            self.setup_summary_var.set("Setup progress is not available yet.")
            self.setup_next_step_var.set("Run the local UI first.")
            self.runtime_detail_var.set("Runtime readiness details are not available yet.")
            self.model_detail_var.set("Model source details are not available yet.")
            self._replace_setup_rows([])
            return

        completed = len([item for item in checklist if isinstance(item, dict) and item.get("done")])
        total = len(checklist)
        next_items = [
            str(item.get("label"))
            for item in checklist
            if isinstance(item, dict) and not item.get("done") and item.get("label")
        ]
        self.setup_summary_var.set(f"Setup checklist: {completed}/{total} complete.")
        if next_items:
            self.setup_next_step_var.set(f"Next step: {next_items[0]}.")
        else:
            self.setup_next_step_var.set("Next step: ready for search, import, and duplicate review.")

        runtime_readiness = setup_state.get("runtime_readiness", {})
        if not isinstance(runtime_readiness, dict):
            runtime_readiness = {}
        device = str(runtime_readiness.get("device") or "n/a")
        model_label = str(runtime_readiness.get("model_label") or "n/a")
        backend_name = str(runtime_readiness.get("backend_name") or "n/a")
        ready_detail = str(runtime_readiness.get("ready_detail") or "No runtime readiness summary.")
        self.runtime_detail_var.set(
            f"Runtime: {device} / {model_label} / {backend_name} | {ready_detail}"
        )

        model_source = str(runtime_readiness.get("model_source") or "No model source ready yet.")
        gpu_vendor = str(runtime_readiness.get("last_health_gpu_vendor") or "unknown vendor")
        gpu_vendor_id = str(runtime_readiness.get("last_health_gpu_vendor_id") or "unknown id")
        vector_dim = runtime_readiness.get("last_health_text_smoke_vector_dim")
        image_dim = runtime_readiness.get("last_health_image_smoke_vector_dim")
        model_detail = f"Model source: {model_source} | GPU {gpu_vendor} ({gpu_vendor_id})"
        if vector_dim is not None:
            model_detail += f" | text smoke {vector_dim}d"
        if image_dim is not None:
            model_detail += f" | image smoke {image_dim}d"
        self.model_detail_var.set(model_detail)

        rows: list[tuple[str, str]] = []
        for item in checklist:
            if not isinstance(item, dict):
                continue
            status = "Done" if item.get("done") else "Next"
            label = str(item.get("label") or "step")
            detail = str(item.get("detail") or "")
            rows.append((status, f"{label}: {detail}".strip()))

        diagnostic_steps = runtime_readiness.get("last_health_diagnostic_steps", [])
        if isinstance(diagnostic_steps, list):
            for step in diagnostic_steps[:6]:
                if not isinstance(step, dict):
                    continue
                status = "OK" if step.get("status") == "ok" else "Issue"
                name = str(step.get("step") or "diagnostic")
                detail = str(step.get("detail") or "")
                rows.append((status, f"{name}: {detail}".strip()))

        self._replace_setup_rows(rows)

    def _replace_setup_rows(self, rows: list[tuple[str, str]]) -> None:
        for item_id in self.setup_tree.get_children(""):
            self.setup_tree.delete(item_id)
        for status, detail in rows:
            self.setup_tree.insert("", END, values=(status, detail))

    def _import_and_index_worker(self, base_url: str, import_source: str) -> None:
        try:
            request = urllib.request.Request(
                base_url.rstrip("/") + "/api/import-and-start-index",
                data=json.dumps({"path": import_source}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.root.after(0, lambda: self._append_log(
                "Import and background indexing started: "
                f"new_assets={payload['import_result']['new_assets']}, "
                f"duplicates={payload['import_result']['duplicate_assets']}"
            ))
            self.root.after(200, self._refresh_setup_summary)
        except Exception as exc:
            self.root.after(0, lambda: self._append_log(f"Import and indexing failed: {exc}"))

    def _open_ui(self) -> None:
        payload = self._server_payload
        if payload is None:
            return
        webbrowser.open(payload.url)
        self._append_log(f"Opened UI in default browser: {payload.url}")

    def _open_logs_dir(self) -> None:
        payload = self._server_payload
        if payload is None:
            return
        logs_dir = payload.library_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(logs_dir))  # type: ignore[attr-defined]
        self._append_log(f"Opened logs directory: {logs_dir}")

    def _copy_url(self) -> None:
        payload = self._server_payload
        if payload is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(payload.url)
        self._append_log(f"Copied local UI URL: {payload.url}")

    def _on_close(self) -> None:
        if self._server_thread is not None and self._server_thread.is_alive():
            if not messagebox.askyesno(
                "Exit MemeSort",
                "The local UI server will stop when this launcher window closes. Exit now?",
            ):
                return
        self.root.destroy()


def launch_desktop_shell(
    library_root: str | None = None,
    autostart_ui: bool = False,
    import_source: str | None = None,
    open_ui_on_ready: bool = True,
) -> None:
    DesktopAppShell(
        library_root=library_root,
        autostart_ui=autostart_ui,
        import_source=import_source,
        open_ui_on_ready=open_ui_on_ready,
    ).run()
