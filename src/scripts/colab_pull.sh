#!/usr/bin/env bash
# Colab 上のディレクトリを固めて手元へ回収する。
#
#   src/scripts/colab_pull.sh /content/jobs works/.jobs [セッション名]
#
# 生成物は works/ 以下に置く。リポジトリには入らない。
set -euo pipefail

cd "$(dirname "$0")/../.."

SRC="${1:?回収元(Colab側の絶対パス)を指定してください}"
DEST="${2:?回収先(手元のパス)を指定してください}"
SESSION="${3:-comfy}"

mkdir -p .colab
TMP=".colab/.pull.py"
trap 'rm -f "$TMP"' EXIT

cat > "$TMP" <<EOF
import subprocess
from pathlib import Path
src = Path("${SRC}")
if not src.exists():
    raise SystemExit(f"{src} がありません")
subprocess.run(
    ["tar", "czf", "/content/pull.tar.gz", "-C", str(src.parent), src.name],
    check=True,
)
size = Path("/content/pull.tar.gz").stat().st_size / 1024**2
print(f"固めた: {size:.1f}MB")
EOF

src/scripts/colab.sh exec -s "$SESSION" -f "$TMP"

mkdir -p "$DEST"
src/scripts/colab.sh download -s "$SESSION" /content/pull.tar.gz "$DEST/.pull.tar.gz"
tar xzf "$DEST/.pull.tar.gz" -C "$DEST" --strip-components=1
rm -f "$DEST/.pull.tar.gz"

echo "回収した: $DEST"
ls -la "$DEST" | tail -20
