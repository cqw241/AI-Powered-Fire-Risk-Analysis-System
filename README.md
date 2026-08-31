# Qwen3.8-27B 消防与电气安全风险分析系统 MVP

当前已完成 F01–F05：工程与 Gradio 页面骨架、图片处理、Qwen 结构化视觉分析、确定性法规关联、Pipeline 和最终结果展示。系统已具备从单图上传到消防、电气安全风险结果展示的完整 MVP 链路；F06 仍需补充五类真实图片的人工端到端验收记录。

## 目标

完成以下端到端链路：

> 上传一张安全检查场景图片 → Qwen3.8-27B 开放视觉分析 → 结构化风险 → 确定性法规关联 → Gradio 可视化结果

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
    "openai>=1.0,<2",
    "pydantic>=2,<3",
    "pydantic-settings>=2,<3",
    "pillow>=10,<12",
]

[dependency-groups]
dev = ["pytest>=8,<9", "ruff>=0.8,<1", "jsonschema>=4,<5"]
```

环境变量：

```text
QWEN_BASE_URL=
QWEN_API_KEY=
QWEN_MODEL=Qwen3.8-27B
# 可选：low / medium / xhigh；未配置时不向模型服务发送 reasoning_effort
# QWEN_REASONING_EFFORT=low
```

可复制 `.env.example` 为 `.env` 后填写配置。真实密钥不会提交到仓库。`QWEN_REASONING_EFFORT` 为可选项；默认不配置时保持模型服务原有默认推理行为。

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
→ Risk Pack Loader 统一 RuleCatalog
→ Rule Binding 查询与 Clause 回填
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
| `src/fire_safety/risk_packs.py` | Risk Pack 发现、校验、合并和统一 RuleCatalog 构建 |
| `data/legal/risk_packs/*/manifest.json` | 规则包标识、版本和启用状态 |
| `data/legal/risk_packs/*/issue_codes.json` | 受控 Issue Code、默认优先级和整改建议 |
| `data/legal/risk_packs/*/rule_bindings.json` | Issue Code 到法规条款的确定性映射 |
| `data/legal/risk_packs/*/clauses.json` | 法规名称、条款号和条款原文 |
| `docs/条款表.md` | 法规条款的人类可读版本 |
| `examples/analysis_result.example.json` | 最终结果示例 |

## Risk Pack

生产运行时扫描 `data/legal/risk_packs` 的直接子目录。每个规则包固定包含以下四个 JSON 文件，
不包含 Python 代码：

```text
<pack-directory>/
├── manifest.json
├── issue_codes.json
├── rule_bindings.json
└── clauses.json
```

`manifest.json` 必须声明 `schema_version`、`pack_id`、`version`、`catalog_id` 和 `enabled`。
Loader 校验所有 manifest，只加载 `enabled: true` 的规则数据；多个启用包中的 Issue Code、
Binding ID 或 Clause ID 重复时拒绝加载，不进行覆盖。新增领域规则原则上只需增加这样的目录，
无需修改核心 Python 代码。

生产运行时通过 `get_rule_catalog()` 加载上述 Risk Packs。`load_rule_catalog()` 无参数调用加载同一套
manifest 驱动的运行时 Catalog；显式同时传入三条 JSON 文件路径时，可加载测试或外部提供的独立
Catalog。`data/legal` 顶层不再保留旧版三文件，规则数据只在各 Risk Pack 中维护。

当前启用三个规则包：

- `cn-mainland-fire-safety`：消防通道、消防设施、明火和场所使用等消防安全规则；
- `cn-mainland-electrical-safety`：线路、配电设施、用电环境和电动自行车充电等电气安全规则。
- `cn-mainland-penalties`：以 Issue Code 与已命中的实体条款为键，补充国家级相关处罚规定。

电气线路敷设及用电产品周边危险物品规则归 electrical pack 所有，避免同一视觉风险在两个包中
出现语义重叠的 Issue Code。

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
允许受控 Issue Code 暂时没有法规绑定；例如 `POWER_STRIP_DAISY_CHAIN` 当前作为可行动的视觉风险
保留，但在没有经核验的适用条款前返回 `no_binding`，不会生成法规关联。

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

## 当前实现状态

| Feature | 状态 | 内容 |
| --- | --- | --- |
| F01 | 已完成 | Python / uv 工程和 Gradio 页面骨架 |
| F02 | 已完成 | 图片解码、EXIF 修正、尺寸限制、bbox 校验与绘制 |
| F03 | 已完成 | Qwen Client、Prompt、Structured Output 和错误映射 |
| F04 | 已完成 | Issue Code 白名单、Rule Binding、Clause 回填和整改建议 |
| F05 | 已完成 | Pipeline、AnalysisResult 和最终 Gradio 展示 |
| F06 | 进行中 | 自动化测试已完成；待补充五类真实图片端到端验收记录 |

当前启用规则包包含 33 个 Issue Code、49 条法规条款、70 条实体规则绑定和 13 条处罚绑定。测试与静态检查结果：

```text
136 passed
ruff check . → All checks passed
```

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
- `uv run ruff check .` 通过；
- 至少五类真实图片完成上传 → 模型 → 校验 → 规则 → 展示的人工端到端验证。
