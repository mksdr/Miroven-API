import hashlib
import io
import uuid

import pytest


def key():
    return uuid.uuid4().hex + "/" + uuid.uuid4().hex


def test_storage_roundtrip_duplicate_and_delete(object_store):
    name = key()
    data = b"encrypted-object-content" * 8192
    checksum = hashlib.sha256(data).hexdigest()
    object_store.put(name, io.BytesIO(data), checksum)
    object_store.put(name, io.BytesIO(data), checksum)
    assert object_store.get(name, len(data)) == data
    with pytest.raises(OSError):
        object_store.get(name, 10)
    object_store.delete(name)
    object_store.delete(name)
    with pytest.raises(FileNotFoundError):
        object_store.get(name, len(data))


def test_storage_rejects_changed_content_and_invalid_checksum(object_store):
    name = key()
    data = b"first"
    object_store.put(name, io.BytesIO(data), hashlib.sha256(data).hexdigest())
    with pytest.raises(OSError):
        object_store.put(name, io.BytesIO(b"second"), hashlib.sha256(b"second").hexdigest())
    assert object_store.get(name, len(data)) == data
    with pytest.raises(OSError):
        object_store.put(key(), io.BytesIO(data), "0" * 64)


@pytest.mark.parametrize("name", ["../outside", "/etc/passwd", "x/y", "../" * 10])
def test_storage_rejects_untrusted_paths(object_store, name):
    with pytest.raises(ValueError):
        object_store.get(name, 100)
    with pytest.raises(ValueError):
        object_store.put(name, io.BytesIO(b"x"), hashlib.sha256(b"x").hexdigest())
    with pytest.raises(ValueError):
        object_store.delete(name)
