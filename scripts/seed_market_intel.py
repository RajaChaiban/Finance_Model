"""Seed the MarketIntelligence Chroma store from local JSON files.

Reads `data/market_intel_seed/*.json` (override via --dir). Each JSON file is
either a list of dicts or a single dict. Each dict must have at least:

  * `id`      — unique identifier (str)
  * `content` — the document body (str)

Optional fields are passed through as Chroma metadata:

  * `doc_type`     — one of "deal" | "market_window" | "pricing_benchmark" |
                     anything else (free-form). The MarketIntelligence
                     query helpers use this to filter retrieval.
  * `asset_class`  — free-form. For our equity-exotic use case, this is the
                     ticker (e.g. "SPY", "AAPL"). Agents pass the underlier
                     as the asset_class filter when querying.
  * Any other keys the corpus author wants to attach.

Example seed file (data/market_intel_seed/spy_recent.json):

  [
    {
      "id": "spy-vol-2026-04-22",
      "doc_type": "market_window",
      "asset_class": "SPY",
      "content": "SPX market remains OPEN. 1M ATM IV 14, 3M ATM IV 16, ..."
    },
    ...
  ]

Usage:
    python scripts/seed_market_intel.py
    python scripts/seed_market_intel.py --dir path/to/json/files
    python scripts/seed_market_intel.py --reset    # clears existing collection first

Exits non-zero if MARKET_INTEL_ENABLED is off or if the MI module can't be
initialised (missing chromadb / sentence-transformers).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Iterable

# Make the repo root importable regardless of where the user invokes this from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_market_intel")


def _iter_seed_files(seed_dir: Path) -> Iterable[Path]:
    if not seed_dir.exists():
        return []
    return sorted(seed_dir.glob("*.json"))


def _load_items(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    raise ValueError(f"{path}: expected list or dict at top level, got {type(raw).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the MarketIntelligence corpus.")
    parser.add_argument(
        "--dir",
        default="data/market_intel_seed",
        help="Directory containing *.json seed files (default: data/market_intel_seed).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear the Chroma collection before seeding.",
    )
    args = parser.parse_args(argv)

    seed_dir = Path(args.dir).resolve()
    files = list(_iter_seed_files(seed_dir))
    if not files:
        logger.warning("No JSON files found under %s. Nothing to seed.", seed_dir)
        # Not an error — just an empty corpus.
        return 0

    try:
        from src.agents.market_intelligence import get_market_intelligence
    except Exception as exc:  # noqa: BLE001
        logger.error("Cannot import market_intelligence: %s", exc)
        return 2

    mi = get_market_intelligence()
    if mi is None:
        logger.error(
            "MarketIntelligence is OFF or unavailable. "
            "Set MARKET_INTEL_ENABLED=1 and ensure chromadb + "
            "sentence-transformers are installed."
        )
        return 2

    if args.reset:
        try:
            collection = getattr(mi.vector_store, "collection", None)
            client = getattr(mi.vector_store, "client", None)
            collection_name = getattr(mi.vector_store, "collection_name", None)
            if collection is not None and client is not None and collection_name:
                client.delete_collection(name=collection_name)
                logger.info("Deleted existing collection %s.", collection_name)
                # Reset the singleton so the next get_market_intelligence()
                # rebuilds the empty collection.
                from src.agents.market_intelligence import reset_market_intelligence
                reset_market_intelligence()
                mi = get_market_intelligence()
                if mi is None:
                    logger.error("Collection reset succeeded but MI failed to re-initialise.")
                    return 2
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not reset collection: %s", exc)

    total = 0
    for path in files:
        try:
            items = _load_items(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping %s (%s)", path.name, exc)
            continue
        if not items:
            continue
        try:
            mi.seed_from_dicts(items)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping %s (seed_from_dicts failed: %s)", path.name, exc)
            continue
        logger.info("Loaded %d items from %s.", len(items), path.name)
        total += len(items)

    final_count = mi.count()
    logger.info(
        "Seeded %d items this run. Corpus now contains %d total documents.",
        total,
        final_count,
    )
    print(f"corpus_count={final_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
