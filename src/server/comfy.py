"""ローカルの ComfyUI (127.0.0.1) を叩くクライアント。"""

from __future__ import annotations

import uuid

import httpx


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ready(self) -> bool:
        """ComfyUI が起動しているか。

        特定ノードの有無は見ない。画像モデルだけを載せたセッション(H3 なし)でも
        静止画の生成は通るようにするため。動画側は has_node() で確かめる。
        """
        try:
            r = await self._http.get("/system_stats")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def has_node(self, class_type: str) -> bool:
        """そのノードが読める状態か(モデルが載っているか)。"""
        try:
            r = await self._http.get(f"/object_info/{class_type}")
            return r.status_code == 200 and bool(r.json())
        except httpx.HTTPError:
            return False

    async def options(self, class_type: str, input_name: str) -> list[str]:
        """ノードのコンボ入力の選択肢(= ComfyUI が見つけているファイル名)を返す。

        **2つの形式がある。** 旧来の INPUT_TYPES で書かれたノードは
        `[[選択肢...], {...}]`、新しい io.Schema のノードは
        `["COMBO", {"options": [選択肢...]}]` を返す。片方だけ見ていると、
        後者のノード(補間モデルのローダ等)が「ウェイトが無い」と誤判定される。
        """
        try:
            r = await self._http.get(f"/object_info/{class_type}")
            if r.status_code != 200:
                return []
            spec = r.json().get(class_type, {}).get("input", {})
            for group in ("required", "optional"):
                entry = spec.get(group, {}).get(input_name)
                if not entry:
                    continue
                if isinstance(entry[0], list):
                    return [str(v) for v in entry[0]]
                if len(entry) > 1 and isinstance(entry[1], dict):
                    return [str(v) for v in entry[1].get("options", [])]
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            return []
        return []

    async def has_model(self, class_type: str, input_name: str, filename: str) -> bool:
        """そのウェイトが models/ に置かれているか。

        ノードの有無ではファイルの有無が分からない。Wan も LTX もノード自体は
        ComfyUI 本体に入っているので、ここまで見ないと「載っている」と誤判定する。
        """
        return filename in await self.options(class_type, input_name)

    async def upload(self, data: bytes, filename: str, content_type: str) -> str:
        """input ディレクトリへアップロードし、LoadImage/LoadAudio 用の名前を返す。"""
        files = {"image": (filename, data, content_type)}
        r = await self._http.post(
            "/upload/image", files=files, data={"type": "input", "overwrite": "true"}
        )
        if r.status_code != 200:
            raise ComfyError(f"アップロード失敗 ({r.status_code}): {r.text[:300]}")
        body = r.json()
        subfolder = body.get("subfolder") or ""
        name = body["name"]
        return f"{subfolder}/{name}" if subfolder else name

    async def queue(self, workflow: dict) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        r = await self._http.post("/prompt", json=payload)
        if r.status_code != 200:
            raise ComfyError(f"ワークフロー投入に失敗 ({r.status_code}): {r.text[:800]}")
        body = r.json()
        if body.get("node_errors"):
            raise ComfyError(f"ワークフローが不正: {body['node_errors']}")
        return body["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        r = await self._http.get(f"/history/{prompt_id}")
        if r.status_code != 200:
            return None
        return r.json().get(prompt_id)

    async def queue_position(self, prompt_id: str) -> int | None:
        """キュー内の待ち位置。実行中は 0、キューに無ければ None。"""
        r = await self._http.get("/queue")
        if r.status_code != 200:
            return None
        body = r.json()
        for item in body.get("queue_running", []):
            if len(item) > 1 and item[1] == prompt_id:
                return 0
        for i, item in enumerate(body.get("queue_pending", [])):
            if len(item) > 1 and item[1] == prompt_id:
                return i + 1
        return None

    async def view(self, filename: str, subfolder: str, type_: str) -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        r = await self._http.get("/view", params=params)
        if r.status_code != 200:
            raise ComfyError(f"出力の取得に失敗 ({r.status_code})")
        return r.content

    async def interrupt(self) -> None:
        await self._http.post("/interrupt")


def extract_output(history: dict, node_id: str) -> dict | None:
    """history から SaveVideo ノードの出力ファイル情報を取り出す。"""
    outputs = (history or {}).get("outputs", {}).get(node_id, {})
    for key in ("images", "video", "videos", "gifs"):
        items = outputs.get(key)
        if items:
            return items[0]
    return None


def failure_reason(history: dict) -> str | None:
    """history に記録された実行失敗の理由。成功していれば None。"""
    status = (history or {}).get("status", {})
    if status.get("status_str") == "error" or not status.get("completed", True):
        for entry in status.get("messages", []):
            if entry and entry[0] == "execution_error":
                detail = entry[1] if len(entry) > 1 else {}
                return f"{detail.get('node_type')}: {detail.get('exception_message')}"
        return "ComfyUI の実行に失敗しました"
    return None
