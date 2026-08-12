"""手元と Colab をつなぐ層。宛先・キー・リトライ・障害の切り分け・単価を集める。

手元から API を叩くスクリプトは複数あるが、経路は1本しかない。同じ経路の面倒は
1か所で見る。引き受けるのは4つ。

- **宛先とキーの解決**(環境変数と .colab/ のファイル。旧名も読む)
- **一時的な失敗のリトライ**。「安いが遅い」運用では1本 72〜400秒 x 12カットを
  GPU を掴んだまま流すので、瞬断で1カット落ちると取り直しにもう一度確保が要る
- **障害の切り分け**。認証切れ・セッション消失・トンネル切れは症状が同じで打つ手が違う
- **GPU の時間単価**。Colab は枚数ではなく稼働時間で課金される
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


class LinkError(RuntimeError):
    """手元から Colab 側の API へ届かなかった、または API がエラーを返した。"""


COLAB_DIR = Path("/app/.colab")
API_KEY_FILE = COLAB_DIR / "colab-api-key"
# 旧名。リネーム前に発行したキーをそのまま使えるようにする
LEGACY_API_KEY_FILE = COLAB_DIR / "h3-api-key"
AUTH_STATE = COLAB_DIR / "auth-state.json"
SESSIONS = COLAB_DIR / ".config" / "colab-cli" / "sessions.json"
GPU_FILE = COLAB_DIR / "gpu"

DEFAULT_ENDPOINT = "http://tunnel:8000"

# cloudflared を挟んでいた頃の名残。ssh -L の経路では接続拒否 (URLError) になるが、
# 前段にプロキシがある構成では今もこのコードで返る
TUNNEL_DOWN_CODES = {502, 503, 504, 521, 522, 523, 524, 530}

CONNECT_TIMEOUT = 60.0
# 一時的なつまずきに粘る回数と間隔。**投入 (POST) は届いた可能性があるものは
# 繰り返さない**(二重生成は GPU 時間をそのまま捨てることになる)
RETRIES = 3
RETRY_WAIT = 5.0

# Colab の従量課金。100CU = 1179円 なので 1CU = 11.79円 (2026-08 時点)
YEN_PER_CU = 11.79
YEN_PER_USD = 157.87
# **既定は L4。** A100 の 3.44分の1 の単価で 1.44倍しか遅くない (README のコスト表)
DEFAULT_GPU = "L4"
GPU_CU_PER_HOUR = {"L4": 1.54, "A100": 5.30}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_endpoint(endpoint: str | None = None) -> str:
    """宛先を決める。tunnel サービスで固定されるので通常は設定不要。"""
    value = (
        endpoint
        or os.environ.get("COLAB_ENDPOINT")
        or os.environ.get("COLAB_H3_ENDPOINT")  # 旧名。既存の .env をそのまま通す
        or DEFAULT_ENDPOINT
    )
    return value.rstrip("/")


def read_api_key(api_key: str | None = None) -> str:
    """平文キーを読む。

    キーは colab_key.sh が手元で発行し、Colab へは SHA-256 ハッシュだけを送る。
    平文がリモートに存在しないので、.env に置くより漏れる面が小さい。
    """
    if api_key:
        return api_key
    for name in ("COLAB_API_KEY", "COLAB_H3_API_KEY"):  # 後者は旧名
        value = os.environ.get(name)
        if value:
            return value
    for path in (API_KEY_FILE, LEGACY_API_KEY_FILE):
        try:
            value = path.read_text().strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def require_api_key(api_key: str | None = None) -> str:
    key = read_api_key(api_key)
    if not key:
        raise LinkError(
            f"アクセスキーがありません。{API_KEY_FILE} が無い場合は "
            "src/scripts/colab_key.sh で発行してください"
        )
    return key


def current_gpu() -> str:
    """いま確保している GPU の名前。colab_run.sh が確保時に書く。"""
    try:
        name = GPU_FILE.read_text().strip()
    except OSError:
        return DEFAULT_GPU
    return name or DEFAULT_GPU


def yen_per_hour(gpu: str | None = None) -> float:
    name = gpu or current_gpu()
    return GPU_CU_PER_HOUR.get(name, GPU_CU_PER_HOUR[DEFAULT_GPU]) * YEN_PER_CU


def usd_per_hour(gpu: str | None = None) -> float:
    return yen_per_hour(gpu) / YEN_PER_USD


def usd_for_seconds(seconds: float, gpu: str | None = None) -> float:
    """GPU を seconds 秒 占有したときの概算コスト(USD)。"""
    return seconds / 3600 * usd_per_hour(gpu)


def diagnose() -> str:
    """届かない原因を、次の一手が分かる形で1行にする。

    認証切れ・セッション消失・トンネル切れは手元から見ると同じ「つながらない」だが、
    打つ手は全部違う。ここで見分けて名指しする。
    """
    auth = _read_json(AUTH_STATE)
    if auth and auth.get("state") == "reauth_needed":
        return (
            "Colab の認証が切れています。docker compose exec colab colab sessions で"
            "入れ直してください。**切れている間はランタイムの確認も停止もできない**ので、"
            "入れ直したあと src/scripts/colab.sh sessions で現物を見ること"
        )
    sessions = _read_json(SESSIONS)
    if sessions is not None and not sessions:
        return (
            "Colab のセッションがありません。src/scripts/colab_run.sh で"
            "確保し直してください"
        )
    return (
        "トンネルが切れています。docker compose restart tunnel を試してください"
        "(台帳にはセッションが残っています)"
    )


def _shape_http_error(endpoint: str, method: str, path: str, exc) -> LinkError:
    body = exc.read().decode(errors="replace")
    if exc.code in TUNNEL_DOWN_CODES:
        return LinkError(f"{endpoint} に到達できません (HTTP {exc.code})。{diagnose()}")
    if "<html" in body[:200].lower():
        # プロキシのエラーページをそのまま載せると読めない
        body = f"(HTML {len(body)} バイト)"
    return LinkError(f"{method} {path} が {exc.code}: {body[:300]}")


def request(
    endpoint: str,
    api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: float = CONNECT_TIMEOUT,
    retries: int = RETRIES,
) -> tuple[int, bytes]:
    """API を1回叩く。一時的な失敗は粘る。

    **リトライしてよいのは「届いていない」と分かる失敗だけ。** 接続そのものが
    拒否された (URLError) なら投入は起きていないので繰り返してよい。前段のプロキシが
    返す 5xx は、投入が通ったあとに応答だけ失われた可能性があるため、
    **本体を変えない GET でしか繰り返さない**。二重生成は GPU 時間を捨てることになる。
    """
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{endpoint}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    last: LinkError | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            error = _shape_http_error(endpoint, method, path, exc)
            retriable = exc.code in TUNNEL_DOWN_CODES and method == "GET"
        except urllib.error.URLError as exc:
            error = LinkError(f"{endpoint} に接続できません ({exc.reason})。{diagnose()}")
            retriable = True
        last = error
        if not retriable or attempt == retries:
            break
        time.sleep(RETRY_WAIT * attempt)
    raise last


def health(endpoint: str, timeout: float = 30.0) -> dict:
    """トンネルと ComfyUI の状態を返す(認証は要らない)。

    生成を投げる前の疎通確認はここに寄せる。呼び出し側で書き直さないこと。
    """
    try:
        with urllib.request.urlopen(f"{endpoint}/health", timeout=timeout) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        if exc.code in TUNNEL_DOWN_CODES:
            raise LinkError(f"{endpoint} に到達できません (HTTP {exc.code})。{diagnose()}") from exc
        raise LinkError(f"/health が {exc.code} を返しました") from exc
    except urllib.error.URLError as exc:
        raise LinkError(f"{endpoint} に接続できません ({exc.reason})。{diagnose()}") from exc
