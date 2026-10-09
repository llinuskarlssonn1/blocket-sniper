# blocket-sniper

Automated marketplace scanner and deal sniffer for Blocket.

## Architecture

The project is structured into modular components:

```text
blocket-sniper/
├── blocket_sniper/
│   ├── __init__.py        # Package exports
│   ├── __main__.py        # Module entrypoint (`python -m blocket_sniper`)
│   ├── config.py          # Centralized configuration (timeouts, thresholds, exclusions, search params)
│   ├── models.py          # Strongly-typed data models (Listing)
│   ├── storage.py         # SQLite persistence & deduplication (StorageManager)
│   ├── filters.py         # Deterministic rule engine (DeterministicFilter)
│   ├── client.py          # Asynchronous HTTP client & scraper (BlocketIngestor)
│   └── pipeline.py        # End-to-end orchestration (SniperPipeline)
├── main.py                # Main CLI entrypoint
├── script.py              # Backward-compatible script entrypoint
├── tests/                 # Unit tests
│   └── test_sniper.py
└── requirements.txt       # Project dependencies
```

## How It Works

1. **Ingestion**: Polls Blocket's search endpoint with browser fingerprint impersonation (`curl_cffi`).
2. **Deduplication**: Checks candidate ad IDs against local SQLite cache (`sniper_cache.db`).
3. **Layer 1 Filter (Summary)**: Evaluates heading keywords and enforces a minimum price threshold.
4. **Detail Acquisition**: Concurrently fetches full descriptions for surviving listings (bounded by semaphore).
5. **Layer 2 Filter (Deep)**: Evaluates full descriptions for defect keywords, lockouts, and sub-80% battery health.
6. **Persistence**: Records newly processed listings to avoid redundant checks on subsequent cycles.

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

### Running

You can run the sniper using any of the following:

```bash
python main.py
```
or:
```bash
python script.py
```
or:
```bash
python -m blocket_sniper
```

### Running Tests

```bash
python -m unittest discover -s tests
```
