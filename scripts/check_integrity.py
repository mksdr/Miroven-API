"""Verify every committed local/S3 ciphertext against its DB size and hash."""

import hashlib
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import BlobObject  # noqa: E402
from app.storage import build_storage  # noqa: E402


def check(engine, storage):
    count = 0
    with Session(engine) as db:
        for blob in db.scalars(select(BlobObject).where(BlobObject.status != "pending")):
            content = storage.get(blob.storage_key, blob.size)
            if len(content) != blob.size or hashlib.sha256(content).hexdigest() != blob.sha256:
                raise ValueError("Blob integrity mismatch")
            count += 1
    return count


if __name__ == "__main__":
    engine = None
    try:
        settings = get_settings()
        engine = build_engine(settings)
        print(f"verified_blobs:{check(engine, build_storage(settings))}")
    except Exception as exc:
        raise SystemExit(f"integrity_failed:{type(exc).__name__}") from None
    finally:
        if engine is not None:
            engine.dispose()
