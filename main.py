"""
Main CLI entrypoint for Blocket Sniper.
Runs a marketplace poll cycle and presents identified deals, or handles admin commands.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from blocket_sniper.pipeline import SniperPipeline
from blocket_sniper.config import DEFAULT_CONFIG


def setup_logging() -> None:
    """Configure console logging format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def reset_database(db_path: str) -> None:
    """Safely unlinks the SQLite database and its associated journal files."""
    db_file = Path(db_path)
    
    if not db_file.exists():
        print(f"Database '{db_path}' does not exist. Nothing to remove.")
        return

    try:
        # Eradicate the main DB and any lingering WAL/journal files to prevent corruption
        associated_files = [
            db_file,
            Path(f"{db_path}-journal"),
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm")
        ]

        for file in associated_files:
            if file.exists():
                file.unlink()
                print(f"Deleted: {file.name}")
                
        print("Database reset successful. Next run will start with a fresh cache.")
    except PermissionError:
        print("Permission Denied: Ensure no other processes (like DB Browser) are locking the file.")
        sys.exit(1)


async def run_sniper() -> None:
    """Run a single polling cycle and display results."""
    setup_logging()
    pipeline = SniperPipeline()
    try:
        leads = await pipeline.run_cycle()
        if leads:
            print("\n" + "=" * 60)
            print(f"IDENTIFIED {len(leads)} ACTIONABLE CANDIDATES:")
            print("=" * 60)
            for item in leads:
                price_str = f"{item.price} SEK" if item.price is not None else "Price not set"
                print(f"[{item.id}] '{item.heading}' - {price_str}")
                print(f"URL: {item.url}")
                desc_snippet = item.description
                print(f"Description snippet: {desc_snippet}\n")
        else:
            print("\nNo new actionable candidates found in this cycle.")
    finally:
        await pipeline.shutdown()


if __name__ == "__main__":
    # 1. Define CLI arguments
    parser = argparse.ArgumentParser(description="Blocket Sniper Arbitrage Bot")
    parser.add_argument(
        "--reset-db", 
        action="store_true", 
        help="Deletes the local SQLite database and exits."
    )
    
    # 2. Parse arguments
    args = parser.parse_args()

    # 3. Handle administrative commands before event loop initialization
    if args.reset_db:
        confirmation = input("WARNING: This will delete all seen ads history. Continue? [y/N]: ")
        if confirmation.lower() == 'y':
            reset_database(DEFAULT_CONFIG.storage.db_path)
        else:
            print("Aborted.")
        sys.exit(0) # Exit cleanly, bypass the scraper

    # 4. Standard execution path
    try:
        asyncio.run(run_sniper())
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(0)