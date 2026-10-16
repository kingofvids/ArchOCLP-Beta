#!/usr/bin/env python3
"""Tkinter desktop UI for ArchOCLP. On Arch Linux install python-tk."""
from __future__ import annotations
import os
import queue
import threading
import subprocess
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from pathlib import Path
import webbrowser

import archoclp
from archoclp_features import (APP_VERSION, InstallerItem, build_efi, discover_apple_installers,
                               download_file, open_core_release_info, search_archive_images, validate_efi_path)


class ArchOCLPApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"ArchOCLP {APP_VERSION} — OpenCore for Linux")
        self.geometry("1120x760")
        self.minsize(900, 620)
        self.events: queue.Queue = queue.Queue()
        self.items: list[InstallerItem] = []
        self.cancel_event = threading.Event()
        self.download_dir = Path.home() / "Downloads" / "ArchOCLP"
        self.build_dir = Path.home() / "archoclp-builds"
        self._styles()
        self._build_ui()
        self.after(100, self._poll_events)
        self._start_thread(self._detect, "Initial hardware detection")

    def _styles(self):
        self.configure(bg="#f2f4f8")
        style = ttk.Style(self)
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure("TFrame", background="#f2f4f8")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Title.TLabel", font=("Sans", 20, "bold"), background="#f2f4f8", foreground="#13213c")
        style.configure("Sub.TLabel", font=("Sans", 10), background="#f2f4f8", foreground="#536176")
        style.configure("TButton", padding=(10, 7))
        style.configure("Primary.TButton", font=("Sans", 10, "bold"))
        style.configure("Treeview", rowheight=27)

    def _build_ui(self):
        header = ttk.Frame(self, padding=(18, 14, 18, 10)); header.pack(fill="x")
        ttk.Label(header, text="ArchOCLP", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Linux-native Mac detection · experimental EFI builder · macOS installer catalogue", style="Sub.TLabel").pack(anchor="w", pady=(3, 0))
        self.notebook = ttk.Notebook(self); self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.overview_tab = ttk.Frame(self.notebook, padding=14)
        self.download_tab = ttk.Frame(self.notebook, padding=14)
        self.log_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.overview_tab, text="  Mac & EFI Builder  ")
        self.notebook.add(self.download_tab, text="  macOS Downloads  ")
        self.notebook.add(self.log_tab, text="  Activity Log  ")
        self._build_overview()
        self._build_downloads()
        self.log = ScrolledText(self.log_tab, wrap="word", font=("Monospace", 10), height=24, relief="flat", borderwidth=8)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        footer = ttk.Frame(self, padding=(16, 0, 16, 12)); footer.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(footer, textvariable=self.status, style="Sub.TLabel").pack(side="left")
        ttk.Button(footer, text="Open output folder", command=self._open_output).pack(side="right")

    def _build_overview(self):
        self.overview_tab.columnconfigure(0, weight=1)
        card = ttk.LabelFrame(self.overview_tab, text="Hardware detection", padding=14)
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        self.host_model = tk.StringVar(value="Detecting…")
        self.host_status = tk.StringVar(value="Reading Linux DMI data")
        ttk.Label(card, text="Detected model:").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=3)
        ttk.Label(card, textvariable=self.host_model, font=("Sans", 11, "bold")).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(card, textvariable=self.host_status).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Button(card, text="Detect again", command=lambda: self._start_thread(self._detect, "Hardware detection")).grid(row=0, column=2, rowspan=2, padx=(14, 0))

        build = ttk.LabelFrame(self.overview_tab, text="Build an experimental OpenCore EFI", padding=14)
        build.grid(row=1, column=0, sticky="ew", pady=(14, 0)); build.columnconfigure(1, weight=1)
        ttk.Label(build, text="Target Mac identifier:").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        self.target_model = tk.StringVar(value="MacBookPro9,2")
        self.model_box = ttk.Combobox(build, textvariable=self.target_model, values=sorted(archoclp.load_models().keys()), width=24)
        self.model_box.grid(row=0, column=1, sticky="w", pady=5)
        ttk.Label(build, text="Output folder:").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        self.output_var = tk.StringVar(value=str(self.build_dir))
        ttk.Entry(build, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(build, text="Browse…", command=self._choose_build_dir).grid(row=1, column=2, padx=(8, 0))
        buttons = ttk.Frame(build); buttons.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.build_btn = ttk.Button(buttons, text="Build EFI files", style="Primary.TButton", command=self._build_efi)
        self.build_btn.pack(side="left")
        ttk.Button(buttons, text="Latest OpenCore release", command=lambda: self._start_thread(self._show_release, "OpenCore release lookup")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Validate existing EFI…", command=self._validate_existing).pack(side="left")
        option_row = ttk.Frame(build); option_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.opt_applealc = tk.BooleanVar(value=False)
        self.opt_restrictevents = tk.BooleanVar(value=False)
        self.opt_nvmefix = tk.BooleanVar(value=False)
        ttk.Label(option_row, text="Optional kexts (only enable when you know the hardware needs them):").pack(anchor="w")
        option_checks = ttk.Frame(option_row); option_checks.pack(anchor="w", pady=(3, 0))
        ttk.Checkbutton(option_checks, text="AppleALC (audio; may need layout-id)", variable=self.opt_applealc).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(option_checks, text="RestrictEvents", variable=self.opt_restrictevents).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(option_checks, text="NVMeFix (NVMe SSD only)", variable=self.opt_nvmefix).pack(side="left")
        self.progress = ttk.Progressbar(build, mode="indeterminate"); self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0))

        notice = ttk.LabelFrame(self.overview_tab, text="Important status", padding=12)
        notice.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(notice, text=("This assembles real OpenCore binaries and selected upstream kexts into an EFI folder. It is an experimental base EFI, "
            "NOT a complete port of OpenCore Legacy Patcher. It does not yet reproduce OCLP's full per-model ACPI/GPU/Wi‑Fi patches or macOS root patcher. "
            "Review SMBIOS values and model-specific settings and test from removable media first; never overwrite the internal EFI blindly."),
            wraplength=980, justify="left").pack(anchor="w")
        ttk.Label(notice, text="macOS downloads: Apple links are preferred. Internet Archive results are third-party and must be checked for provenance, integrity, and licence/compatibility.",
                  wraplength=980, justify="left", foreground="#694c00").pack(anchor="w", pady=(8, 0))

    def _build_downloads(self):
        self.download_tab.columnconfigure(0, weight=1); self.download_tab.rowconfigure(2, weight=1)
        top = ttk.Frame(self.download_tab); top.grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Refresh Apple / installer index", command=self._refresh_apple).pack(side="left")
        self.archive_query = tk.StringVar(value="Mac OS X installer")
        ttk.Entry(top, textvariable=self.archive_query, width=34).pack(side="left", padx=(10, 6))
        ttk.Button(top, text="Search archived DMGs / ISOs", command=self._search_archive).pack(side="left")
        ttk.Label(self.download_tab, text="Select an item to download. Recent InstallAssistant.pkg files and legacy Apple-hosted DMGs are discovered from live source pages; archived images are clearly marked third-party.",
                  wraplength=1000, justify="left").grid(row=1, column=0, sticky="w", pady=(10, 8))
        cols = ("kind", "version", "title", "source", "size", "trust")
        self.tree = ttk.Treeview(self.download_tab, columns=cols, show="headings", selectmode="browse")
        for col, title, width in [("kind","Type",70),("version","Version/Year",95),("title","Title / filename",330),("source","Source",160),("size","Size",90),("trust","Trust label",240)]:
            self.tree.heading(col, text=title); self.tree.column(col, width=width, anchor="w")
        self.tree.grid(row=2, column=0, sticky="nsew")
        sy = ttk.Scrollbar(self.download_tab, orient="vertical", command=self.tree.yview); sy.grid(row=2, column=1, sticky="ns"); self.tree.configure(yscrollcommand=sy.set)
        self.tree.bind("<Double-1>", lambda _e: self._open_selected_source())
        controls = ttk.Frame(self.download_tab); controls.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Download selected", style="Primary.TButton", command=self._download_selected).pack(side="left")
        ttk.Button(controls, text="Open source page", command=self._open_selected_source).pack(side="left", padx=8)
        ttk.Button(controls, text="Choose download folder", command=self._choose_download_dir).pack(side="left")
        ttk.Button(controls, text="Cancel download", command=self._cancel_download).pack(side="left", padx=8)
        self.download_dir_label = ttk.Label(controls, text=str(self.download_dir), style="Sub.TLabel")
        self.download_dir_label.pack(side="right")

    def _emit(self, kind, value): self.events.put((kind, value))
    def _log(self, message): self._emit("log", message)
    def _start_thread(self, fn, description):
        self.status.set(description + "…")
        threading.Thread(target=self._guard, args=(fn,), daemon=True).start()
    def _guard(self, fn):
        try: fn()
        except Exception as exc: self._emit("error", f"{type(exc).__name__}: {exc}")
    def _poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal"); self.log.insert("end", str(value) + "\n"); self.log.see("end"); self.log.configure(state="disabled")
                elif kind == "detect":
                    self.host_model.set(value.get("model_identifier") or "Not detected")
                    self.host_status.set(f"{value.get('detection_status')} · {value.get('detection_source')}")
                    if value.get("model_identifier"): self.target_model.set(value["model_identifier"])
                    self.status.set("Hardware detection complete")
                elif kind == "catalog":
                    self.items = value; self._refresh_tree(); self.status.set(f"Loaded {len(value)} download candidates")
                elif kind == "done":
                    self.status.set(str(value)); self.progress.stop(); self.build_btn.configure(state="normal")
                elif kind == "error":
                    self.status.set("An operation failed"); self.progress.stop(); self.build_btn.configure(state="normal")
                    messagebox.showerror("ArchOCLP", str(value)); self._log("ERROR: " + str(value))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _detect(self):
        result = archoclp.detect_hardware()
        self._emit("detect", result)
        self._log("Hardware probe: " + str(result))

    def _choose_build_dir(self):
        result = filedialog.askdirectory(initialdir=self.output_var.get() or str(Path.home()))
        if result: self.output_var.set(result)
    def _choose_download_dir(self):
        result = filedialog.askdirectory(initialdir=str(self.download_dir))
        if result:
            self.download_dir = Path(result); self.download_dir_label.configure(text=str(self.download_dir))
    def _open_output(self):
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except (OSError, FileNotFoundError):
            webbrowser.open(path.as_uri())
    def _build_efi(self):
        model = self.target_model.get().strip()
        if not model:
            messagebox.showwarning("Model required", "Enter a Mac model identifier, for example MacBookPro9,2."); return
        if not messagebox.askyesno("Experimental EFI builder", f"Download upstream OpenCore/kext assets and build an experimental EFI for {model}?\n\nThis does not install to a disk. The result is not a complete, validated OCLP build."):
            return
        self.build_btn.configure(state="disabled"); self.progress.start(10); self.notebook.select(self.log_tab)
        optional = []
        if self.opt_applealc.get(): optional.append("AppleALC")
        if self.opt_restrictevents.get(): optional.append("RestrictEvents")
        if self.opt_nvmefix.get(): optional.append("NVMeFix")
        output_path = Path(self.output_var.get()).expanduser()
        def work():
            result = build_efi(model, output_path, progress=self._log, optional_kexts=optional)
            self._emit("log", "Build finished: " + result["path"])
            self._emit("done", "EFI files assembled; review BUILD-REPORT.md before use")
        self._start_thread(work, "Building EFI")

    def _validate_existing(self):
        selected = filedialog.askdirectory(title="Select EFI folder or build folder", initialdir=str(self.build_dir))
        if not selected: return
        def work():
            problems = validate_efi_path(Path(selected))
            if problems:
                self._log("Local EFI consistency check FAILED:")
                for problem in problems: self._log(" - " + problem)
                self._emit("done", "EFI path check failed; see Activity Log")
            else:
                self._log("Local EFI file/path consistency check PASSED; full ocvalidate and bootability remain unverified.")
                self._emit("done", "Local EFI file/path check passed (not a bootability check)")
        self._start_thread(work, "Validating EFI")

    def _cancel_download(self):
        self.cancel_event.set()
        self.status.set("Cancel requested for active download…")

    def _show_release(self):
        info = open_core_release_info(); self._log("Latest OpenCore release: " + str(info)); self._emit("done", f"Latest OpenCore: {info['version']}")

    def _refresh_apple(self):
        self._start_thread(lambda: self._load_catalog(discover_apple_installers(progress=self._log)), "Refreshing Apple installer links")
    def _search_archive(self):
        query = self.archive_query.get().strip()
        if not query: return
        self._start_thread(lambda: self._load_catalog(search_archive_images(query, progress=self._log)), "Searching archived installers")
    def _load_catalog(self, items):
        self._emit("catalog", items)
        if not items: self._log("No direct-download candidates discovered. Source links may have changed; open Apple Support / Mr. Macintosh manually.")
    def _refresh_tree(self):
        for row in self.tree.get_children(): self.tree.delete(row)
        for idx, item in enumerate(self.items):
            self.tree.insert("", "end", iid=str(idx), values=(item.kind, item.version, item.title, item.source, item.size, item.trust))
    def _selected_item(self):
        selected = self.tree.selection()
        if not selected: return None
        idx = int(selected[0]); return self.items[idx] if 0 <= idx < len(self.items) else None
    def _open_selected_source(self):
        item = self._selected_item()
        if item: webbrowser.open(item.source_page)
    def _download_selected(self):
        item = self._selected_item()
        if not item:
            messagebox.showinfo("Select a download", "Choose a row from the download list first."); return
        if "Third-party" in item.trust:
            if not messagebox.askyesno("Third-party archive", "This image is from a third-party archive, not Apple. Verify its provenance, checksum, licence and compatibility. Download anyway?"):
                return
        file_name = Path(urllib.parse.unquote(urllib.parse.urlparse(item.url).path)).name or "macOS-installer-download"
        dest = self.download_dir / file_name
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            index = 1
            while dest.exists():
                dest = self.download_dir / f"{stem} ({index}){suffix}"
                index += 1
        self.cancel_event.clear()
        self._start_thread(lambda: self._download_worker(item, dest), "Downloading installer")
    def _download_worker(self, item, dest):
        try:
            result = download_file(item.url, dest, progress=self._log, cancel_event=self.cancel_event)
        except InterruptedError:
            self._log("Download cancelled; the temporary .part file was removed.")
            self._emit("done", "Download cancelled")
            return
        self._log(f"Download complete: {result}")
        self._emit("done", f"Downloaded {dest.name} ({result['bytes']:,} bytes); SHA-256 sidecar saved")


def main():
    app = ArchOCLPApp(); app.mainloop()

if __name__ == "__main__": main()
