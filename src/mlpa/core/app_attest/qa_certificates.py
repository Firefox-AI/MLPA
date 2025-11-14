from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable

from google.cloud import storage
from google.cloud.exceptions import NotFound
from loguru import logger

from mlpa.core.config import env

QA_CERT_DIR = Path(env.APP_ATTEST_QA_CERT_DIR)
QA_CERT_FILENAMES: tuple[str, ...] = (
    "root_cert.pem",
    "root_key.pem",
    "key_id.json",
)

_certificates_ready = False
_download_lock = threading.Lock()


class QACertificateError(RuntimeError):
    """Raised when QA certificates cannot be prepared."""


def ensure_qa_certificates(force: bool = False) -> None:
    """
    Ensure QA certificates exist locally by downloading them from the configured bucket if needed.

    Args:
        force: If True, always attempt to download irrespective of cached state.

    Raises:
        QACertificateError: If certificates cannot be downloaded while APP_ATTEST_QA is enabled.
    """
    if not env.APP_ATTEST_QA:
        return

    global _certificates_ready
    if _certificates_ready and not force:
        return

    with _download_lock:
        if _certificates_ready and not force:
            return

        QA_CERT_DIR.mkdir(parents=True, exist_ok=True)

        missing_files = _missing_certificates()
        if not missing_files and not force:
            _certificates_ready = True
            return

        if not env.APP_ATTEST_QA_BUCKET:
            raise QACertificateError(
                "APP_ATTEST_QA is enabled but APP_ATTEST_QA_BUCKET is not configured. "
                "Either provide the bucket or place certificates under qa_certificates/."
            )

        _download_certificates(missing_files or QA_CERT_FILENAMES)

        still_missing = _missing_certificates()
        if still_missing:
            raise QACertificateError(
                f"Failed to download QA certificates: {', '.join(still_missing)}"
            )

        _certificates_ready = True


def _download_certificates(filenames: Iterable[str]) -> None:
    try:
        client = storage.Client(project=env.APP_ATTEST_QA_GCP_PROJECT_ID)
    except Exception as e:
        raise QACertificateError(
            f"Failed to initialize GCS client: Please ensure GCP credentials are properly configured."
        ) from e
    bucket = client.bucket(env.APP_ATTEST_QA_BUCKET)
    prefix = (env.APP_ATTEST_QA_BUCKET_PREFIX or "").strip("/")

    for filename in filenames:
        blob_path = f"{prefix}/{filename}" if prefix else filename
        destination = QA_CERT_DIR / filename
        try:
            blob = bucket.blob(blob_path)
            blob.download_to_filename(destination)
            logger.info(
                f"Downloaded QA certificate blob '{blob_path}' from bucket "
                f"'{bucket.name}' to '{destination}'."
            )
        except NotFound as e:
            raise QACertificateError(
                f"QA certificate '{blob_path}' not found in bucket '{bucket.name}'."
            ) from e
        except Exception as e:  # pragma: no cover - defensive logging
            raise QACertificateError(
                f"Failed to download QA certificate '{blob_path}"
            ) from e


def _missing_certificates() -> list[str]:
    return [
        filename
        for filename in QA_CERT_FILENAMES
        if not (QA_CERT_DIR / filename).exists()
    ]
