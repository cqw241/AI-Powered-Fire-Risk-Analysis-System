# 消防与电气安全风险分析系统

上传一张现场图片，系统使用 Qwen3.8-27B 识别可见风险，再由本地规则包关联法规，最后在 Gradio 页面展示标注图、证据、适用条件和整改建议。模型只提出视觉事实与 Issue Code 建议；法规原文及关联关系由规则包提供。

## 功能

- 标注图片中的风险区域，并逐项展示可见证据、风险等级和整改建议。
- 从本地规则包关联消防、电气安全条款，区分直接相关与条件相关。
- 展示相关处罚规定及仍需现场确认的条件，不输出违法或处罚结论。
- 提供模型原文、耗时分段和调用记录，便于核查分析过程。

## 快速开始

项目使用 Python 3.13 和 uv。依赖版本以 `pyproject.toml`、`uv.lock` 为准。

```bash
uv sync
cp .env.example .env
# 在 .env 中填写 QWEN_BASE_URL 和 QWEN_API_KEY
uv run python app.py
```

默认页面地址为 `http://127.0.0.1:7860`，可通过 `HOST`、`PORT` 修改。支持 JPEG、PNG、WEBP 单图上传。

`.env.example` 列出了全部常用配置。其中：

- `QWEN_PROVIDER` 可选 `dashscope`、`llamacpp`、`vllm`，决定服务商专用的图片参数与日志标签。
- `QWEN_MODEL` 默认 `Qwen3.8-27B`；`QWEN_MAX_PIXELS` 适用于支持该参数的服务商。
- `QWEN_REASONING_EFFORT` 可选 `none`、`low`、`medium`、`xhigh`；留空时使用服务端默认行为。
- `QWEN_TEMPERATURE` 可设为 `0.1`，允许范围为 0–2；留空时使用服务端默认值。

## 结果如何产生

```text
图片解码与 EXIF 修正 → Qwen 结构化视觉分析 → Schema 与 bbox 校验
→ Issue Code 过滤 → 本地规则包关联法规及处罚规定 → AnalysisResult → 页面展示
```

- Finding 可超出预设 Issue Code；无有效 Code 或无法规绑定时仍展示风险，不生成法规引用。
- 法规分为 `direct` 与 `conditional`。后者会同时展示法规和仍需现场确认的 `missing_conditions`。
- 相关处罚规定只在已命中实体条款时附带展示，不构成违法认定或处罚决定。
- bbox 使用 0–1000 归一化坐标；单个无效 bbox 不会删除整个 Finding。
- 图片无法确认的场所属性、距离、审批或维护情况应保留为限制，不得推断为已知事实。

设计边界见 [系统设计](docs/设计文档.md)，结构化契约见 `schemas/`，规则数据见 `data/legal/risk_packs/`。三个启用规则包分别覆盖消防安全、电气安全和相关处罚规定。历史规则扩展理由见 `docs/v1.1-法规扩展.md` 与 `docs/v1.3-处罚规定关联.md`。

## 诊断

每次分析的结果区提供“本次耗时”和“模型输出原文”折叠面板；终端输出 `[耗时]` 摘要。结构化调用记录追加到默认的 `model_calls.jsonl`，可用 `CALL_LOG_PATH` 修改路径。该文件已被 Git 忽略。

参与开发时的检查与验收约定见 [开发规范](docs/开发规范.md)。
