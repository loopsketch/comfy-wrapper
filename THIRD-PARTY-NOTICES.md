# Third-party notices / サードパーティの表示

## Comfy-Org/workflow_templates

`src/server/h3_workflows.py` / `wan_workflows.py` / `ltx_workflows.py` /
`image_workflows.py` / `post_workflows.py` build ComfyUI API-format workflows whose node
graphs, input names, and defaults are derived from the official ComfyUI workflow
templates and blueprints.

これらのワークフロー(ノード構成・入力名・既定値)は、ComfyUI 公式のワークフロー
テンプレートを API フォーマットへ展開したものです。

https://github.com/Comfy-Org/workflow_templates

```
MIT License

Copyright (c) 2023-present Comfy Org

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

ComfyUI itself (GPL-3.0) is not bundled or linked here — this project talks to a running
ComfyUI instance over HTTP.

ComfyUI 本体 (GPL-3.0) は同梱もリンクもしていません。動作中の ComfyUI と HTTP で
やり取りするだけです。
