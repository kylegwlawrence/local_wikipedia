"""Download and extract KaTeX to static/katex/ for offline math rendering.

Run once after cloning the repo:
    python download/download_katex.py

Downloads the KaTeX release tarball from GitHub and extracts only the files
needed by the web app (CSS, JS, fonts) into static/katex/. After this runs
the app requires no internet connection to render mathematical formulas.
"""

import argparse
import io
import os
import pathlib
import shutil
import sys
import tarfile

import httpx

from paths import BASE_DIR

KATEX_VERSION = "0.16.11"
TARBALL_URL = f"https://github.com/KaTeX/KaTeX/releases/download/v{KATEX_VERSION}/katex.tar.gz"

DEST_DIR = BASE_DIR / "static" / "katex"

# Only these paths (relative to the katex/ dir inside the tarball) are needed.
WANTED_PREFIXES = (
    "katex/katex.min.css",
    "katex/katex.min.js",
    "katex/contrib/auto-render.min.js",
    # mhchem extension — required for \ce{} chemistry notation (<chem>/<ce> tags).
    "katex/contrib/mhchem.min.js",
    "katex/fonts/",
)


def _extract_to(tar: tarfile.TarFile, target: pathlib.Path) -> None:
    """Write the wanted katex files from ``tar`` into ``target`` directory."""
    for member in tar.getmembers():
        if not any(member.name.startswith(p) for p in WANTED_PREFIXES):
            continue
        # Strip the leading "katex/" prefix so files land directly in target
        rel = pathlib.PurePosixPath(member.name).relative_to("katex")
        dest = target / rel
        if member.isdir():
            dest.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            dest.parent.mkdir(parents=True, exist_ok=True)
            f = tar.extractfile(member)
            if f:
                dest.write_bytes(f.read())


def download_katex(force: bool = False) -> int:
    if DEST_DIR.exists() and any(DEST_DIR.iterdir()) and not force:
        print(f"KaTeX already present at {DEST_DIR} — skipping download.")
        print("Re-run with --force to refresh.")
        return 0

    print(f"Downloading KaTeX v{KATEX_VERSION} ...", flush=True)
    try:
        response = httpx.get(TARBALL_URL, follow_redirects=True, timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        print(f"ERROR: download failed: {e}", file=sys.stderr)
        return 1

    print("Extracting ...", flush=True)
    DEST_DIR.parent.mkdir(parents=True, exist_ok=True)
    # Extract into a sibling temp dir, then atomically swap into place so a
    # crash mid-extraction never leaves a half-installed DEST_DIR behind.
    staging = DEST_DIR.with_name(DEST_DIR.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
            _extract_to(tar, staging)
        if DEST_DIR.exists():
            shutil.rmtree(DEST_DIR)
        os.replace(staging, DEST_DIR)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(f"KaTeX installed to {DEST_DIR}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if static/katex/ is already populated",
    )
    args = parser.parse_args(argv)
    return download_katex(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
