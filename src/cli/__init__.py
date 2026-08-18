"""`cw` の実体。`uv tool install --editable .` でここが `comfy_wrapper` として入る。

パッケージにするのはディスパッチャだけで、生成の中身は `src/scripts/` のまま残す。
コンテナからも `docker compose run --rm client src/scripts/...` で今までどおり動く。
"""
