import base64
import uuid

import pytest
from pydantic import ValidationError

from app.profile import PROFILE_DOCUMENT_ID
from app.sync_schemas import SyncWrite
from tests.test_sync_postgres import operation


@pytest.mark.parametrize("size,valid", [(16, True), (65536, True), (15, False), (65537, False)])
def test_profile_ciphertext_boundaries(size, valid):
    body = operation(PROFILE_DOCUMENT_ID)
    body["payload"]["ciphertext"] = base64.b64encode(bytes(size)).decode()
    if valid:
        assert SyncWrite(**body).entry_id == PROFILE_DOCUMENT_ID
    else:
        with pytest.raises(ValidationError):
            SyncWrite(**body)


@pytest.mark.parametrize("version", [1, 2])
def test_profile_cannot_reference_blob_in_either_attachment_format(version):
    body = operation(PROFILE_DOCUMENT_ID)
    body["attachment_version"] = version
    if version == 1:
        body["blob_id"] = str(uuid.uuid4())
    else:
        body["blob_ids"] = [str(uuid.uuid4())]
    with pytest.raises(ValidationError):
        SyncWrite(**body)


def test_profile_cannot_send_plaintext_fields():
    body = operation(PROFILE_DOCUMENT_ID)
    body["name"] = "Private name"
    with pytest.raises(ValidationError):
        SyncWrite(**body)
