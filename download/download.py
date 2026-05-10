"""Download and SHA-1-verify the latest Wikipedia multistream dump + index."""
import argparse
import hashlib
import os
import pathlib
import sys
import httpx
from tqdm import tqdm

# Define wikimedia constants
BASE_URL = "https://dumps.wikimedia.org"
DEFAULT_WIKI = "enwiki"
DUMPS_DIR = pathlib.Path("dumps")
TARGET_SUFFIXES = (
    "-pages-articles-multistream.xml.bz2",
    "-pages-articles-multistream-index.txt.bz2",
)
# Fallback for wikis (e.g. enwiki) that publish a monolithic dump without
# the multistream format.
FALLBACK_SUFFIXES = ("-pages-articles.xml.bz2",)

CHUNK_BYTES = 1 << 20


def fetch_sha1sums(wiki: str) -> dict[str, str]:
    """Fetch Wikimedia's sha1sums manifest for the target dump files.

    Prefers the multistream format (TARGET_SUFFIXES). Falls back to the
    monolithic articles dump (FALLBACK_SUFFIXES) for wikis such as enwiki
    that do not publish multistream files.

    Args:
        wiki: The wiki identifier (e.g. ``simplewiki``, ``enwiki``).

    Returns:
        A dict mapping filename to its expected hex SHA-1 digest.

    Raises:
        RuntimeError: If neither multistream nor fallback files are present.
        httpx.HTTPStatusError: If the manifest URL returns a non-2xx response.
    """
    url = f"{BASE_URL}/{wiki}/latest/{wiki}-latest-sha1sums.txt"
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()

    all_targets = TARGET_SUFFIXES + FALLBACK_SUFFIXES
    manifest: dict[str, str] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Each manifest line is: <sha1>  <filename>  (two-space separator)
        sha1, _, filename = line.partition("  ")
        if not filename:
            continue
        if filename.startswith(wiki) and filename.endswith(all_targets):
            manifest[filename] = sha1

    multistream = {f: s for f, s in manifest.items() if f.endswith(TARGET_SUFFIXES)}
    fallback = {f: s for f, s in manifest.items() if f.endswith(FALLBACK_SUFFIXES)}

    if multistream:
        matched = {s for f in multistream for s in TARGET_SUFFIXES if f.endswith(s)}
        missing = set(TARGET_SUFFIXES) - matched
        if missing:
            raise RuntimeError(
                f"sha1sums manifest is missing target file(s): {sorted(missing)}"
            )
        return multistream

    if fallback:
        return fallback

    raise RuntimeError(
        f"sha1sums manifest contains no article dump files for {wiki!r}"
    )


def _hash_file(path: pathlib.Path) -> str:
    """Compute the SHA-1 digest of a file, reading it in chunks to avoid loading it all into memory.

    Args:
        path: Path to the file to hash.

    Returns:
        The lowercase hex SHA-1 digest string.
    """
    h = hashlib.sha1()
    with path.open("rb") as f:
        # iter with a sentinel stops when read() returns b"" (EOF)
        for block in iter(lambda: f.read(CHUNK_BYTES), b""):
            h.update(block)
    return h.hexdigest()


def verify_existing(path: pathlib.Path, expected_sha1: str) -> bool:
    """Check whether a file already exists and matches the expected SHA-1.

    Args:
        path: Path to the file to verify.
        expected_sha1: The hex SHA-1 digest the file should have.

    Returns:
        ``True`` if the file exists and its digest matches, ``False`` if it is
        missing or the digest does not match.
    """
    if not path.exists():
        return False
    print(f"checking existing {path.name} ...", flush=True)
    return _hash_file(path) == expected_sha1


def download_with_verify(url: str, dest: pathlib.Path, expected_sha1: str) -> None:
    """Stream a file from a URL to disk, verifying its SHA-1 on completion.

    Downloads to a ``.tmp`` file first so ``dest`` is never left in a partial
    state if the transfer is interrupted or the hash check fails.

    Args:
        url: The URL to download from.
        dest: The final destination path for the downloaded file.
        expected_sha1: The hex SHA-1 digest the downloaded file must match.

    Raises:
        RuntimeError: If the SHA-1 of the downloaded file does not match
            ``expected_sha1``. The temporary file is deleted before raising.
        httpx.HTTPStatusError: If the server returns a non-2xx response.
    """
    # Write to a temp path so a failed/interrupted download never leaves a corrupt dest
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    h = hashlib.sha1()

    with httpx.stream("GET", url, timeout=None, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0)) or None
        with tmp.open("wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=dest.name
        ) as bar:
            for chunk in resp.iter_bytes(chunk_size=CHUNK_BYTES):
                f.write(chunk)
                h.update(chunk)
                bar.update(len(chunk))

    actual = h.hexdigest()
    if actual != expected_sha1:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(
            f"SHA-1 mismatch for {dest.name}: expected {expected_sha1}, got {actual}"
        )

    # Atomic rename so dest is never visible in a partial state
    os.replace(tmp, dest)


def main(argv: list[str] | None = None) -> int:
    """Download and SHA-1-verify the target dump files for a given wiki.

    Args:
        argv: Argument list to parse. Defaults to ``sys.argv[1:]`` when ``None``.

    Returns:
        ``0`` if all files were downloaded or already verified, ``1`` if any
        file failed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", default=DEFAULT_WIKI, help="wiki name, e.g. simplewiki, enwiki")
    args = parser.parse_args(argv)

    DUMPS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = fetch_sha1sums(args.wiki)
    summary: list[tuple[str, int, str, str]] = []
    failed = False

    for filename, sha1 in manifest.items():
        dest = DUMPS_DIR / filename
        # Filename is {wiki}-{date}-...; extract date to build the correct directory URL
        date = filename[len(args.wiki) + 1:].split("-")[0]
        url = f"{BASE_URL}/{args.wiki}/{date}/{filename}"
        try:
            if verify_existing(dest, sha1):
                status = "skipped (already verified)"
            else:
                download_with_verify(url, dest, sha1)
                status = "ok"
        except Exception as e:
            status = f"FAILED: {e}"
            failed = True

        size = dest.stat().st_size if dest.exists() else 0
        summary.append((filename, size, sha1, status))

    print("\n=== summary ===")
    for filename, size, sha1, status in summary:
        print(f"{filename}\n  size={size:,} bytes\n  sha1={sha1}\n  status={status}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
