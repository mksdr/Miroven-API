import hashlib
import io
import json
import logging
import sys
import tarfile

import pytest

from app.observability import SafeFilter
from scripts.backup_restore import validate_backup


def test_logs_discard_credentials_urls_and_exception_details():
    try:
        raise RuntimeError("postgresql://private:password@host?token=secret")
    except RuntimeError:
        record = logging.LogRecord(
            "uvicorn.error",
            logging.ERROR,
            "",
            1,
            "private@example.com code=123456",
            (),
            sys.exc_info(),
        )
    SafeFilter().filter(record)
    assert json.loads(record.getMessage())["error_type"] == "RuntimeError"
    assert record.exc_info is None
    assert "password" not in record.getMessage()
    assert "private" not in record.getMessage()
    record = logging.LogRecord(
        "miroven.requests",
        logging.INFO,
        "",
        1,
        {
            "request_id": "safe-id",
            "route": "/api/v1/blobs/{blob_id}",
            "status": 200,
            "authorization": "Bearer secret",
            "query": "token=secret",
        },
        (),
        None,
    )
    SafeFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "{blob_id}" in record.getMessage()


@pytest.mark.parametrize("unsafe", [False, True])
def test_backup_validation_rejects_corruption_and_unsafe_archives(tmp_path, unsafe):
    (tmp_path / "db.dump").write_bytes(b"PGDMP")
    with tarfile.open(tmp_path / "blobs.tar", "w") as archive:
        member = tarfile.TarInfo("../escape" if unsafe else "safe/blob")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"abc"))
    manifest = {
        "format": 1,
        "files": {
            name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
            for name in ("db.dump", "blobs.tar")
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    if unsafe:
        with pytest.raises(ValueError, match="Unsafe"):
            validate_backup(tmp_path)
    else:
        validate_backup(tmp_path)
        (tmp_path / "db.dump").write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="checksum"):
            validate_backup(tmp_path)
