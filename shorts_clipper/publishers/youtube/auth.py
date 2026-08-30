import pickle
from pathlib import Path


def get_youtube_service(client_secret_file: Path | str = "client_secret.json"):
    """Authenticate and return the YouTube service object."""
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    token_path = Path(".cache/shorts-clipper/token.pickle")

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
