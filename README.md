# Qwen3.8-27B 消防风险分析系统 MVP

当前已完成 F01：工程和 Gradio 页面骨架，以及 F02：图片解码、EXIF 方向修正、尺寸限制和 bbox 绘制。页面支持单图上传和“开始分析”交互，后续 F03–F05 将依次接入 Qwen、结构化校验、法规规则和最终结果展示。

## 目标

完成以下端到端链路：

> 上传一张消防场景图片 → Qwen3.8-27B 开放视觉分析 → 结构化风险 → 确定性法规关联 → Gradio 可视化结果

## 技术栈

- Python 3.13.15
- uv
- Gradio 6.22.0
- Qwen3.8-27B
- Pydantic 2
- Pillow

## 环境

`.python-version`：

```text
3.13.15
```

`pyproject.toml` 至少包含：

```toml
[project]
name = "fire-safety-ai"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = [
    "gradio==6.22.0",
    "openai",
    "pydantic>=2,<3",
    "pydantic-settings>=2,<3",
    "pillow",
]

[dependency-groups]
dev = ["pytest", "ruff"]
```

环境变量：

```text
QWEN_BASE_URL=
QWEN_API_KEY=
QWEN_MODEL=Qwen3.8-27B
```

可复制 `.env.example` 为 `.env` 后填写配置。F01 页面不会发起 Qwen 请求，真实密钥不会提交到仓库。

安装和运行：

```bash
uv sync
uv run python app.py
```

测试：

```bash
uv run pytest
uv run ruff check .
```

## 核心流程

```text
Gradio 上传图片
→ 图片解码和 EXIF 方向修正
→ Qwen3.8-27B Structured Output
→ Schema / bbox / Issue Code 校验
→ rule_bindings.json 查询
→ clauses.json 回填法规
→ 组装 AnalysisResult
→ Gradio 展示 bbox、Finding、法规和整改建议
```

## F02 图片处理接口

`fire_safety.image.prepare_image` 接收上传字节或文件路径，返回 `PreparedImage`。其中
`PreparedImage.image` 与 `PreparedImage.qwen_bytes` 都来自同一张 EXIF 修正后的图像，后续模型输入和 UI
bbox 绘制必须使用这组统一坐标基准。无法使用的图片抛出 `ImageProcessingError`，其 `status` 为
`image_unusable`。

`bbox_to_pixels` 校验 0–1000 的整数坐标并转换为半开像素矩形；`draw_bboxes` 对每个 Finding 单独处理，
无效 bbox 会被跳过，但不会影响同一 Finding 或其他 Finding 的绘制。

## 文件职责

| 文件 | 用途 |
| --- | --- |
| `prompts/visual_investigator.md` | Qwen 图片分析 Prompt |
| `schemas/visual_investigation.schema.json` | Qwen Structured Output 契约 |
| `schemas/analysis_result.schema.json` | Pipeline 最终结果契约 |
| `data/legal/clauses.json` | 法规名称、条款号和条款原文 |
| `data/legal/issue_codes.json` | 受控 Issue Code、默认优先级和整改建议 |
| `data/legal/rule_bindings.json` | Issue Code 到法规条款的确定性映射 |
| `docs/条款表.md` | 法规条款的人类可读版本 |
| `examples/analysis_result.example.json` | 最终结果示例 |

## 实现规则

### Qwen 输出

Qwen 输出：

- `scene_summary`
- `regions`
- `findings`
- 每个 Finding 的 `suggested_issue_codes`

Qwen 不输出法规名称、法规编号、条款号、条款原文或违法结论。

### Issue Code

程序只接受 `issue_codes.json` 中存在的 Code。无有效 Code 的 Finding 仍然展示，法规列表为空。

### 法规关联

`rule_bindings.json` 中：

- `direct`：当前可见风险与条款内容可直接关联；
- `conditional`：条款相关，但还需要图片之外的信息确认适用条件。

每个 Finding 最多展示 3 条法规，排序规则：

1. `direct` 优先于 `conditional`；
2. `priority` 数字越小越优先；
3. 相同 `clause_id` 去重。

### bbox

bbox 使用 0-1000 归一化坐标：

```text
[x_min, y_min, x_max, y_max]
```

单个 bbox 无效时忽略该 bbox，不删除整个 Finding。

## 开发顺序

1. 工程初始化和 Gradio 页面骨架；
2. 图片处理和 bbox 绘制；
3. Qwen Client、Prompt 和 Structured Output；
4. Issue Code 校验和法规关联；
5. AnalysisResult 组装和 Gradio 展示；
6. Schema、Rule、Pipeline 测试和真实图片验证。

## MVP 验收

- JPEG / PNG / WEBP 单图可上传；
- Qwen 返回符合 Schema 的结构化结果；
- 支持 Issue Code 列表之外的开放 Finding；
- 可定位风险能在图片上显示 bbox；
- 无效 bbox 不删除 Finding；
- 无效 Issue Code 被过滤；
- 法规字段全部来自 `clauses.json`；
- `rule_bindings.json` 能稳定完成法规关联；
- 条件法规能显示缺失条件；
- 无法规映射的 Finding 仍可展示；
- Gradio 完整展示图片、风险、法规和整改建议；
- `uv run pytest` 通过。
