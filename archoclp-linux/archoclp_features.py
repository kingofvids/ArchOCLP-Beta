#!/usr/bin/env python3
"""Network, installer catalog and experimental OpenCore EFI assembly for ArchOCLP.

The EFI builder assembles upstream OpenCore binaries and selected kext releases and
writes a model-tagged Sample.plist-based config. It is a genuine EFI tree, but NOT
a drop-in replacement for OCLP's hardware probe/root patches and must be reviewed
and validated for the target Mac before use.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import plistlib
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
from typing import Callable, Any

APP_VERSION = "0.2.0"
GITHUB_API = "https://api.github.com"
APPLE_INSTALLER_PAGE = "https://support.apple.com/en-gb/102662"
MR_MACINTOSH_ARCHIVE = "https://mrmacintosh.com/category/macos-installer/"
MR_MACINTOSH_OLD = "https://mrmacintosh.com/how-to-download-macos-catalina-mojave-or-high-sierra-full-installers/"
IA_SEARCH = "https://archive.org/advancedsearch.php"
UA = "ArchOCLP/0.2.0 (Linux; OpenCore EFI and macOS installer catalog)"
Progress = Callable[[str], None]

# Small direct kext set for a base Intel Mac EFI. Model-specific add-ons are not
# guessed automatically; a later OCLP-native port must apply their rules.
CORE_KEXT_REPOS = [
    ("acidanthera/Lilu", "Lilu.kext", "Lilu"),
    ("acidanthera/WhateverGreen", "WhateverGreen.kext", "WhateverGreen"),
]
OPTIONAL_KEXT_REPOS = {
    "AppleALC": ("acidanthera/AppleALC", "AppleALC.kext", "AppleALC"),
    "RestrictEvents": ("acidanthera/RestrictEvents", "RestrictEvents.kext", "RestrictEvents"),
    "NVMeFix": ("acidanthera/NVMeFix", "NVMeFix.kext", "NVMeFix"),
}


def _request(url: str, *, timeout: int = 30, headers: dict[str, str] | None = None):
    req_headers = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    response = urllib.request.urlopen(req, timeout=timeout)
    if urllib.parse.urlparse(response.geturl()).scheme != "https":
        response.close()
        raise ValueError("Refusing non-HTTPS redirect")
    return response


def _get_json(url: str) -> Any:
    with _request(url, headers={"Accept": "application/vnd.github+json"}) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _safe_extract(zip_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"Unsafe archive path: {info.filename}")
            out = (destination / Path(*member.parts)).resolve()
            if root not in out.parents and out != root:
                raise ValueError(f"Archive path escapes destination: {info.filename}")
        zf.extractall(destination)


def download_file(url: str, destination: Path, progress: Progress | None = None,
                  cancel_event=None, expected_sha256: str | None = None) -> dict[str, Any]:
    """Stream a HTTPS URL to disk, verify size/hash, and rename atomically."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only HTTPS downloads are allowed")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Destination already exists; choose another filename: {destination}")
    part = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    received = 0
    last_progress_time = 0.0
    last_progress_percent = -1
    try:
        with _request(url, timeout=60) as response, part.open("wb") as stream:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type.startswith("text/html") and destination.suffix.lower() in {".dmg", ".pkg", ".iso", ".img", ".cdr", ".ipsw", ".zip"}:
                raise ValueError(f"The server returned an HTML page instead of {destination.suffix} data (content-type: {content_type})")
            length_header = response.headers.get("Content-Length")
            total = int(length_header) if length_header and length_header.isdigit() else None
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Download cancelled")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress:
                    now = time.monotonic()
                    current_percent = int(received * 100 / total) if total else -1
                    should_report = (now - last_progress_time >= 2.0 or
                                     (current_percent >= 0 and current_percent // 5 > last_progress_percent // 5))
                    if should_report:
                        percent = f"{received * 100 / total:.1f}%" if total else f"{received:,} bytes"
                        progress(f"Downloading {destination.name}: {percent} ({received:,} bytes)")
                        last_progress_time = now
                        last_progress_percent = current_percent
        sha = digest.hexdigest()
        if total is not None and received != total:
            raise ValueError(f"Download incomplete: server advertised {total} bytes, received {received}")
        if expected_sha256 and sha.lower() != expected_sha256.lower():
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {sha}")
        if received == 0:
            raise ValueError("Server returned an empty download")
        part.replace(destination)
        checksum_path = destination.with_name(destination.name + ".sha256")
        try:
            checksum_path.write_text(f"{sha}  {destination.name}\n", encoding="ascii")
        except OSError:
            # Keep a successful download even if the destination is read-only for sidecars.
            pass
        return {"path": str(destination), "bytes": received, "sha256": sha, "sha256_file": str(checksum_path), "url": url}
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _asset_by_name(release: dict[str, Any], predicate: Callable[[str], bool]) -> dict[str, Any] | None:
    assets = release.get("assets", [])
    matches = [item for item in assets if predicate(str(item.get("name", "")))]
    if not matches:
        return None
    # Prefer a zip, then smallest-ish matching asset if duplicates exist.
    matches.sort(key=lambda a: (0 if str(a.get("name", "")).lower().endswith(".zip") else 1,
                                str(a.get("name", ""))))
    return matches[0]


def latest_release_asset(repo: str, predicate: Callable[[str], bool]) -> tuple[str, str]:
    release = _get_json(f"{GITHUB_API}/repos/{repo}/releases/latest")
    asset = _asset_by_name(release, predicate)
    if not asset:
        raise RuntimeError(f"No matching release asset found for {repo} ({release.get('tag_name', 'unknown release')})")
    return str(asset["browser_download_url"]), str(asset["name"])


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=False)


def _find_named(root: Path, name: str) -> Path | None:
    for p in root.rglob(name):
        if p.is_file() or p.is_dir():
            return p
    return None


def _kext_config_entry(kext_path: Path, bundle_path: str | None = None) -> dict[str, Any]:
    info_path = kext_path / "Contents" / "Info.plist"
    if not info_path.exists():
        raise ValueError(f"Kext missing Contents/Info.plist: {kext_path}")
    with info_path.open("rb") as f:
        info = plistlib.load(f)
    executable = str(info.get("CFBundleExecutable", ""))
    if not executable:
        raise ValueError(f"Kext has no CFBundleExecutable: {kext_path}")
    return {
        "Arch": "Any",
        "BundlePath": bundle_path or kext_path.name,
        "Comment": str(info.get("CFBundleIdentifier", kext_path.stem)),
        "Enabled": True,
        "ExecutablePath": f"Contents/MacOS/{executable}",
        "MaxKernel": "",
        "MinKernel": "",
        "PlistPath": "Contents/Info.plist",
    }


def _build_config(sample: Path, oc_dir: Path, model: str) -> tuple[Path, list[str]]:
    with sample.open("rb") as f:
        cfg = plistlib.load(f)
    warnings: list[str] = []

    # The sample plist is version-matched to the OpenCore binaries downloaded.
    # Enable only files actually assembled into this EFI.
    cfg.setdefault("Kernel", {})["Add"] = []
    kext_dir = oc_dir / "Kexts"
    kext_order = {name: idx for idx, name in enumerate([
        "Lilu.kext", "WhateverGreen.kext", "AppleALC.kext", "RestrictEvents.kext", "NVMeFix.kext"
    ])}
    root_kexts = sorted(kext_dir.glob("*.kext"), key=lambda p: (kext_order.get(p.name, 999), p.name.lower()))
    for root_kext in root_kexts:
        cfg["Kernel"]["Add"].append(_kext_config_entry(root_kext))
        plugins = root_kext / "Contents" / "PlugIns"
        if plugins.is_dir():
            for plugin in sorted(plugins.glob("*.kext"), key=lambda p: p.name.lower()):
                relative = f"{root_kext.name}/Contents/PlugIns/{plugin.name}"
                cfg["Kernel"]["Add"].append(_kext_config_entry(plugin, relative))

    drivers = cfg.setdefault("UEFI", {}).setdefault("Drivers", [])
    available_drivers = {p.name for p in (oc_dir / "Drivers").glob("*") if p.is_file()}
    # Remove entries whose files aren't shipped in this EFI, then activate OpenRuntime.
    drivers[:] = [d for d in drivers if str(d.get("Path", "")) in available_drivers]
    runtime = next((d for d in drivers if d.get("Path") == "OpenRuntime.efi"), None)
    if runtime:
        runtime["Enabled"] = True
    else:
        warnings.append("OpenRuntime.efi is missing from the upstream package; UEFI driver setup needs review.")

    boot = cfg.setdefault("Misc", {}).setdefault("Boot", {})
    boot["ShowPicker"] = True
    boot["Timeout"] = 5
    boot["PickerMode"] = "Builtin"
    security = cfg.setdefault("Misc", {}).setdefault("Security", {})
    security["ScanPolicy"] = 0
    security["SecureBootModel"] = "Disabled"
    nvram_add = cfg.setdefault("NVRAM", {}).setdefault("Add", {})
    apple_guid = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
    nvram_add.setdefault(apple_guid, {})
    nvram_add[apple_guid].setdefault("boot-args", "")
    # Don't inject fabricated Apple serial / MLB / ROM values.
    platform_info = cfg.setdefault("PlatformInfo", {})
    platform_info["Automatic"] = True
    platform_info.setdefault("UpdateDataHub", True)
    platform_info.setdefault("UpdateNVRAM", True)
    platform_info.setdefault("UpdateSMBIOS", True)
    platform_info.setdefault("UpdateSMBIOSMode", "Create")
    generic = platform_info.setdefault("Generic", {})
    generic["SystemProductName"] = model
    generic["SystemSerialNumber"] = ""
    generic["MLB"] = ""
    generic["ROM"] = b"\x00\x00\x00\x00\x00\x00"
    generic["SystemUUID"] = "00000000-0000-0000-0000-000000000000"
    warnings.extend([
        "This is an experimental OpenCore base EFI, not a complete OCLP port.",
        "The config is based on OpenCore's version-matched Sample.plist with generic settings; it is NOT proven bootable on this model.",
        "No fabricated serial number, MLB, or ROM was inserted. SMBIOS values and model-specific quirks must be reviewed.",
        "OCLP-specific ACPI, GPU root-patching, legacy Wi-Fi, storage and other per-model logic are not implemented here.",
        "Use removable media for first-boot testing and keep a known-good boot path. Do not install this EFI internally without expert review.",
    ])
    config_path = oc_dir / "config.plist"
    with config_path.open("wb") as f:
        plistlib.dump(cfg, f, sort_keys=True)
    return config_path, warnings


def _validate_tree(efi_root: Path) -> list[str]:
    problems = []
    oc = efi_root / "EFI" / "OC"
    required = [efi_root / "EFI" / "BOOT" / "BOOTx64.efi", oc / "OpenCore.efi", oc / "config.plist", oc / "Drivers" / "OpenRuntime.efi"]
    for path in required:
        if not path.is_file():
            problems.append(f"Missing required EFI file: {path.relative_to(efi_root)}")
    cfg_path = oc / "config.plist"
    if cfg_path.is_file():
        try:
            with cfg_path.open("rb") as f:
                cfg = plistlib.load(f)
            for item in cfg.get("Kernel", {}).get("Add", []):
                bpath = oc / "Kexts" / item.get("BundlePath", "")
                if item.get("Enabled") and not bpath.exists():
                    problems.append(f"Enabled kext missing: {item.get('BundlePath')}")
            for item in cfg.get("UEFI", {}).get("Drivers", []):
                if item.get("Enabled") and not (oc / "Drivers" / item.get("Path", "")).exists():
                    problems.append(f"Enabled driver missing: {item.get('Path')}")
        except Exception as exc:
            problems.append(f"config.plist can't be parsed: {exc}")
    return problems


def validate_efi_path(path: Path) -> list[str]:
    """Validate a local EFI file tree, not the full OpenCore schema or bootability."""
    path = path.expanduser().resolve()
    if path.name == "EFI" and path.is_dir():
        return _validate_tree(path.parent)
    if (path / "EFI").is_dir():
        return _validate_tree(path)
    raise ValueError("Select the EFI directory itself or its parent build directory")


def build_efi(model: str, output: Path, progress: Progress | None = None,
              optional_kexts: list[str] | None = None) -> dict[str, Any]:
    """Download upstream release binaries and produce a genuine experimental EFI tree."""
    if not re.fullmatch(r"(?:MacBookPro|MacBookAir|MacBook|Macmini|iMac|MacPro|Xserve|iMacPro|MacStudio|Mac\d+)[A-Za-z0-9]*\d+,\d+", model):
        raise ValueError(f"Invalid Mac model identifier: {model}")
    # OCLP is for Intel-era genuine Mac configurations; Apple Silicon has a different boot chain.
    if model.startswith(("MacBookPro17,", "MacBookPro18,", "MacBookPro19,", "MacBookPro20,",
                         "MacBookAir10,", "MacBookAir11,", "MacBookAir15,", "MacBookAir16,",
                         "Macmini9,", "iMac21,", "MacStudio13,", "MacStudio14,", "MacPro14,8",
                         "Mac14,", "Mac15,", "Mac16,", "Mac17,", "Mac18,")):
        raise ValueError("Apple Silicon models do not use this Intel OpenCore EFI builder; refusing to create a misleading EFI.")
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / model
    if target.exists():
        # Preserve old output; use timestamp suffix rather than delete user data.
        target = output / f"{model}-{time.strftime('%Y%m%d-%H%M%S')}"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "archoclp" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)

    def log(message: str):
        if progress:
            progress(message)

    with tempfile.TemporaryDirectory(prefix="archoclp-build-") as tmp_s:
        tmp = Path(tmp_s)
        log("Looking up the latest OpenCorePkg RELEASE…")
        oc_url, oc_name = latest_release_asset("acidanthera/OpenCorePkg", lambda n: "RELEASE" in n.upper() and n.lower().endswith(".zip"))
        oc_zip = cache / oc_name
        if not oc_zip.exists() or oc_zip.stat().st_size == 0:
            oc_zip.unlink(missing_ok=True)
            download_file(oc_url, oc_zip, progress=log)
        extract_oc = tmp / "oc"
        extract_oc.mkdir()
        _safe_extract(oc_zip, extract_oc)
        sample = _find_named(extract_oc, "Sample.plist")
        efi_dir = None
        for candidate in extract_oc.rglob("EFI"):
            if candidate.is_dir() and (candidate / "BOOT" / "BOOTx64.efi").exists() and (candidate / "OC" / "OpenCore.efi").exists():
                efi_dir = candidate
                break
        if sample is None or efi_dir is None:
            raise RuntimeError("OpenCore release archive structure was not recognized; refusing to emit an incomplete EFI")

        stage = tmp / "stage"
        efi_root = stage / "EFI"
        _copy_tree(efi_dir, efi_root)
        oc_dir = efi_root / "OC"
        (oc_dir / "Kexts").mkdir(exist_ok=True)
        (oc_dir / "ACPI").mkdir(exist_ok=True)
        (oc_dir / "Resources").mkdir(exist_ok=True)
        log(f"Assembled OpenCore {oc_name} base files.")

        # The default set avoids VirtualSMC: these are genuine Macs, where AppleSMC
        # is native. Add optional kexts only when the user deliberately selects them.
        selected_repos = list(CORE_KEXT_REPOS)
        for optional_name in (optional_kexts or []):
            if optional_name not in OPTIONAL_KEXT_REPOS:
                raise ValueError(f"Unknown optional kext: {optional_name}")
            selected_repos.append(OPTIONAL_KEXT_REPOS[optional_name])
        for repo, expected, friendly in selected_repos:
            log(f"Looking up {friendly} release…")
            url, asset_name = latest_release_asset(repo, lambda n: n.upper().endswith(".ZIP") and "DEBUG" not in n.upper())
            archive = cache / f"{repo.replace('/', '_')}_{asset_name}"
            if not archive.exists() or archive.stat().st_size == 0:
                archive.unlink(missing_ok=True)
                download_file(url, archive, progress=log)
            extracted = tmp / (repo.replace('/', '_').replace('-', '_'))
            extracted.mkdir()
            _safe_extract(archive, extracted)
            found = _find_named(extracted, expected)
            if found is None:
                raise RuntimeError(f"Could not locate {expected} inside official release asset {asset_name}")
            dest = oc_dir / "Kexts" / expected
            _copy_tree(found, dest)
            log(f"Added {expected} ({asset_name}).")

        # Config from the same OpenCore version; kext entries refer only to copied bundles.
        config_path, warnings = _build_config(sample, oc_dir, model)
        errors = _validate_tree(stage)
        if errors:
            raise RuntimeError("EFI tree validation failed:\n" + "\n".join(errors))

        target.mkdir(parents=True, exist_ok=False)
        shutil.copytree(stage / "EFI", target / "EFI")
        with zipfile.ZipFile(target / "EFI.zip", "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted((target / "EFI").rglob("*")):
                if path.is_file(): archive.write(path, path.relative_to(target))
        (target / "BUILD-REPORT.md").write_text(
            "# ArchOCLP experimental EFI build\n\n"
            f"- Target Mac: `{model}`\n- OpenCore archive: `{oc_name}`\n- Generated: `{time.strftime('%Y-%m-%d %H:%M:%S %Z')}`\n"
            "- Build type: OpenCore binaries + selected upstream kexts + Sample.plist-derived configuration\n"
            f"- Optional kexts selected: {', '.join(optional_kexts or []) or 'none'}\n"
            "- EFI file structure checked by ArchOCLP's local path/plist validator\n"
            "- Export archive: `EFI.zip` (contains the EFI folder only)\n\n"
            "## Important limitations\n\n" + "\n".join(f"- {w}" for w in warnings) +
            "\n\nThis build has not been run through Apple's boot process or a complete matching `ocvalidate` on the target Mac.\n",
            encoding="utf-8")
        (target / "manifest.json").write_text(json.dumps({
            "tool": "ArchOCLP", "tool_version": APP_VERSION, "target_model": model,
            "opencore_archive": oc_name, "efi_root": str(target / "EFI"), "efi_zip": str(target / "EFI.zip"),
            "validation": {"local_file_and_plist_checks": "passed", "ocvalidate": "not run by this Linux build"},
            "optional_kexts": optional_kexts or [],
            "warnings": warnings,
        }, indent=2) + "\n", encoding="utf-8")
        log(f"EFI tree created: {target / 'EFI'}")
        log("Local path and plist checks passed. This is still an experimental base EFI, not a validated OCLP build.")
        return {"path": str(target), "efi": str(target / "EFI"), "report": str(target / "BUILD-REPORT.md"),
                "warnings": warnings, "errors": errors}


@dataclass
class InstallerItem:
    title: str
    version: str
    kind: str
    source: str
    source_page: str
    url: str
    trust: str
    size: str = ""
    identifier: str = ""


def _read_html(url: str, max_bytes: int = 8_000_000) -> str:
    with _request(url, timeout=30, headers={"Accept": "text/html,application/json,*/*"}) as response:
        return response.read(max_bytes).decode("utf-8", "replace")


class _AnchorParser:
    """Small HTML parser retaining anchor text plus its surrounding table row."""
    def __init__(self):
        from html.parser import HTMLParser
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links: list[tuple[str, str, str]] = []
                self.href: str | None = None
                self.anchor_text: list[str] = []
                self.in_row = False
                self.row_text: list[str] = []
                self.row_links: list[tuple[str, str]] = []
            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self.in_row = True
                    self.row_text = []
                    self.row_links = []
                elif tag == "a":
                    self.href = dict(attrs).get("href")
                    self.anchor_text = []
            def handle_data(self, data):
                if self.in_row:
                    self.row_text.append(data)
                if self.href is not None:
                    self.anchor_text.append(data)
            def handle_endtag(self, tag):
                if tag == "a" and self.href is not None:
                    text_value = " ".join(" ".join(self.anchor_text).split())
                    if self.in_row:
                        self.row_links.append((self.href, text_value))
                    else:
                        self.links.append((self.href, text_value, text_value))
                    self.href = None
                    self.anchor_text = []
                elif tag == "tr" and self.in_row:
                    context = " ".join(" ".join(self.row_text).split())
                    for href, label in self.row_links:
                        self.links.append((href, label, context))
                    self.in_row = False
                    self.row_text = []
                    self.row_links = []
        self.parser = Parser()


def _is_apple_download_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return (host == "apple.com" or host.endswith(".apple.com") or
            host == "cdn-apple.com" or host.endswith(".cdn-apple.com"))


def _direct_file_kind(url: str, label: str = "") -> str | None:
    path = urllib.parse.urlparse(url).path.lower()
    text = (path + " " + label.lower()).split("?")[0]
    if ".dmg" in text or "disk image" in text:
        return "DMG"
    if ".pkg" in text or "installassistant" in text:
        return "PKG"
    if ".iso" in text:
        return "ISO"
    if ".ipsw" in text:
        return "IPSW"
    return None


def discover_apple_installers(progress: Progress | None = None) -> list[InstallerItem]:
    """Scrape Apple's official download page and Mr. Macintosh's Apple-link index.

    Mr. Macintosh is used as an index only; direct InstallAssistant links on that page
    point to Apple's content delivery network. This function never uses their server
    to host installer bytes.
    """
    items: dict[str, InstallerItem] = {}
    pages = [(APPLE_INSTALLER_PAGE, "Apple Support", "Official Apple Support"),
             (MR_MACINTOSH_ARCHIVE, "Mr. Macintosh installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-golden-gate-full-installer-database-download-directly-from-apple/", "Golden Gate installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-tahoe-full-installer-database-download-directly-from-apple/", "Tahoe installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-sequoia-full-installer-database-download-directly-from-apple/", "Sequoia installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-sonoma-full-installer-database-download-directly-from-apple/", "Sonoma installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-ventura-13-full-installer-database-download-directly-from-apple/", "Ventura installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-12-monterey-full-installer-database-download-directly-from-apple/", "Monterey installer index", "Apple CDN link via installer index"),
             ("https://mrmacintosh.com/macos-big-sur-full-installer-database-download-directly-from-apple/", "Big Sur installer index", "Apple CDN link via installer index"),
             (MR_MACINTOSH_OLD, "Mr. Macintosh legacy installer index", "Legacy direct-link index")]
    for page_url, source, trust in pages:
        try:
            if progress: progress(f"Reading {source}…")
            source_html = _read_html(page_url)
            parser = _AnchorParser().parser
            parser.feed(source_html)
            for href, label, context in parser.links:
                url = urllib.parse.urljoin(page_url, html.unescape(href.strip()))
                kind = _direct_file_kind(url, label)
                if not kind: continue
                host = (urllib.parse.urlparse(url).hostname or "").lower()
                is_apple = _is_apple_download_host(host)
                # Ignore site navigation, and only offer Apple-hosted files here.
                if not is_apple: continue
                if not (url.lower().endswith((".dmg", ".pkg", ".ipsw", ".iso")) or "installassistant" in url.lower()): continue
                title = label or Path(urllib.parse.urlparse(url).path).name or kind
                # On installer index pages the actual link text is often only
                # "InstallAssistant.pkg"; preserve row context to distinguish builds.
                if title.lower() in {"installassistant.pkg", "download", "here"} and context:
                    title = context[:180]
                version_match = re.search(r"\b(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\b", context + " " + title)
                version = version_match.group(1) if version_match else ""
                item = InstallerItem(title=title, version=version, kind=kind, source=source, source_page=page_url,
                                     url=url, trust=trust)
                items[url] = item
        except Exception as exc:
            if progress: progress(f"Could not read {source}: {exc}")
    return sorted(items.values(), key=lambda x: (x.kind, x.version, x.title), reverse=True)


def search_archive_images(query: str, progress: Progress | None = None, rows: int = 25) -> list[InstallerItem]:
    """Search Internet Archive metadata for legacy macOS install images.

    Results are explicitly marked third-party/unverified; user should check rights,
    hashes and provenance before using them. Only .dmg/.iso/.img/.cdr are surfaced.
    """
    query = query.strip() or "Mac OS X installer"
    # Keep the query scoped to software and use the API instead of scraping search HTML.
    q = f'mediatype:software AND (title:"{query}" OR description:"{query}")'
    params = urllib.parse.urlencode({"q": q, "fl[]": ["identifier", "title", "year"], "rows": str(min(max(rows, 1), 50)), "page": "1", "output": "json"}, doseq=True)
    if progress: progress(f"Searching Internet Archive for {query!r}…")
    data = _get_json(f"{IA_SEARCH}?{params}")
    docs = data.get("response", {}).get("docs", [])
    result: list[InstallerItem] = []
    for idx, doc in enumerate(docs, 1):
        ident = str(doc.get("identifier", ""))
        if not ident: continue
        try:
            meta = _get_json("https://archive.org/metadata/" + urllib.parse.quote(ident, safe=""))
            files = meta.get("files", [])
            matches = [f for f in files if Path(str(f.get("name", ""))).suffix.lower() in {".dmg", ".iso", ".img", ".cdr"} and f.get("source") != "metadata"]
            matches.sort(key=lambda f: (0 if Path(str(f.get("name", ""))).suffix.lower() == ".dmg" else 1, str(f.get("name", ""))))
            for file in matches[:4]:
                name = str(file.get("name", ""))
                if not name: continue
                url = "https://archive.org/download/" + urllib.parse.quote(ident, safe="") + "/" + urllib.parse.quote(name, safe="")
                kind = Path(name).suffix.lower().lstrip(".").upper()
                bytes_n = file.get("size")
                size = f"{int(bytes_n)/1024**3:.2f} GiB" if str(bytes_n).isdigit() else ""
                result.append(InstallerItem(title=f"{doc.get('title', ident)} — {name}", version=str(doc.get("year", "")),
                    kind=kind, source="Internet Archive", source_page="https://archive.org/details/" + urllib.parse.quote(ident, safe=""),
                    url=url, trust="Third-party archive; verify provenance and hash", size=size, identifier=ident))
        except Exception as exc:
            if progress: progress(f"Skipped archive item {ident}: {exc}")
        if len(result) >= 30: break
        if progress and idx % 5 == 0: progress(f"Checked {idx}/{len(docs)} archive records…")
    return result


def open_core_release_info() -> dict[str, Any]:
    release = _get_json(f"{GITHUB_API}/repos/acidanthera/OpenCorePkg/releases/latest")
    asset = _asset_by_name(release, lambda n: "RELEASE" in n.upper() and n.lower().endswith(".zip"))
    if not asset:
        raise RuntimeError("No OpenCore RELEASE zip asset found")
    return {"version": release.get("tag_name", "unknown"), "name": asset.get("name"),
            "url": asset.get("browser_download_url"), "page": release.get("html_url")}
