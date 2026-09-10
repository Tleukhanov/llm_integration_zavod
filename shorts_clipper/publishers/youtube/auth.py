import base64
import os
import pickle
import tempfile
from pathlib import Path


def get_youtube_service(
    client_secret_file: Path | str | None = None,
    token_cache_dir: Path | str | None = None,
):
    """Authenticate and return the YouTube service object."""
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    channel = os.environ.get("SHORTS_CHANNEL")

    if token_cache_dir:
        token_path = Path(token_cache_dir) / "youtube" / "token.pickle"
    elif channel:
        token_path = Path("data/creds") / channel / "youtube" / "token.pickle"
    else:
        token_path = Path(".cache/shorts-clipper/token.pickle")

    if client_secret_file is None:
        b64 = os.environ.get("YT_CLIENT_SECRET_B64")
        if b64:
            secret_dir = token_path.parent
            secret_dir.mkdir(parents=True, exist_ok=True)
            decoded = base64.b64decode(b64)
            client_secret_file = secret_dir / "client_secret.json"
            client_secret_file.write_bytes(decoded)
        else:
            env_file = os.environ.get("YT_CLIENT_SECRET_FILE")
            if env_file:
                client_secret_file = env_file
            else:
                client_secret_file = "client_secret.json"

    creds = None
    if token_path.exists():
        with open(token_path, "rb") as token:
            creds = pickle.load(token)  # noqa: S301

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.parent.mkdir(parents=True, exist_ok=True)
                with open(token_path, "wb") as token:
                    pickle.dump(creds, token)
            except Exception as e:
                token_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "YouTube channel is not connected. Please link your YouTube account from the Web UI sidebar first!"
                ) from e
        else:
            raise RuntimeError(
                "YouTube channel is not connected. Please link your YouTube account from the Web UI sidebar first!"
            )

    return build("youtube", "v3", credentials=creds)
