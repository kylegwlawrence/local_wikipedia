"""Download and extract KaTeX to static/katex/ for offline math rendering.

Run once after cloning the repo:
    python download/download_katex.py

Downloads the KaTeX release tarball from GitHub and extracts only the files
needed by the web app (CSS, JS, fonts) into static/katex/. After this runs
the app requires no internet connection to render mathematical formulas.
"""
import io
import pathlib
import sys
import tarfile

import httpx

KATEX_VERSION = "0.16.11"
TARBALL_URL = (
    f"https://github.com/KaTeX/KaTeX/releases/download/"
    f"v{KATEX_VERSION}/katex.tar.gz"
)

BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
DEST_DIR = BASE_DIR / "static" / "katex"

# Only these paths (relative to the katex/ dir inside the tarball) are needed.
WANTED_PREFIXES = (
    "katex/katex.min.css",
    "katex/katex.min.js",
    "katex/contrib/auto-render.min.js",
    "katex/fonts/",
)


def download_katex() -> int:
    if DEST_DIR.exists() and any(DEST_DIR.iterdir()):
        print(f"KaTeX already present at {DEST_DIR} — skipping download.")
        print("Delete the directory and re-run to force a fresh download.")
        return 0

    print(f"Downloading KaTeX v{KATEX_VERSION} ...", flush=True)
    try:
        response = httpx.get(TARBALL_URL, follow_redirects=True, timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        print(f"ERROR: download failed: {e}", file=sys.stderr)
        return 1

    print("Extracting ...", flush=True)
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    data = io.BytesIO(response.content)
    with tarfile.open(fileobj=data, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not any(member.name.startswith(p) for p in WANTED_PREFIXES):
                continue
            # Strip the leading "katex/" prefix so files land directly in DEST_DIR
            rel = pathlib.PurePosixPath(member.name).relative_to("katex")
            dest = DEST_DIR / rel
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                dest.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(member)
                if f:
                    dest.write_bytes(f.read())

    print(f"KaTeX installed to {DEST_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(download_katex())
