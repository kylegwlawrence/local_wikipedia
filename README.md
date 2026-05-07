# Wikipedia Dump Downloader

A Python tool for downloading and verifying Wikipedia dump files from Wikimedia with built-in SHA-1 verification.

## Features

- Downloads the latest multistream Wikipedia dumps (both article XML and index files)
- Automatic SHA-1 verification against Wikimedia's official checksums
- Progress bar display for download tracking
- Smart resume: skips files that already exist with correct checksums
- Atomic writes to prevent corrupt files on interruption
- Memory-efficient streaming for multi-gigabyte files

## Installation

1. Clone this repository
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Download Wikipedia dumps for Simple English Wikipedia (default):
```bash
python download/download.py
```

Download dumps for a specific wiki:
```bash
python download/download.py --wiki enwiki
```

Downloaded files are saved to the `dumps/` directory.

## How It Works

1. Fetches the official SHA-1 checksum manifest from Wikimedia
2. Checks if target files already exist with correct checksums (skip if valid)
3. Downloads missing or invalid files with progress indication
4. Verifies downloaded files against checksums
5. Uses atomic file operations (temp files + rename) to prevent corruption

## Testing

Run all tests:
```bash
pytest download/test_download.py
```

Run a specific test class:
```bash
pytest download/test_download.py::TestDownloadWithVerify
```

Run with verbose output:
```bash
pytest download/test_download.py -v
```

## Dependencies

- **httpx** (0.28.1) - Async HTTP client for downloads
- **tqdm** (4.67.3) - Terminal progress bars
- **pytest** (9.0.3) - Testing framework
- **respx** (0.23.1) - HTTP mocking for tests

## Project Structure

```
.
├── download/
│   ├── download.py       # Main downloader module
│   └── test_download.py  # Test suite
├── dumps/                # Downloaded files (created automatically)
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

## License

This project is provided as-is for educational and personal use.
