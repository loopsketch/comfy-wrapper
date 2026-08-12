"""画像モデルのルック比較を Colab 上で回す。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_image_bench.py

同じプロンプトを複数のモデル・LoRA に通し、生成時間とともに /content/bench へ集める。
ComfyUI に直接投げるので、FastAPI 側の実装(#2 後半)より先に結果が見られる。

比較の軸は 4 つ:
  photoreal  実写・映画風フォトリアル(現行 monochrome-buddy-3 のルック)
  japanese   商品パッケージの日本語(現行は3〜4文字が限界)
  anime      アニメ調(i2v でアニメ MV を作れるか)
  character  キャラの全身像(一貫性の土台)
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/content/comfy/server")

import image_workflows as iw  # noqa: E402

COMFY = "http://127.0.0.1:8188"
OUT = Path("/content/bench")

# 現行作品(monochrome-buddy-3)のルックに寄せた検証用プロンプト
PROMPTS = {
    "photoreal": (
        "Photorealistic cinematic film still. A young Japanese woman in her early twenties "
        "sits on the floor of a sunlit apartment, hugging a small panda plush toy the size of "
        "a cushion. Warm afternoon light through a lace curtain, pastel mint and cream "
        "interior, shallow depth of field, 35mm lens, natural skin texture, no makeup shine."
    ),
    "japanese": (
        "Photorealistic close-up of two Japanese snack bags held up in a supermarket aisle. "
        "The left bag is orange and reads ガリボリ in bold rounded Japanese lettering. "
        "The right bag is light blue and reads ふわもち. Shelves behind are out of focus. "
        "Only these two packages carry readable text."
    ),
    "anime": (
        "Modern anime style illustration, 2D cel shading with clean linework. A cheerful "
        "Japanese high school girl in a summer uniform runs along a seaside road at golden "
        "hour, holding a small panda plush under one arm. Bright saturated colors, soft "
        "gradient sky, light bloom, expressive eyes."
    ),
    "character": (
        "Full body character sheet photograph, front view, standing straight, entire body "
        "from head to shoes inside the frame. A Japanese woman in her early twenties with a "
        "shoulder-length bob, wearing a mint green cardigan, white wide pants and cream "
        "sneakers. Plain light gray background, even studio lighting."
    ),
}

# (ラベル, モデル, LoRA)
RUNS = [
    ("qwen-image", "qwen-image", []),
    ("qwen+anime-modern", "qwen-image", [("qwen_image_modern_anime.safetensors", 1.0)]),
    ("qwen+anime", "qwen-image", [("qwen_image_anime.safetensors", 1.0)]),
]

# 参照画像つきの検証(Qwen-Image-Edit-2511)。キャラ一貫性はこちらでしか測れない。
# 参照は /content/ComfyUI/input/ に置いたファイル名で指す。
REF_IMAGE = "ref_character.png"
EDIT_RUNS = [
    (
        "edit-pose",
        "The woman in image 1, exactly the same face, hairstyle and outfit, "
        "now sitting on a park bench in three-quarter view, holding a small panda "
        "plush toy on her lap. Keep the mint cardigan, white wide pants and cream "
        "sneakers unchanged. Natural daylight, photorealistic.",
        [],
    ),
    (
        "edit-anime",
        "Convert image 1 into a modern anime style illustration with clean cel "
        "shading and crisp linework. Keep the same person, the same hairstyle and "
        "the same outfit. Full body, standing, plain background.",
        [],
    ),
    (
        "edit-anime-lora",
        "Convert image 1 into a modern anime style illustration with clean cel "
        "shading and crisp linework. Keep the same person, the same hairstyle and "
        "the same outfit. Full body, standing, plain background.",
        [("qwen_image_edit_2511_anime.safetensors", 1.0)],
    ),
]

ASPECT = "9x16"  # 現行作品が縦なので縦で比べる
SEED = 12345


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{COMFY}{path}", data=json.dumps(payload).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _history(prompt_id: str) -> dict:
    with urllib.request.urlopen(f"{COMFY}/history/{prompt_id}", timeout=30) as r:
        return json.loads(r.read())


def _wait(prompt_id: str, timeout: int = 900) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = _history(prompt_id).get(prompt_id)
        if h and h.get("status", {}).get("completed"):
            return h
        if h and h.get("status", {}).get("status_str") == "error":
            return h
        time.sleep(3)
    return None


def _save(history: dict, dest: Path) -> bool:
    for out in (history.get("outputs") or {}).values():
        for img in out.get("images", []):
            q = urllib.parse.urlencode(
                {"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                 "type": img.get("type", "output")}
            )
            with urllib.request.urlopen(f"{COMFY}/view?{q}", timeout=120) as r:
                dest.write_bytes(r.read())
            return True
    return False


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = []

    for label, model, loras in RUNS:
        for axis, prompt in PROMPTS.items():
            # アニメ LoRA は anime 軸だけ、素のモデルは anime も含めて全部見る
            if loras and axis != "anime":
                continue
            name = f"{axis}__{label}"
            dest = OUT / f"{name}.png"
            if dest.exists():
                print(f"[skip] {name}")
                continue

            wf = iw.build_t2i(
                model, prompt, aspect=ASPECT, seed=SEED, loras=loras,
                filename_prefix=f"bench_{name}",
            )
            t = time.time()
            try:
                res = _post("/prompt", {"prompt": wf})
            except Exception as e:
                print(f"[fail] {name}: 投入できません {e}")
                report.append({"name": name, "error": str(e)})
                continue

            h = _wait(res["prompt_id"])
            elapsed = time.time() - t
            if not h or not _save(h, dest):
                msg = json.dumps(h.get("status", {}), ensure_ascii=False)[:200] if h else "timeout"
                print(f"[fail] {name}: {msg}")
                report.append({"name": name, "error": msg, "seconds": round(elapsed, 1)})
                continue

            size_kb = dest.stat().st_size / 1024
            print(f"[ok  ] {name}  {elapsed:6.1f}s  {size_kb:6.0f}KB")
            report.append({"name": name, "seconds": round(elapsed, 1),
                           "kb": round(size_kb), "model": model,
                           "loras": [n for n, _ in loras]})

    # 参照画像つき(キャラ一貫性)。参照が置かれていなければ黙って飛ばす
    ref = Path("/content/ComfyUI/input") / REF_IMAGE
    if not ref.exists():
        print(f"[skip] 参照画像 {ref} が無いので edit の検証は行わない")
    else:
        for label, prompt, loras in EDIT_RUNS:
            dest = OUT / f"{label}.png"
            if dest.exists():
                print(f"[skip] {label}")
                continue

            wf = iw.build_edit(
                prompt, [REF_IMAGE], aspect=ASPECT, seed=SEED, loras=loras,
                filename_prefix=f"bench_{label}",
            )
            t = time.time()
            try:
                res = _post("/prompt", {"prompt": wf})
            except Exception as e:
                print(f"[fail] {label}: 投入できません {e}")
                report.append({"name": label, "error": str(e)})
                continue

            h = _wait(res["prompt_id"])
            elapsed = time.time() - t
            if not h or not _save(h, dest):
                msg = json.dumps(h.get("status", {}), ensure_ascii=False)[:200] if h else "timeout"
                print(f"[fail] {label}: {msg}")
                report.append({"name": label, "error": msg, "seconds": round(elapsed, 1)})
                continue

            print(f"[ok  ] {label}  {elapsed:6.1f}s")
            report.append({"name": label, "seconds": round(elapsed, 1),
                           "model": "qwen-image-edit",
                           "loras": [n for n, _ in loras]})

    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n{len([r for r in report if 'error' not in r])}/{len(report)} 枚を {OUT} に出力した")


if __name__ == "__main__":
    main()
