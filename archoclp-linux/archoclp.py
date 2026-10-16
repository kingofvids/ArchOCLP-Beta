#!/usr/bin/env python3
"""ArchOCLP: Linux-side Mac detection and per-model OpenCore build planning.

This prototype deliberately does NOT write a bootable EFI. Correct OCLP EFI
creation requires the upstream payloads and platform/model-specific builder.
It emits a validated, machine-specific build workspace instead of pretending
that a generic config.plist is safe to boot.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_NAME = "ArchOCLP"
VERSION = "0.2.0"
MODEL_RE = re.compile(r"^(?:MacBookPro|MacBookAir|MacBook|Macmini|iMac|MacPro|Xserve|iMacPro|MacStudio|Mac14|Mac15|Mac16)[A-Za-z0-9]*\d+,\d+$")
APPLE_VENDOR_TOKENS = ("apple inc.", "apple computer", "apple")

DEFAULT_MODELS: dict[str, dict[str, Any]] = {
    "MacBookPro5,1": {"name": "MacBook Pro (15-inch, Late 2008)", "year": 2008, "generation": "Penryn", "gpu": "NVIDIA GeForce 9400M / 9600M GT depending on configuration", "notes": ["Non-Metal GPU; newer macOS versions require graphics patching."]},
    "MacBookPro6,2": {"name": "MacBook Pro (15-inch, Mid 2010)", "year": 2010, "generation": "Arrandale", "gpu": "Intel HD Graphics + NVIDIA GeForce GT 330M", "notes": ["Dual-GPU models can have known GPU reliability concerns.", "Non-Metal graphics path for newer macOS releases."]},
    "MacBookPro8,1": {"name": "MacBook Pro (13-inch, Early/Late 2011)", "year": 2011, "generation": "Sandy Bridge", "gpu": "Intel HD Graphics 3000", "notes": ["Non-Metal iGPU; newer macOS releases need graphics patches."]},
    "MacBookPro8,2": {"name": "MacBook Pro (15-inch, Early/Late 2011)", "year": 2011, "generation": "Sandy Bridge", "gpu": "Intel HD Graphics 3000 + AMD Radeon dGPU", "notes": ["Dual-GPU models can have known GPU reliability concerns.", "Non-Metal iGPU; graphics support needs careful OS-specific selection."]},
    "MacBookPro8,3": {"name": "MacBook Pro (17-inch, Early/Late 2011)", "year": 2011, "generation": "Sandy Bridge", "gpu": "Intel HD Graphics 3000 + AMD Radeon dGPU", "notes": ["Dual-GPU models can have known GPU reliability concerns.", "Non-Metal iGPU; graphics support needs careful OS-specific selection."]},
    "MacBookPro9,1": {"name": "MacBook Pro (15-inch, Mid 2012)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000 + NVIDIA GeForce GT 650M", "notes": ["Legacy Metal generation; GPU-specific root patches depend on target macOS."]},
    "MacBookPro9,2": {"name": "MacBook Pro (13-inch, Mid 2012, non-Retina)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000", "notes": ["Legacy Metal generation; GPU-specific root patches depend on target macOS.", "This model is listed by upstream OpenCore Legacy Patcher."]},
    "MacBookPro10,1": {"name": "MacBook Pro (15-inch Retina, 2012/2013)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000 + NVIDIA GeForce GT 650M/750M depending on revision", "notes": ["Retina and dual-GPU details must be probed before final EFI creation."]},
    "MacBookPro10,2": {"name": "MacBook Pro (13-inch Retina, 2012/2013)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000", "notes": ["Legacy Metal generation; GPU-specific root patches depend on target macOS."]},
    "MacBookPro11,1": {"name": "MacBook Pro (13-inch Retina, Late 2013/Mid 2014)", "year": 2013, "generation": "Haswell", "gpu": "Intel Iris / Iris Pro family", "notes": ["Final configuration depends on exact GPU and target macOS."]},
    "MacBookPro11,2": {"name": "MacBook Pro (15-inch Retina, Late 2013/Mid 2014, integrated graphics)", "year": 2013, "generation": "Haswell", "gpu": "Intel Iris Pro", "notes": ["Final configuration depends on target macOS."]},
    "MacBookPro11,3": {"name": "MacBook Pro (15-inch Retina, Late 2013/Mid 2014, dual graphics)", "year": 2013, "generation": "Haswell", "gpu": "Intel Iris Pro + NVIDIA GeForce GT 750M", "notes": ["Dual-GPU settings need hardware-specific verification."]},
    "MacBookAir5,1": {"name": "MacBook Air (11-inch, Mid 2012)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000", "notes": ["Legacy Metal generation; GPU-specific root patches depend on target macOS."]},
    "MacBookAir5,2": {"name": "MacBook Air (13-inch, Mid 2012)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000", "notes": ["Legacy Metal generation; GPU-specific root patches depend on target macOS."]},
    "Macmini5,1": {"name": "Mac mini (Mid 2011, base)", "year": 2011, "generation": "Sandy Bridge", "gpu": "Intel HD Graphics 3000", "notes": ["Non-Metal graphics path for newer macOS releases."]},
    "Macmini6,1": {"name": "Mac mini (Late 2012, base)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000", "notes": ["Legacy Metal generation; GPU-specific root patches depend on target macOS."]},
    "iMac12,1": {"name": "iMac (21.5-inch, Mid 2011)", "year": 2011, "generation": "Sandy Bridge", "gpu": "Intel HD Graphics 3000 / AMD Radeon depending on SKU", "notes": ["Exact GPU configuration must be detected before EFI creation."]},
    "iMac13,1": {"name": "iMac (21.5-inch, Late 2012)", "year": 2012, "generation": "Ivy Bridge", "gpu": "Intel HD Graphics 4000 + NVIDIA GT 640M/650M depending on SKU", "notes": ["Exact GPU configuration must be detected before EFI creation."]},
    "MacPro5,1": {"name": "Mac Pro (Mid 2010/Mid 2012)", "year": 2010, "generation": "Nehalem/Westmere", "gpu": "Upgradeable PCIe GPU", "notes": ["GPU, Wi-Fi, storage and PCIe hardware vary widely; inventory is essential."]},
}


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except (OSError, PermissionError):
        return ""


def _normalise_model(*candidates: str) -> str:
    for value in candidates:
        value = value.strip().replace("\x00", "")
        # DMI product_name is normally the model identifier, but be forgiving.
        match = re.search(r"\b((?:MacBookPro|MacBookAir|MacBook|Macmini|iMac|MacPro|Xserve|iMacPro)[A-Za-z0-9]*\d+,\d+)\b", value)
        if match:
            return match.group(1)
    return ""


def is_apple_silicon_model(model: str) -> bool:
    """Best-effort model-ID check for common Apple Silicon families."""
    silicon_prefixes = (
        "MacBookPro17,", "MacBookPro18,", "MacBookPro19,", "MacBookPro20,",
        "MacBookAir10,", "MacBookAir11,", "MacBookAir15,", "MacBookAir16,",
        "Macmini9,", "iMac21,", "MacStudio13,", "MacStudio14,", "MacPro14,8",
        "Mac14,", "Mac15,", "Mac16,", "Mac17,", "Mac18,",
    )
    return model.startswith(silicon_prefixes)


def detect_hardware(sysfs_root: Path = Path("/sys/class/dmi/id"), allow_dmidecode: bool = True) -> dict[str, Any]:
    """Read DMI first; optionally fall back to dmidecode on Linux."""
    fields = {
        "sys_vendor": read_text(sysfs_root / "sys_vendor"),
        "product_name": read_text(sysfs_root / "product_name"),
        "product_version": read_text(sysfs_root / "product_version"),
        "board_name": read_text(sysfs_root / "board_name"),
        "bios_version": read_text(sysfs_root / "bios_version"),
    }
    model = _normalise_model(fields["product_name"], fields["product_version"], fields["board_name"])
    source = "sysfs DMI"
    if not model and allow_dmidecode and shutil.which("dmidecode"):
        try:
            result = subprocess.run(["dmidecode", "-s", "system-product-name"], check=False,
                                    capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                model = _normalise_model(result.stdout)
                if model:
                    fields["product_name"] = result.stdout.strip()
                    source = "dmidecode"
        except (OSError, subprocess.SubprocessError):
            pass
    vendor = fields["sys_vendor"].lower()
    is_apple = any(token in vendor for token in APPLE_VENDOR_TOKENS) or bool(model)
    is_intel_mac = bool(model and not is_apple_silicon_model(model))
    if not model:
        status = "apple-hardware-model-unidentified" if is_apple else "not-detected-as-mac"
    elif not is_apple:
        status = "model-id-found-vendor-unconfirmed"
    else:
        status = "mac-detected"
    return {
        **fields,
        "model_identifier": model or None,
        "is_apple_hardware": is_apple,
        "looks_like_intel_era_mac": is_intel_mac if model else None,
        "is_apple_silicon_model": is_apple_silicon_model(model) if model else None,
        "detection_source": source,
        "detection_status": status,
        "host_os": platform.system(),
        "host_architecture": platform.machine(),
        "detected_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def load_models(path: Path | None = None) -> dict[str, dict[str, Any]]:
    if path is None:
        path = Path(__file__).with_name("models.json")
    if path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("models.json must contain a JSON object keyed by model identifier")
        return data
    return DEFAULT_MODELS


def model_profile(model: str, models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    known = models.get(model)
    if known:
        profile = dict(known)
        profile.update({"model_identifier": model, "profile_status": "known-model", "upstream_oclp_model_list": "Listed upstream; verify current notes and OS-version compatibility." if model == "MacBookPro9,2" else "Model hint only; verify the current upstream supported-model list before use."})
    else:
        profile = {
            "model_identifier": model,
            "name": "Unmapped Apple model",
            "year": None,
            "generation": "unknown",
            "gpu": "Must be probed; do not guess",
            "notes": ["Model identifier was detected, but this prototype has no reviewed hardware profile for it."],
            "profile_status": "unmapped-model",
            "upstream_oclp_model_list": "Check upstream model list before proceeding",
        }
    profile["target_architecture"] = "x86_64/Intel Mac assumed by OpenCore Legacy Patcher model database"
    profile["needs_live_hardware_probe"] = True
    profile["build_status"] = "plan-only-no-bootable-efi-generated"
    return profile


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_workspace(model: str, output: Path, detection: dict[str, Any] | None = None,
                    models: dict[str, dict[str, Any]] | None = None) -> Path:
    models = models or load_models()
    profile = model_profile(model, models)
    target = output / model
    target.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tool": APP_NAME,
        "tool_version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_model": model,
        "host_detection": detection,
        "model_profile": profile,
        "build_plan": {
            "phase": "hardware identification and build planning",
            "efi_generated": False,
            "usb_written": False,
            "firmware_modified": False,
            "next_steps": [
                "Confirm the model is listed in the official OCLP supported-models document.",
                "Probe the actual GPU, Wi-Fi/Bluetooth chipset, storage and firmware revision on the target Mac.",
                "Port and run the upstream OCLP EFI builder and payload handling on Linux.",
                "Validate the finished config.plist with the matching OpenCore ocvalidate binary before attempting to boot.",
            ],
        },
        "sources": {
            "oclp_repository": "https://github.com/dortania/OpenCore-Legacy-Patcher",
            "oclp_supported_models": "https://dortania.github.io/OpenCore-Legacy-Patcher/MODELS.html",
            "oclp_build_guide": "https://dortania.github.io/OpenCore-Legacy-Patcher/BUILD.html",
        },
    }
    _write(target / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    notes = "\n".join(f"- {item}" for item in profile.get("notes", [])) or "- No reviewed notes are available."
    report = f"""# ArchOCLP build workspace: {model}

**Status: planning workspace only — not a bootable EFI.**

## Target

- **Model identifier:** `{model}`
- **Model:** {profile.get('name', 'Unknown')}
- **Year:** {profile.get('year') or 'Unknown'}
- **CPU generation:** {profile.get('generation', 'Unknown')}
- **GPU hint:** {profile.get('gpu', 'Unknown')}
- **Profile status:** `{profile.get('profile_status')}`

## Model notes

{notes}

## Safety status

This program did **not** write to an EFI partition, alter NVRAM, install OpenCore, download macOS, or create a `config.plist`. This is deliberate: a model-specific OpenCore EFI needs the full upstream OCLP build logic, its version-matched assets, and a hardware probe. A generic config can prevent macOS from booting.

## Next steps to a real EFI

1. Verify support against the official OCLP model list linked in `manifest.json`.
2. Collect a target hardware report, including exact GPU and Wi-Fi/Bluetooth chipset.
3. Use a Linux-compatible port of upstream OCLP's EFI builder and payload downloader.
4. Validate the generated EFI with the matching `ocvalidate`, then test from a removable USB before installing to internal storage.

See `manifest.json` for machine-readable details.
"""
    _write(target / "REPORT.md", report)
    return target


def cmd_detect(args: argparse.Namespace) -> int:
    result = detect_hardware(Path(args.sysfs_root), allow_dmidecode=not args.no_dmidecode)
    print(json.dumps(result, indent=2))
    if result["detection_status"] == "mac-detected":
        print(f"\nDetected Mac model: {result['model_identifier']}")
        return 0
    print("\nNo fully identified Apple Mac model was found.", file=sys.stderr)
    return 2


def cmd_models(args: argparse.Namespace) -> int:
    models = load_models(Path(args.models_file) if args.models_file else None)
    for identifier in sorted(models):
        item = models[identifier]
        print(f"{identifier:16} {item.get('name', 'Unknown model')}")
    print(f"\n{len(models)} bundled model profiles. This list is not the complete upstream OCLP support list.")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    detection = detect_hardware(Path(args.sysfs_root), allow_dmidecode=not args.no_dmidecode)
    model = args.model or detection.get("model_identifier")
    if not model:
        print("Could not determine a Mac model. Run `archoclp detect` or pass --model MacBookPro9,2.", file=sys.stderr)
        return 2
    if not MODEL_RE.match(model):
        print(f"'{model}' does not look like an Apple model identifier (for example MacBookPro9,2).", file=sys.stderr)
        return 2
    if not args.model and detection.get("detection_status") != "mac-detected":
        print("Model auto-detection was not confirmed as Apple hardware; refusing automatic build. Pass --model to cross-build intentionally.", file=sys.stderr)
        return 2
    models = load_models(Path(args.models_file) if args.models_file else None)
    workspace = build_workspace(model, Path(args.output).expanduser().resolve(), detection, models)
    print(f"Created model-specific planning workspace: {workspace}")
    print(f"Report: {workspace / 'REPORT.md'}")
    print(f"Manifest: {workspace / 'manifest.json'}")
    print("NOTE: This prototype does not generate a bootable EFI yet.")
    return 0


def cmd_build_efi(args: argparse.Namespace) -> int:
    from archoclp_features import build_efi
    detection = detect_hardware()
    model = args.model or detection.get("model_identifier")
    if not model:
        print("Could not detect a Mac model. Pass --model MacBookPro9,2 to build for a known target.", file=sys.stderr)
        return 2
    result = build_efi(model, Path(args.output), progress=print, optional_kexts=args.optional_kext)
    print(f"EFI tree: {result['efi']}")
    print(f"Build report: {result['report']}")
    print("WARNING: experimental base EFI only; review BUILD-REPORT.md before use.")
    return 0


def cmd_downloads(args: argparse.Namespace) -> int:
    from archoclp_features import discover_apple_installers, search_archive_images
    if args.archive:
        items = search_archive_images(args.archive, progress=print, rows=args.rows)
    else:
        items = discover_apple_installers(progress=print)
    payload = [item.__dict__ for item in items]
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for i, item in enumerate(items, 1):
            print(f"{i:2}. [{item.kind:4}] {item.version:8} {item.title}\n    {item.url}\n    Source: {item.source} | {item.trust}")
        print(f"\n{len(items)} download candidate(s). Third-party archives must be checked for provenance, rights and integrity.")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    from archoclp_features import download_file
    result = download_file(args.url, Path(args.output), progress=print, expected_sha256=args.sha256)
    print(json.dumps(result, indent=2))
    return 0


def cmd_validate_efi(args: argparse.Namespace) -> int:
    from archoclp_features import validate_efi_path
    problems = validate_efi_path(Path(args.path))
    if problems:
        print("Local EFI consistency check: FAILED")
        for problem in problems: print(f"- {problem}")
        return 1
    print("Local EFI file/path consistency check: PASSED")
    print("This does not run the matching ocvalidate tool and does not prove bootability.")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        print("Tk GUI is unavailable. On Arch Linux install it with: sudo pacman -S tk", file=sys.stderr)
        raise exc
    from archoclp_gui import main
    main()
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archoclp", description="ArchOCLP: Linux-native Mac detector, experimental EFI assembler and macOS installer downloader")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="detect a Mac from Linux DMI/SMBIOS data")
    detect.add_argument("--sysfs-root", default="/sys/class/dmi/id", help=argparse.SUPPRESS)
    detect.add_argument("--no-dmidecode", action="store_true", help="do not try dmidecode as a fallback")
    detect.set_defaults(func=cmd_detect)

    models = sub.add_parser("models", help="list bundled model profiles")
    models.add_argument("--models-file", help="use an alternative models.json")
    models.set_defaults(func=cmd_models)

    build = sub.add_parser("build", help="create a model-specific planning workspace (not a bootable EFI)")
    build.add_argument("--model", help="target model identifier; e.g. MacBookPro9,2. Omit for auto-detection on a Mac.")
    build.add_argument("--output", default="./archoclp-build", help="output directory (default: ./archoclp-build)")
    build.add_argument("--sysfs-root", default="/sys/class/dmi/id", help=argparse.SUPPRESS)
    build.add_argument("--no-dmidecode", action="store_true", help="do not try dmidecode as a fallback")
    build.add_argument("--models-file", help="use an alternative models.json")
    build.set_defaults(func=cmd_build)

    efi = sub.add_parser("build-efi", help="download upstream OpenCore/kext assets and assemble an experimental EFI tree")
    efi.add_argument("--model", help="target Mac model identifier; defaults to DMI detection when available")
    efi.add_argument("--output", default="~/archoclp-builds", help="output directory (default: ~/archoclp-builds)")
    efi.add_argument("--optional-kext", action="append", choices=["AppleALC", "RestrictEvents", "NVMeFix"], default=[], help="include a model-dependent optional kext; may be repeated")
    efi.set_defaults(func=cmd_build_efi)

    downloads = sub.add_parser("downloads", help="discover Apple-hosted macOS installers or archived images")
    downloads.add_argument("--archive", help="search Internet Archive for legacy DMGs/ISOs instead of official installer indexes")
    downloads.add_argument("--rows", type=int, default=25, help="number of archive index records to inspect (1-50)")
    downloads.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    downloads.set_defaults(func=cmd_downloads)

    download = sub.add_parser("download", help="download a direct HTTPS installer URL")
    download.add_argument("url", help="direct HTTPS download URL")
    download.add_argument("--output", required=True, help="destination filename")
    download.add_argument("--sha256", help="optional expected SHA-256 hash")
    download.set_defaults(func=cmd_download)

    validate = sub.add_parser("validate-efi", help="check required EFI files and config.plist paths (not full OpenCore schema)")
    validate.add_argument("--path", required=True, help="EFI folder itself or build folder containing EFI/")
    validate.set_defaults(func=cmd_validate_efi)

    gui = sub.add_parser("gui", help="launch the Tk desktop interface")
    gui.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
