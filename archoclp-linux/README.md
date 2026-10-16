# ArchOCLP — Arch Linux OpenCore / macOS installer toolkit

**Version:** 0.2.0 · **Runtime:** Python 3.10+ · **Dependencies:** Python standard library; GUI requires Tk (`sudo pacman -S tk`).

ArchOCLP detects Apple Mac identifiers from Linux DMI/SMBIOS data, assembles an experimental EFI folder from actual upstream OpenCore and kext release assets, and discovers downloadable macOS installers from Apple-hosted pages. For older disk images no longer easy to find on Apple, it can search Internet Archive metadata and download selected images with a SHA-256 hash recorded after transfer.

## Quick start

```bash
sudo pacman -S --needed python tk
./archoclp-gui
```

Or run the CLI:

```bash
./archoclp detect
./archoclp build-efi --model MacBookPro9,2 --output ~/archoclp-builds
# Optional only when your hardware/config calls for it:
./archoclp build-efi --model MacBookPro9,2 --optional-kext NVMeFix
./archoclp downloads
./archoclp downloads --archive "Mac OS X Snow Leopard"
./archoclp validate-efi --path ~/archoclp-builds/MacBookPro9,2
./archoclp download 'https://example.org/some-installer.dmg' --output ~/Downloads/some-installer.dmg
```

For a macOS, the GUI auto-detects the model identifier. A different target can be selected in the EFI Builder tab. OpenCore and kext release files are downloaded from their upstream GitHub release assets, cached under `~/.cache/archoclp/downloads`, and assembled into a conventional `EFI/BOOT` + `EFI/OC` tree with a version-matched OpenCore `Sample.plist` as the starting point.

## EFI builder status — important

The application now assembles **real OpenCore boot files, release kext bundles and a config.plist-derived EFI tree**. It performs local plist/file-path checks and writes `BUILD-REPORT.md`, `manifest.json`, and an `EFI.zip` export containing the EFI folder.

This is an **experimental base EFI assembly, not yet a full Linux port or replacement for OpenCore Legacy Patcher**. Specifically, this release does not yet implement OCLP's complete per-model ACPI tables, GPU compatibility/root patch sets, legacy Wi-Fi patches, NVRAM/firmware probing, SMBIOS serial generation, `ocvalidate` execution on Linux, post-install root patching, or a verified per-Mac quirks database. It deliberately does not invent Apple serial numbers or MLB values; the generated config has blank serial/MLB fields and zero ROM/SystemUUID placeholders that must be set correctly before use. It does not write to EFI partitions. A passing local file check does not mean the Mac will boot. Review and customise the config, use trusted model-specific settings, and test via removable media while retaining a known-good boot path.

This current EFI assembly should not be used to overwrite the internal EFI or be represented as a known-good OCLP configuration. The next engineering milestone for genuine OCLP parity is porting upstream `BuildOpenCore`, its hardware datasets and PatcherSupportPkg asset selection so the exact per-model OCLP logic drives the build.

## macOS installer sources

- **Apple Support** (`https://support.apple.com/en-gb/102662`) is the first-party source of legacy DMGs and current download guidance.
- **Mr. Macintosh** (`https://mrmacintosh.com/category/macos-installer/`) is used as an index for direct links that point to Apple's CDN; page links and version availability may change.
- **Internet Archive** search is an optional fallback for older DMG/ISO/IMG/CDR disk images. Such results are marked third-party and unverified. The app does not claim authenticity or legal entitlement; check provenance, hashes, licence terms and hardware compatibility. Downloads are saved as files only; no image is automatically mounted, executed or flashed to a USB drive.

The downloader requires HTTPS, streams to a temporary `.part` file, checks the advertised byte count where available, computes SHA-256, and saves a `.sha256` sidecar after a successful transfer. An expected SHA-256 can be supplied through the CLI. A locally computed checksum detects later file changes but does not, by itself, prove the source was authentic.

## Commands

- `detect` — detect model / host information.
- `models` — list selected sample model profiles.
- `build` — create a planning report only, retained for diagnostics.
- `build-efi` — download real upstream binaries and assemble an experimental EFI tree. Optional flags: `--optional-kext AppleALC`, `RestrictEvents`, or `NVMeFix` (repeat flag as required).
- `downloads` — find current Apple-hosted installer links.
- `downloads --archive QUERY` — search Internet Archive metadata for disk images.
- `download URL --output PATH [--sha256 HASH]` — download a selected HTTPS file. Existing destination files are not overwritten.
- `validate-efi --path PATH` — check required EFI files and config paths (not full `ocvalidate`).
- `gui` — start Tk desktop interface.

## Safety

- No command formats disks, partitions drives, edits firmware/NVRAM, blesses an EFI folder, or automatically flashes a USB drive.
- The app does not host Apple's copyrighted installers; it downloads from source links selected by the user.
- Third-party archive items require an explicit confirmation in the GUI.
- Don't assume that an unsupported macOS version will work on a given model just because an installer downloads successfully.

## Tests

```bash
python -m unittest discover -s tests -v
```

## References

- OCLP source: https://github.com/dortania/OpenCore-Legacy-Patcher
- OCLP supported model guide: https://dortania.github.io/OpenCore-Legacy-Patcher/MODELS.html
- OCLP EFI build guide: https://dortania.github.io/OpenCore-Legacy-Patcher/BUILD.html
- OpenCorePkg releases: https://github.com/acidanthera/OpenCorePkg/releases
- Apple macOS downloads: https://support.apple.com/en-gb/102662
- Installer database / Apple CDN index: https://mrmacintosh.com/category/macos-installer/

## Licences

ArchOCLP prototype code: MIT. Upstream OpenCore, kexts and any macOS installers retain their original licences and ownership. ArchOCLP is independent and is not affiliated with or endorsed by Dortania, Acidanthera, Apple, Mr. Macintosh or the Internet Archive.
