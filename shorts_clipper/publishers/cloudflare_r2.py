import logging
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config

from shorts_clipper.core.exceptions import ConfigurationError
from shorts_clipper.core.settings import Settings

log = logging.getLogger(__name__)


class R2Storage:
    def __init__(self, settings: Settings):
        self.bucket = settings.r2_bucket_name

        if not all(
            [
                settings.r2_account_id,
                settings.r2_access_key_id,
                settings.r2_secret_access_key,
                self.bucket,
            ]
        ):
            raise ConfigurationError("Missing Cloudflare R2 credentials in settings.")

        endpoint_url = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"

        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=10,
                read_timeout=30,
                retries={"max_attempts": 3},
            ),
        )

    def upload(self, file_path: Path | str) -> str:
        """Upload a file to R2 and return the generated object key."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File to upload does not exist: {path}")

        key = f"shorts/{uuid4().hex}_{path.name}"
        log.info("Uploading %s to R2 bucket '%s' under key '%s'...", path.name, self.bucket, key)

        log.info("Streaming video to R2 via boto3 upload_file...")
        self.s3_client.upload_file(str(path), self.bucket, key)

        log.info("Successfully uploaded %s to R2.", path.name)
        return key

    def generate_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for downloading the object, valid for expires_in seconds."""
        log.info("Generating presigned URL for key '%s' (expires in %ds)...", key, expires_in)
        url = self.s3_client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )
        return url

    def delete(self, key: str) -> None:
        """Delete an object from R2."""
        log.info("Deleting key '%s' from R2 bucket '%s'...", key, self.bucket)
        self.s3_client.delete_object(Bucket=self.bucket, Key=key)
        log.info("Successfully deleted key '%s'.", key)
