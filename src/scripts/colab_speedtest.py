"""ウェイト取得の実効速度を測る。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_speedtest.py

**確保した直後に必ず流す。** 割り当てられたクラスタによって Hugging Face からの
取得速度が桁で変わる(実測: 速い回は 165〜300MB/s、遅い回は 8MB/s)。クラスタは
選べないが、遅い当たりを引いたら **すぐ stop して引き直す**方が安い。

判定に使うのは 1.58GB の LoRA 1本。**小さすぎるファイルでは測れない**。0.31GB の VAE で
測っていたときは 128MB/s と出たが、同じセッションの実際の取得は 273〜299MB/s だった。
TCP のスロースタートと接続確立が支配的で、定常速度に達する前に終わってしまうため。

**閾値を上げてはいけない。** 測定値は実測より低く出るので、上げると速いクラスタまで
弾いて引き直しを繰り返すことになる。遅い当たりとは20倍以上離れているので 30MB/s で足りる。

落とすファイルは Qwen-Image-Edit の Lightning LoRA。本番でも使うものなので、
続行する場合はキャッシュがそのまま生きて無駄にならない。
"""

import os
import time
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = "lightx2v/Qwen-Image-Edit-2511-Lightning"
FILE = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors"
THRESHOLD_MBPS = 30.0


def main() -> None:
    token = Path("/content/hf-token")
    if token.exists():
        os.environ.setdefault("HF_TOKEN", token.read_text().strip())
        print("HF_TOKEN あり")
    else:
        print("HF_TOKEN なし(未認証)")

    start = time.time()
    path = hf_hub_download(repo_id=REPO, filename=FILE)
    elapsed = time.time() - start
    mb = Path(path).stat().st_size / 1024**2
    mbps = mb / elapsed if elapsed > 0 else 0.0

    print(f"{mb:.0f}MB を {elapsed:.1f}秒 => **{mbps:.1f} MB/s**")
    if mbps:
        print(f"40GB の見込み: {40 * 1024 / mbps / 60:.0f}分 (実測はこれより速いことが多い)")
    print("判定:", "続行してよい" if mbps >= THRESHOLD_MBPS else "**遅い。引き直した方がよい**")


if __name__ == "__main__":
    main()
