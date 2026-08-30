# Security Policy

## Supported versions

Shorts Clipper is in active early development. Security fixes are applied to the main branch.

## Reporting a vulnerability

Please do not open a public issue for sensitive security reports.

If you find a vulnerability, contact the repository owner privately through GitHub. Include:

- affected commit or version
- reproduction steps
- expected impact
- any suggested mitigation

## Security considerations

Shorts Clipper executes local media-processing tools and downloads user-provided media URLs. Contributors should follow these rules:

- Never pass untrusted input through `shell=True`.
- Build ffmpeg and yt-dlp commands as argv lists.
- Validate URLs, paths, timestamps, and output locations.
- Do not log API keys, cookies, tokens, or private URLs.
- Keep generated files inside the configured work/output directory.
- Treat third-party media files as untrusted input.
- Avoid committing downloaded media, model blobs, `.env`, or credentials.
