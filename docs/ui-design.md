# F05 UI 设计规格

**依据**：`docs/设计文档.md` §12（Gradio UI）、§13（错误处理）、§16（MVP 验收）
**目标 task**：F05-T03「将结果接入 `ui.py`」（`docs/开发文档.md`）
**技术边界**：Gradio 6.22.0；不引入 React/Vue、无独立前端构建系统；UI 只渲染 `AnalysisResult`

## 0. 设计立场

1. **这是一份"检查报告"，不是仪表盘。** 阅读顺序 = 现场巡检顺序：看图定位 → 看风险项 → 核对依据 → 落实整改。结果区采用"纸面报告"样式（白底、细边框、弱阴影），与 SaaS 卡片墙区分。
2. **UI 只渲染 `AnalysisResult` 字段，零推导。** 不新增业务字段、不重排 Finding（按 payload 顺序渲染）、不重新计算法规。`scene_summary` 只存在于 Qwen 契约（`VisualInvestigation`），**不在 `AnalysisResult` 中，UI 不得显示**。
3. **所有来自结果的动态文本一律经 `html.escape` 后拼接**（模型文本不可信，`gr.HTML` 是原始 HTML 注入点）。静态文案（状态条标题、提示语）可直接书写。

## 1. 信息架构

单页、无 Tab、无路由。两个纵向分区：

```text
┌──────────────┬──────────────────────────────────┐
│ 左（上传区） │ 右（结果区）                      │
│              │                                  │
│  gr.Image    │  ① 状态条（status banner）        │
│  上传原图     │  ② Finding 列表                  │
│              │     F1 · 高风险 · 标题           │
│  [开始分析]   │     可见证据 → 风险说明/机理        │
│   [清空]      │     相关法规（direct/conditional） │
│              │     整改建议 → 分析限制 → 规则提示   │
│  gr.Image    │                                  │
│  标注结果图   │  ・首页：说明占位                 │
│  （原图+bbox  │  ・加载：分析中占位               │
│    编号 F1..）│                                  │
└──────────────┴──────────────────────────────────┘
```

**标注编号即卡片编号。** `image.py` 在结果图上绘制 F1/F2 等编号，与 Finding 卡片头部的 `finding_id` 徽章一致——这是本工具最重要的"可扫读性"设计：图中编号 ↔ 卡片标题一一对应。

**流程与状态：** 空态 → 点击分析 → 加载态（10–60 秒，一次模型请求）→ 终态。状态条数据来源：`result.message` 优先，缺失时用映射表静态标题；错误状态附加静态提示语（恢复路径）。

| `AnalysisStatus` | 状态条 | 标注图 |
|---|---|---|
| `completed` | 「分析完成 · 共 N 个风险」（N = `len(findings)`） | 标注图 |
| `no_findings` | 设计文档文案（`message`）「当前图片可见范围内未发现明确的消防风险。」+ 固定说明行「该结论仅基于图片可见内容。」 | 原图（无标注） |
| `image_unusable` | 错误横幅：「图片无法使用」+ `message` + 「请重新上传 JPEG / PNG / WEBP 图片。」 | None |
| `model_failed` | 错误横幅：「模型分析失败」+ `message` + 「请稍后重试；若持续失败，请检查模型配置。」 | None |
| `invalid_model_output` | 错误横幅：「模型输出无效」+ `message` + 「建议更换更清晰的图片后重试。」 | None |

## 2. 组件层级（Finding 卡片模板）

```html
<section class="finding prio-high" id="finding-F1">
  <header class="finding-head">
    <span class="finding-id">F1</span>            <!-- 与图中标注编号一致 -->
    <h3 class="finding-title">人员通行空间被大量堆物占用</h3>
    <span class="prio prio-high">高风险</span>     <!-- 文本+颜色，非只靠颜色 -->
  </header>
  <div class="sec">
    <span class="sec-label">可见证据</span>
    <ul><li>多个纸箱连续占据… <span class="tag">标注 F1</span></li></ul>
  </div>
  <div class="sec">
    <span class="sec-label">风险说明</span>
    <p class="desc">…description…</p>
    <p class="mech">风险机理：…risk_mechanism…</p>
  </div>
  <div class="sec">
    <span class="sec-label">相关法规</span>
    <details class="clause"><summary>
      <span class="clause-cite">《中华人民共和国消防法》 第二十八条</span>
      <span class="rel rel-conditional">条件相关</span>
      <code class="clause-id">XFF-28</code>
    </summary><p class="clause-text">…clause_text…</p></details>
    <ul class="missing-cond">…missing_conditions…</ul>   <!-- 条件相关时，条目位于 details 之外、始终可见 -->
  </div>
  <div class="sec"><span class="sec-label">整改建议</span><p>…recommended_action…</p></div>
  <div class="sec limits"><span class="sec-label">分析限制</span><ul>…limitations…</ul></div>
  <div class="sec warns"><span class="sec-label">规则提示</span><ul>…rule_warnings…</ul></div>
</section>
```

- 卡片顺序与设计文档 §12 固定显示顺序一致：标题 → 优先级 → 证据 → 说明/机理 → 法规 → 建议 → 限制。
- 卡片左侧 4px 优先级色条（`border-left`，不产生布局位移）。
- `limitations` / `rule_warnings` 为空则对应区块整体不渲染（空标签是噪音）；`evidence` 至少一条；证据无 bbox 时不显示「标注 F1」标签。
- `rule_status` 不在 UI 单独展示——`rule_warnings` 已携带可审计信息。
- 法规折叠用原生 `<details>/<summary>`（语义、键盘、零 JS）。

## 3. 事件接线与 Gradio 组件表

**极简节点策略：** 结果区重排版（芯片、`<details>`、长法条换行）全部交给一个 `gr.HTML` + 自定义 CSS，Gradio 组件只保留 4 个静态节点。避免"一个 Finding 拆几十个 gr.Markdown/Accordion 节点"的同步与布局成本；法规折叠不用 `gr.Accordion`（无法放进 `gr.HTML`，且嵌套节点多）。

| 用途 | 组件 | 关键参数 |
|---|---|---|
| 页面 | `gr.Blocks` | `title="“智消慧检”——基于多模态人工智能的消防安全风险智能识别与辅助研判系统"`, `analytics_enabled=False`；Gradio 6 中 `theme`/`css` 为 launch 参数，在 `launch_app` 的 `.launch(theme=_THEME, css=_CSS)` 传入；`footer_links=[]` 隐藏底栏（通过 API 使用 / 使用 Gradio 构建 / 设置） |
| 页头 | `gr.Markdown` × 2 | 标题 + 职责说明；第二行为模型名（`Settings.qwen_model` ← `.env` 的 `QWEN_MODEL`，`.env` 路径锚定项目根，避免 CWD 依赖） |
| 上传 | `gr.Image` | `type="filepath"`, `sources=["upload","clipboard"]`, `label="上传消防场景图片"` |
| 触发 | `gr.Button("开始分析", variant="primary")` | queue 下事件运行中自动禁用 |
| 清空 | `gr.ClearButton(image_input, value="清空")` | 另挂 `.click` 复位两个输出 |
| 标注图输出 | `gr.Image` | `interactive=False`, `buttons=["download"]`, `label="风险标注结果"` |
| 结果区 | **单个 `gr.HTML`** | `elem_id="result_area"`，初始值为空态说明 |

**不使用：** `gr.JSON`（审计信息已并入警告行）、`gr.Accordion`、`gr.Tabs`、`gr.Label`、`gr.Dataframe`、`gr.State`、逐个字段的 `gr.Markdown`。

**事件链：**

```python
analyze_button.click(render_loading_html, outputs=result_area, show_progress="hidden").then(
    on_analyze, inputs=image_input, outputs=[annotated_output, result_area],
    show_progress="hidden",
)
```

`show_progress="hidden"`（Gradio 6 事件参数，默认 `"full"`）关闭队列进度遮罩与右下角 `processing | 已用时/预估时长` 计时器——Qwen 单次数十秒，遮罩会盖住结果区，且计时器容易让用户误以为是"上传"或"平白无故"出现；加载反馈由页面自带的加载占位承担。

`on_analyze` 内部流程（省略号处为管线）：

1. 无图 → 返回空态占位；
2. `prepare_image(image_path)` 抛 `ImageProcessingError` → 构造 `AnalysisResult(status=image_unusable, message=…, findings=[])` 走统一渲染；
3. `PreparedImage` 直接传入 `pipeline.analyze(prepared)`（管线接受 `ImageSource`，避免二次解码，且 bbox 坐标系与绘制一致）；
4. 兜底 `except Exception` → 渲染「模型分析失败 · 系统异常」错误横幅，任何路径页面不崩溃；
5. `completed` / `no_findings` → `draw_bboxes(prepared, {finding_id: [bboxes]})` 生成标注图；错误状态返回 None。
   `no_findings` 时 bbox 为空，`draw_bboxes` 返回无标注原图（等价于"原图"）。

## 4. 视觉层级

四级层次，靠字号/字重/明度，不靠阴影堆叠：

1. **页标题 + 状态条**：22px/700；状态条自带底色与左边条。
2. **Finding 标题**：17px/700，与编号徽章、优先级芯片同行。
3. **区块标签**（可见证据/风险说明/相关法规/…）：12.5px/600/灰 `#64748B`，字距 0.02em——两秒扫完一张卡。
4. **正文**：15px/400/`#334155`；法规原文 14.5px/行高 1.8/`#475569`。

## 5. 风险优先级处理

| 值 | 标签 | 芯片 | 卡片左边条 |
|---|---|---|---|
| high | 高风险 | 底 `#FEE2E2` / 字 `#B91C1C` | `#DC2626` |
| medium | 中风险 | 底 `#FEF3C7` / 字 `#B45309` | `#F59E0B` |
| low | 低风险 | 底 `#F1F5F9` / 字 `#475569` | `#94A3B8` |

- 低风险用**中性灰蓝而不用绿色**——"绿=安全"在消防语境会误导。
- 结果区**不排序、不聚合**，按 payload 顺序渲染。
- CTA 橙色（`#F97316`）只用于"开始分析"按钮与 completed 状态条左边条，与高风险红、中风险琥珀刻意区分，避免橙色按钮被读成警示色。

## 6. 直接 / 条件相关法规处理

- 每条法规引用行（`<summary>`）常显：`《source_name》clause_number` + 关系芯片 + `clause_id`（11.5px mono，审计可核）。
- `direct` → **「直接相关」**实心蓝芯片（底 `#EFF6FF` / 字 `#1D4ED8` / 边 `#BFDBFE`）。
- `conditional` → **「条件相关」**琥珀**虚线边框**芯片（底 `#FFFBEB` / 字 `#92400E` / 边 `#D97706` dashed）。**`missing_conditions` 位于 `</details>` 之后、始终可见**（合并门槛要求"条件关联及缺失条件在 UI 中可见"，放进折叠区会被挡住），底色/边框同琥珀虚线样式。
- 法规原文放在 `<details>` 内；一屏 2–3 条时仍可逐条精读。

## 7. 空态 / 加载态 / 错误态

- **空态**（首次加载/清空后）：右区 3 行静音说明——「上传图片后点击『开始分析』。」+「将展示：标注图片 · 风险（优先级/证据/机理）· 相关法规与缺失条件 · 整改建议。」这是功能图例而非欢迎页。
- **加载态**：结果区展示六个固定视觉阶段，不与真实 Pipeline 进度绑定。当前阶段圆点以 1.5 秒呼吸动画强调，阶段完成后圆点变为 ✓，底部状态文字做淡入淡出切换；最终六个阶段统一显示完成。
- **错误态**：红色横幅带 `role="alert"`（屏幕阅读器可播报），固定文案给出恢复路径；左列保留原图便于换图重试。

## 8. 版式（Typography）

- **系统字体栈，不引 Google Fonts**（境内/内网环境不可靠；Noto 系列作为本地兜底之一）：

  ```css
  --frs-font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
              "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC",
              "Source Han Sans SC", "Noto Sans SC", "Helvetica Neue", Arial, sans-serif;
  ```

- 主题同步：`gr.themes.Base(primary_hue="orange", neutral_hue="slate", font=<同上字符串>, font_mono=<mono 栈>)`——传普通字符串，**不用 `gr.themes.GoogleFont`**（避免联网拉字体）。
- 字号：正文/列表 15px（移动端 ≥16px）；标题 17px/700；页头 22px/700（Markdown）；区块标签 12.5px/600；法条 14.5px；`clause_id` 11.5px mono。行高：正文 1.7，法条 1.8；长文本 `overflow-wrap: anywhere`。

## 9. 间距（Spacing）

4px 基准：页容器 20px（移动端 12px，由 Gradio 默认响应式承担）→ 层内实际间距：Findings 之间 14px；卡片内边距 18px 16px；卡片内区块间隙 10px；`<details>` 内 10px 12px；状态条内边距 12px 14px；法条与缺失项之间 6px；末尾元素归零（`.sec:last-child`、`.clause:last-child` 等）。

## 10. 色彩令牌（Color Tokens）

CSS 变量定义在 `.frs` 根（每个 HTML 片段最外层 `<div class="frs">`），作用域自包含——**结果区恒为浅色纸面色调**，不随 Gradio 暗色模式切换（法规报告是"纸张"，与页面主题解耦，规避双份暗色芯片成本）：

```css
--frs-bg: #FFFFFF;     --frs-page-bg: #F8FAFC;  --frs-border: #E2E8F0;
--frs-text: #334155;   --frs-text-2: #64748B;    --frs-text-3: #475569;
/* 优先级 */
--frs-high-fg: #B91C1C; --frs-high-bg: #FEE2E2; --frs-high-bar: #DC2626;
--frs-med-fg: #B45309;  --frs-med-bg: #FEF3C7;  --frs-med-bar: #F59E0B;
--frs-low-fg: #475569;  --frs-low-bg: #F1F5F9;  --frs-low-bar: #94A3B8;
/* 法规关系 */
--frs-rel-direct-fg: #1D4ED8; --frs-rel-direct-bg: #EFF6FF; --frs-rel-direct-bd: #BFDBFE;
--frs-rel-cond-fg: #92400E;   --frs-rel-cond-bg: #FFFBEB;   --frs-rel-cond-bd: #D97706; /* dashed */
/* 状态横幅 */
--frs-error-bg: #FEF2F2; --frs-error-bd: #FCA5A5; --frs-error-fg: #991B1B;
--frs-ok-bg: #ECFDF5;    --frs-ok-bd: #6EE7B7;    --frs-ok-fg: #047857;
```

对比度：正文 `#334155`/白 ≈ 9.4:1；次要 `#64748B` ≈ 4.8:1；各芯片底/字 ≈ ≥4.5:1；所有语义均带文字标签，不单独依赖颜色。

## 11. 响应式

- `gr.Row` 两列：左 `gr.Column(scale=5, min_width=360)`、右 `gr.Column(scale=7, min_width=380)`——Gradio 在宽度不足时自动堆叠（约 ≤780px 变单列：图在上、结果在下）。
- HTML 内：卡片头 `flex-wrap`（窄屏时芯片换行而非挤压）；正文与法条 `overflow-wrap: anywhere`；`#result_area` 不设固定宽度。
- 只需验证 375 / 768 / 1440 三档；单列下保持同一阅读顺序（图 → 风险 → 法规 → 建议）。

## 12. 实现映射与验收

`src/fire_safety/ui.py` 结构：

| 函数/常量 | 职责 |
|---|---|
| `_FONT_STACK` / `_FONT_MONO` / `_THEME` / `_CSS` | 字体栈、主题、令牌与组件样式 |
| `render_empty_html()` / `render_loading_html()` | 空态 / 加载态静态 HTML |
| `render_result_html(result) -> str` | 按 `result.status` 分发；只读 `AnalysisResult` |
| `_render_finding_html(f)` / `_render_clause_html(law)` | 卡片 / 法条（含关系芯片与 `missing_conditions`） |
| `_annotated_for_result(prepared, result)` | `completed`/`no_findings` 时绘制标注图，否则 None |
| `build_app(settings=None)` / `launch_app(...)` | 页面构造与启动（保持既有签名） |

F05-T03 验收对照：

- [ ] 5 种 `status` 均可渲染，错误状态展示 `message`；
- [ ] `no_findings` 显示设计文档规定文案；
- [ ] `conditional` 必出「条件相关」芯片 + `missing_conditions`（在折叠区外、默认可见）；
- [ ] `direct` 与 `conditional` 芯片视觉可区分（实心蓝 / 虚线琥珀）；
- [ ] UI 无 `scene_summary`，无 payload 之外的排序与推导；动态文本全经 `html.escape`；
- [ ] 法规原文在 `<details>` 内、关键词可达；字体栈纯系统，无外链字体；
- [ ] 页面仅 4 个交互组件 + 1 个 `gr.HTML` 结果区，无 `gr.JSON`/`gr.Accordion` 堆砌。
