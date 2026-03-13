import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.storage import s3_image_store as store


def test_load_s3_config_raises_when_required_vars_missing(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(ValueError):
        store._load_s3_config()


def test_load_s3_config_returns_config_when_values_present(monkeypatch):
    monkeypatch.setenv("AWS_S3_BUCKET", "bucket-a")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sk")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    cfg = store._load_s3_config()
    assert cfg is not None
    assert cfg.bucket == "bucket-a"
    assert cfg.region == "ap-southeast-1"


def test_build_s3_image_key_uses_image_uuid_only_when_no_source_name():
    key = store.build_s3_image_key(
        image_uuid="img-uuid-1",
        extension=".png",
        prefix="docling-previews",
    )
    assert key == "docling-previews/images/img-uuid-1.png"


def test_build_s3_image_key_uses_source_filename_and_uuid():
    key = store.build_s3_image_key(
        image_uuid="img-uuid-1",
        extension=".png",
        prefix="docling-previews",
        source_file_name="Client Portfolio Analysis Report.pdf",
    )
    assert key == "docling-previews/images/Client_Portfolio_Analysis_Report-img-uuid-1.png"


def test_build_s3_image_key_uses_file_id_paths_for_docling_artifacts():
    key = store.build_s3_image_key(
        image_uuid="img-uuid-1",
        extension=".png",
        prefix="docling-previews",
        file_id="file-uuid-1",
        artifact_type="image",
    )
    assert key == "docling-previews/file-uuid-1/images/img-uuid-1.png"


def test_build_s3_docling_artifact_key_for_table_data():
    key = store.build_s3_docling_artifact_key(
        file_id="file-uuid-1",
        artifact_uuid="table-uuid-1",
        artifact_type="table_data",
        extension=".json",
        prefix="docling-previews",
    )
    assert key == "docling-previews/file-uuid-1/table_data/table-uuid-1.json"


def test_s3_uri_format():
    assert store._make_s3_uri("my-bucket", "a/b/c.png") == "s3://my-bucket/a/b/c.png"


def test_upload_bytes_to_s3_success_with_mock_client(monkeypatch):
    class _FakeClient:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(kwargs)
            return {"ETag": '"etag-123"'}

    fake_client = _FakeClient()
    cfg = store.S3Config(
        bucket="bucket-a",
        region="ap-southeast-1",
        access_key_id="ak",
        secret_access_key="sk",
        prefix="docling-previews",
        upload_enabled=True,
    )
    monkeypatch.setattr(store, "_get_client_for_config", lambda _cfg: fake_client)

    result = store.upload_bytes_to_s3(
        data=b"png-bytes",
        key="docling-previews/run/images/img.png",
        content_type="image/png",
        metadata={"image_uuid": "u1"},
        config=cfg,
    )

    assert result.bucket == "bucket-a"
    assert result.key.endswith("img.png")
    assert result.s3_uri == "s3://bucket-a/docling-previews/run/images/img.png"
    assert fake_client.calls[0]["ContentType"] == "image/png"
    assert fake_client.calls[0]["Metadata"]["image_uuid"] == "u1"
    assert "artifact_run_id" not in fake_client.calls[0]["Metadata"]


def test_upload_bytes_to_s3_propagates_client_errors(monkeypatch):
    class _FakeClient:
        def put_object(self, **kwargs):
            raise RuntimeError("upload failed")

    cfg = store.S3Config(
        bucket="bucket-a",
        region="ap-southeast-1",
        access_key_id="ak",
        secret_access_key="sk",
        prefix="docling-previews",
        upload_enabled=True,
    )
    monkeypatch.setattr(store, "_get_client_for_config", lambda _cfg: _FakeClient())

    with pytest.raises(RuntimeError, match="upload failed"):
        store.upload_bytes_to_s3(
            data=b"data",
            key="k",
            config=cfg,
        )


def test_generate_presigned_get_url_with_mock_client(monkeypatch):
    class _FakeClient:
        def generate_presigned_url(self, method, Params=None, ExpiresIn=None):
            return f"https://example.com/{Params['Bucket']}/{Params['Key']}?ttl={ExpiresIn}"

    cfg = store.S3Config(
        bucket="bucket-a",
        region="ap-southeast-1",
        access_key_id="ak",
        secret_access_key="sk",
        prefix="docling-previews",
        upload_enabled=True,
    )
    monkeypatch.setattr(store, "_get_client_for_config", lambda _cfg: _FakeClient())

    url = store.generate_presigned_get_url(
        key="docling-previews/run/images/x.png",
        expires_in=600,
        config=cfg,
    )
    assert "bucket-a" in url
    assert "x.png" in url


def test_upload_bytes_requires_explicit_config():
    with pytest.raises(ValueError, match="S3 config is required"):
        store.upload_bytes_to_s3(data=b"data", key="k")


def test_generate_presigned_requires_explicit_config():
    with pytest.raises(ValueError, match="S3 config is required"):
        store.generate_presigned_get_url(key="k")
