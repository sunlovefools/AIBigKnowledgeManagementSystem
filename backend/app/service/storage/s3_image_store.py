import os
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel


class S3Config(BaseModel):
    """
    Configuration for S3 image uploads. All fields are required if uploads are enabled.
    """
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    prefix: str = "docling-previews"
    upload_enabled: bool = False


class S3ImageUploadResult(BaseModel):
    """
    Upload result containing information about the uploaded image in S3.
    """
    bucket: str
    key: str
    region: str | None = None
    s3_uri: str = ""
    etag: str | None = None


def _make_s3_uri(bucket: str, key: str) -> str:
    """
    Construct an S3 URI for the given bucket and key.
    """
    return f"s3://{bucket}/{key}"


_DOCLING_ARTIFACT_SUBDIRS = {
    "image": "images",
    "table_image": "table_images",
    "table_data": "table_data",
}


def _load_s3_config() -> S3Config | None:
    """
    Load S3 configuration from environment variables. 
    Returns an S3Config if uploads are enabled, otherwise None.
    """

    bucket = (os.getenv("AWS_S3_BUCKET") or "").strip()
    region = (os.getenv("AWS_REGION") or "").strip()
    access_key_id = (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret_access_key = (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()

    # Validate required fields if uploads are enabled, but allow them to be empty if uploads are disabled.
    missing = [
        name
        for name, value in [
            ("AWS_S3_BUCKET", bucket),
            ("AWS_REGION", region),
            ("AWS_ACCESS_KEY_ID", access_key_id),
            ("AWS_SECRET_ACCESS_KEY", secret_access_key),
        ]
        if not value
    ]
    if missing:
        raise ValueError(
            "S3 upload enabled but required environment variables are missing: "
            + ", ".join(missing)
        )

    prefix = (os.getenv("AWS_S3_PREFIX") or "docling-previews").strip().strip("/")
    if not prefix:
        prefix = "docling-previews"

    session_token = (os.getenv("AWS_SESSION_TOKEN") or "").strip() or None

    # Return the configuration for S3
    return S3Config(
        bucket=bucket,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        prefix=prefix,
        upload_enabled=True,
    )

def build_s3_image_key(
    image_uuid: str,
    extension: str = ".png",
    prefix: str | None = None,
    source_file_name: str | None = None,
    file_id: str | None = None,
    artifact_type: str = "image",
) -> str:
    """
    Build the S3 key for an image.

    If `source_file_name` is provided, the object file name is:
    `{safe_source_stem}-{image_uuid}{extension}`.
    Otherwise it falls back to `{image_uuid}{extension}`.
    """
    ext = extension if extension.startswith(".") else f".{extension}"
    root_prefix = (prefix or "docling-previews").strip("/ ")

    if file_id:
        return build_s3_docling_artifact_key(
            file_id=file_id,
            artifact_uuid=image_uuid,
            artifact_type=artifact_type,
            extension=ext,
            prefix=root_prefix,
        )

    object_name = f"{image_uuid}{ext}"
    if source_file_name:
        source_stem = Path(source_file_name).stem or "document"
        source_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_stem).strip("._-") or "document"
        object_name = f"{source_stem}-{image_uuid}{ext}"
    return f"{root_prefix}/images/{object_name}"


def build_s3_docling_artifact_key(
    *,
    file_id: str,
    artifact_uuid: str,
    artifact_type: str,
    extension: str = ".png",
    prefix: str | None = None,
) -> str:
    """
    Build Docling artifact S3 keys using `{prefix}/{file_id}/{subdir}/{uuid}{ext}`.
    """

    normalized_file_id = (file_id or "").strip()
    if not normalized_file_id:
        raise ValueError("file_id is required to build docling artifact key.")

    subdir = _DOCLING_ARTIFACT_SUBDIRS.get((artifact_type or "").strip().lower())
    if not subdir:
        raise ValueError(
            f"Unsupported artifact_type={artifact_type!r}. "
            f"Expected one of {sorted(_DOCLING_ARTIFACT_SUBDIRS)}."
        )

    ext = extension if extension.startswith(".") else f".{extension}"
    root_prefix = (prefix or "docling-previews").strip("/ ")
    return f"{root_prefix}/{normalized_file_id}/{subdir}/{artifact_uuid}{ext}"


@lru_cache(maxsize=4)
def _get_boto3_s3_client(
    region: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None,
) -> Any:
    """
    Create and cache a boto3 S3 client based on the provided configuration.
    Caching the client allows us to reuse connections and improve performance for multiple uploads.
    """

    # Lazy import so environments without boto3 still work when S3 uploads are disabled.
    import boto3
    from botocore.config import Config

    client_kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
        # Force virtual-hosted style as requested.
        "config": Config(s3={"addressing_style": "virtual"}),
    }
    if session_token:
        client_kwargs["aws_session_token"] = session_token

    return boto3.client(**client_kwargs)


def _get_client_for_config(config: S3Config) -> Any:
    return _get_boto3_s3_client(
        config.region,
        config.access_key_id,
        config.secret_access_key,
        config.session_token,
    )


def upload_bytes_to_s3(
    data: bytes,
    key: str,
    *,
    content_type: str = "application/octet-stream",
    metadata: dict[str, str] | None = None,
    bucket: str | None = None,
    config: S3Config | None = None,
) -> S3ImageUploadResult:
    """
    Upload raw bytes to S3 with the specified key and content type. Returns an S3ImageUploadResult with details of the uploaded object.

    The difference between this and `upload_file_to_s3` is that this function operates on in-memory bytes, while `upload_file_to_s3` reads from a local file path. 
    Use this function when you already have the data in memory and want to upload directly without an intermediate file.

    Note:
        - Files here refer to images, not the user uploaded files
    """
    if config is None:
        raise ValueError("S3 config is required for upload_bytes_to_s3().")
    resolved_config = config
    resolved_bucket = bucket or resolved_config.bucket
    client = _get_client_for_config(resolved_config)

    response = client.put_object(
        Bucket=resolved_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        Metadata=metadata or {},
    )
    etag = response.get("ETag")

    return S3ImageUploadResult(
        bucket=resolved_bucket,
        key=key,
        region=resolved_config.region,
        s3_uri=_make_s3_uri(resolved_bucket, key),
        etag=etag,
    )


def upload_file_to_s3(
    local_path: str | Path,
    key: str,
    *,
    content_type: str = "application/octet-stream",
    metadata: dict[str, str] | None = None,
    bucket: str | None = None,
    config: S3Config | None = None,
) -> S3ImageUploadResult:
    """
    Upload a file from the local filesystem to S3. This is a convenience wrapper around `upload_bytes_to_s3`.

    Purpose of this function is that if the file is already on disk, then we will have this available for direct upload without needing to read it into memory first. 
    
    Note:
        - Files here refer to images, not the user uploaded files
    """

    path = Path(local_path)
    data = path.read_bytes()
    return upload_bytes_to_s3(
        data=data,
        key=key,
        content_type=content_type,
        metadata=metadata,
        bucket=bucket,
        config=config,
    )


def generate_presigned_get_url(
    key: str,
    *,
    bucket: str | None = None,
    expires_in: int | None = None,
    config: S3Config | None = None,
) -> str:
    if config is None:
        raise ValueError("S3 config is required for generate_presigned_get_url().")
    resolved_config = config
    resolved_bucket = bucket or resolved_config.bucket
    ttl = expires_in or int(os.getenv("AWS_S3_PRESIGN_TTL_SECONDS", "3600"))
    client = _get_client_for_config(resolved_config)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": resolved_bucket, "Key": key},
        ExpiresIn=ttl,
    )
