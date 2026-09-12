#!/usr/bin/env bash
# install.sh — bootstrap Shorts Factory on a fresh Linux VPS
# Usage: sudo bash deploy/install.sh
# Override defaults via env: BASE_DIR, PYTHON_BIN, GIT_REPO
set -euo pipefail

BASE_DIR="${BASE_DIR:-/srv/shorts-clipper}"
REPO="${GIT_REPO:-https://github.com/anomalyco/shorts-clipper.git}"
PYTHON_MIN="3.10"

# ── 1. System deps ────────────────────────────────────────────────
echo "==> Checking system dependencies …"
for cmd in git ffmpeg python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  installing $cmd …"
        apt-get update -qq && apt-get install -y -qq "$cmd"
    fi
done

# Verify python version
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if printf '%s\n%s\n' "$PYTHON_MIN" "$PY_VER" | sort -V | head -n1 | grep -q "$PYTHON_MIN"; then
    echo "  python $PY_VER >= $PYTHON_MIN — OK"
else
    echo "  ERROR: python $PY_VER is too old (need >= $PYTHON_MIN)" >&2
    exit 1
fi

# ── 2. Clone or pull repo ─────────────────────────────────────────
if [ -d "$BASE_DIR/.git" ]; then
    echo "==> Repo exists at $BASE_DIR — pulling latest …"
    git -C "$BASE_DIR" pull --rebase origin main
else
    echo "==> Cloning repo into $BASE_DIR …"
    git clone "$REPO" "$BASE_DIR"
fi

# ── 3. Python venv + dependencies ─────────────────────────────────
echo "==> Setting up Python venv …"
if [ ! -d "$BASE_DIR/.venv" ]; then
    python3 -m venv "$BASE_DIR/.venv"
fi
"$BASE_DIR/.venv/bin/pip" install --upgrade pip -q
"$BASE_DIR/.venv/bin/pip" install -r "$BASE_DIR/requirements.txt" -q

# ── 4. .env template ─────────────────────────────────────────────
if [ ! -f "$BASE_DIR/.env" ]; then
    if [ -f "$BASE_DIR/.env.example" ]; then
        cp "$BASE_DIR/.env.example" "$BASE_DIR/.env"
        echo "  Created .env from .env.example — edit it with your keys!"
    else
        echo "  WARNING: no .env.example found; create $BASE_DIR/.env manually."
    fi
fi

# ── 5. systemd units ──────────────────────────────────────────────
echo "==> Installing systemd units …"
sed "s|/srv/shorts-clipper|$BASE_DIR|g" \
    "$BASE_DIR/deploy/shorts-factory.service" > /etc/systemd/system/shorts-factory.service
sed "s|/srv/shorts-clipper|$BASE_DIR|g" \
    "$BASE_DIR/deploy/shorts-factory.timer"   > /etc/systemd/system/shorts-factory.timer

systemctl daemon-reload
systemctl enable --now shorts-factory.timer

echo ""
echo "=== Shorts Factory installed ==="
echo "  Repo:          $BASE_DIR"
echo "  Timer:         shorts-factory.timer (daily at 12:00 + 5 min after boot)"
echo "  Manual run:    systemctl start shorts-factory.service"
echo "  Logs:          journalctl -u shorts-factory -f"
echo ""
echo "  Next steps:"
echo "    1. Edit $BASE_DIR/.env with your API keys"
echo "    2. systemctl start shorts-factory.service   (manual first run)"
echo "    3. journalctl -u shorts-factory -f           (watch output)"
