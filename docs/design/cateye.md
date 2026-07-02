# CatEye 图片识别工具集设计文档

## 设计理念

### 第一原则：轻与重结合

90% 的日常场景通过预设方案直接完成；10% 的复杂场景，智能体可自行组合原子工具应对。

### 第二原则：内聚自动化

预处理、缓存、上下文注入等横切关注点由核心工具内部自动完成，AI 无需感知。工具调用从「编排多个原子步骤」简化为「一次调用即完成」，将 AI 的决策链路从 5-9 步压缩到 1-3 步。

核心工具（OCR/Search/Vision）在执行时自动完成以下内部流程：
1. 缓存检查 -- 命中则直接返回
2. 图片预处理 -- 按任务类型优化尺寸和格式
3. 执行核心逻辑 -- OCR 识别 / 搜图 / 视觉理解
4. 缓存写入 -- 将结果写入缓存
5. 上下文记录 -- 将分析摘要写入图片认知上下文

### 第三原则：融入记忆生态

缓存条目和图片认知上下文可由外部记忆插件接管，分析结果可纳入已有记忆系统。通过 `MemoryBridge` 协议和 `NEKOKIT_MANAGED_DATA` 声明机制，实现与天使之魂记忆插件的无缝对接。

---

## 实现方式

缓存存储基于 NekoKit 的 kv_store 工具，场景预设硬编码在初始化模块中，工具类继承 BaseTool 并通过注册表注册。核心工具通过 `CateyeServices` 依赖容器注入预处理、缓存和上下文服务。

### 工具清单

暴露给 AI 的工具共 4 个：

| 工具 | 类型 | 职责边界 |
|------|------|---------|
| `nkit_ce_ocr` | 核心 | 文字提取，只负责把图中的文字读出来 |
| `nkit_ce_search` | 核心 | 以图搜图，只负责找到图片的来源或相似图 |
| `nkit_ce_vision` | 核心 | 视觉理解，只负责用大模型解读图片内容 |
| `nkit_ce_scene` | 场景 | 场景预设管理，返回工具组合策略 |

内部工具（不暴露给 AI）：

| 工具 | 职责 |
|------|------|
| `PreprocessTool` | 图片预处理，按任务类型优化尺寸和格式 |
| `CacheTool` | 缓存管理，内部调用 kv_store 管理图片分析缓存 |
| `ImageContextManager` | 图片认知上下文管理，7 天 TTL |

---

## 缓存管理

缓存 Key 基于图片哈希值和任务类型生成（`cat_eye:cache:{image_hash}_{task_type}`），Value 仅存储分析结果和元数据。生命周期 48 小时，超期可清除。缓存条目可被记忆模块管理。

内部缓存调用会触发过期维护：每次进入缓存工具时先检查 `cat_eye:maintenance:cache_cleanup_last_run`，若当天尚未清理，则全量扫描 `cat_eye:cache:` 前缀并删除过期条目；同一天内后续调用只读取维护标记，不重复全量扫描。

### Value 结构

```json
{
  "result": {
    "ocr_text": "提取的文字内容"
  },
  "evaluation": 0,
  "created_at": "2026-05-20T10:30:00Z",
  "expires_at": "2026-05-22T10:30:00Z"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `result` | object | 各工具返回的分析结果，键名与任务类型对应 |
| `evaluation` | integer | 任务评价，范围 [-2, 2]，默认 0 |
| `created_at` | string | 缓存创建时间（ISO 8601） |
| `expires_at` | string | 缓存过期时间（ISO 8601），固定 48 小时 |

### 操作

`CacheTool` 提供三种操作（内部调用，不暴露给 AI）：

- `check`：查询缓存，自动检查过期，命中返回完整条目
- `store`：写入新缓存条目，自动计算图片哈希，设置 48 小时过期
- `update`：合并新结果到已有条目，刷新过期时间，写入评价

### 缓存命中流程

核心工具在执行时自动检查缓存：

1. 调用 `CacheTool.check(image_url, task_type)` 查询缓存
2. 命中且未过期 -- 直接返回缓存结果，跳过预处理和核心逻辑
3. 未命中 -- 执行完整流程（预处理 -> 核心逻辑 -> 缓存写入）

---

## 独立 Context 机制

图片认知上下文（ImageContext）独立于缓存，用于跨任务积累对同一张图片的认知。

### ImageContextManager

管理图片的认知上下文，存储键格式为 `cat_eye:ctx:{image_hash}`，TTL 为 7 天。

内部上下文同样采用每日一次的过期维护：访问或写入上下文时检查 `cat_eye:maintenance:ctx_cleanup_last_run`，若当天尚未清理，则全量扫描 `cat_eye:ctx:` 前缀并删除已超过 7 天 TTL 的条目。

### ImageContext 结构

```json
{
  "image_hash": "md5_dhash",
  "image_url": "原始图片 URL",
  "knowledge": [
    {
      "source": "nkit_ce_ocr",
      "content": "提取的文字摘要（前 200 字）",
      "timestamp": "2026-05-20T10:30:00Z"
    }
  ],
  "scene": {
    "name": "extract_text",
    "description": "文字提取场景"
  },
  "intent": {
    "keywords": ["翻译", "提取文字"],
    "distilled": "用户要求翻译菜单"
  },
  "created_at": "2026-05-20T10:30:00Z",
  "updated_at": "2026-05-20T10:30:00Z"
}
```

### 核心工具与 Context 的交互

- **OCR/Search**：执行完成后，自动将分析摘要写入 Context 的 `knowledge` 列表
- **Vision**：执行前自动从 Context 读取历史分析记录，注入系统 Prompt；执行完成后写入新记录

### MemoryBridge Protocol

定义外部记忆系统的接入协议，包含以下方法：

| 方法 | 说明 |
|------|------|
| `add_knowledge` | 添加知识条目 |
| `get_knowledge` | 获取指定图片的知识条目 |
| `update_scene` | 更新场景信息 |
| `update_intent` | 更新意图信息 |
| `remove_context` | 移除上下文 |
| `build_vision_context` | 构建视觉理解的上下文注入文本 |

`ImageContextManager` 根据是否提供 `bridge` 实例，自动选择 NekoKit 内部存储或外部桥接实现。

---

## 场景预设

场景预设通过 `nkit_ce_scene` 工具管理，内置 5 个预设硬编码在 `BUILTIN_PRESETS` 字典中，自定义预设通过 kv_store 存储（Key: `cat_eye:scene:{scene_name}`）。

### 内置预设

| 编码 | 名称 | 工具链路 | 触发关键词 |
|------|------|---------|-----------|
| `extract_text` | 文字提取 | ocr | 提取文字、OCR、识别文字、台词、字幕、公告 |
| `identify_character` | 角色识别 | search -> vision | 这是谁、角色识别、认人、动漫角色、游戏角色 |
| `find_anime_source` | 番剧溯源 | search | 这是什么番、番名、动漫名称、哪一集、找番 |
| `understand_meme` | 表情包解读 | vision | 梗图、表情包、什么意思、笑点、玩梗 |
| `analyze_chart` | 图片分析 | vision | 分析一下、游戏截图、攻略、面板、配装 |

注意：工具链路中不再包含 preprocess 和 cache 节点，这些由核心工具内部自动执行。

### 预设 Value 结构

```json
{
  "name": "extract_text",
  "description": "从图片中提取文字内容",
  "tool_chain": ["nkit_ce_ocr"],
  "model_preference": "daily",
  "trigger_keywords": ["提取文字", "OCR", "识别文字"],
  "is_preset": true
}
```

内置预设不可被 `is_preset=true` 的数据覆盖。智能体可通过 `update` 操作添加自定义预设。

---

## 核心工具设计

### OCR（nkit_ce_ocr）

引擎：RapidOCR（基于 ONNX Runtime）。无需 GPU，默认模型支持中英文，通过 `text_score` 配置项控制识别精度。同步调用委托给线程池，引擎实例懒加载并缓存。

内部流程：缓存检查 -> 图片预处理（PNG 无损） -> OCR 识别 -> 缓存写入 -> Context 知识记录

### 搜图（nkit_ce_search）

供应商信息由内置常量 `BUILTIN_PROVIDERS` 和运行时动态合并的自定义供应商组成。`initialize` 时调用 `_merge_custom_providers` 解析用户配置的 `custom_providers` JSON 数组，合并到 `_providers` 实例字典中。`BUILTIN_SCENE_MAP` 定义内置场景映射，自定义供应商的 `scene` 字段触发新场景注册。

每个供应商的 API 调用逻辑封装在独立的 `_call_xxx` 方法中，自定义供应商通过通用 `_call_custom` 方法处理（支持 GET/POST、form/json 请求格式、binary/base64 图片编码、嵌套 JSON 路径字段注入、`{api_key}` 占位符替换）。所有供应商调用失败时返回空结果，由 AI 尝试其他工具降级。

内部流程：缓存检查 -> 图片预处理（JPEG 85%） -> 供应商调用 -> 缓存写入 -> Context 知识记录

### 视觉理解（nkit_ce_vision）

双模式设计：`daily` 模式用于日常场景，`professional` 模式用于复杂分析。模型选择由配置驱动，`professional_model` 留空时自动降级为 `daily_model`。

参数仅保留 `image_url`、`prompt`、`mode` 三个，所有上下文注入由内部自动完成。

内部流程：缓存检查 + Context 读取 -> 图片预处理（JPEG 90%） -> 模型调用（注入历史分析记录） -> 缓存写入 -> Context 知识记录

### 场景预设（nkit_ce_scene）

管理场景预设的查询和自定义。支持 `list`（列出所有预设）、`get`（获取指定预设方案）、`update`（更新预设方案）三种操作。内置预设不可被覆盖。

---

## 天使之魂适配

### AngelMemoryBridge

实现 `MemoryBridge` 协议，将图片认知上下文桥接到天使之魂记忆插件的 MemoryRuntime。核心映射逻辑：

- 知识条目 -> 记忆类型 `knowledge`，judgment 为 `[source] content[:50]`
- 标签系统 -> `nekokit_data` + `cateye_context` + source + `img:{hash[:8]}` + `mode:{mode}`
- 记忆作用域 -> `cateye`（与用户级记忆隔离，recall 时同时检索 cateye 和 public 域）
- 检索方式 -> 使用 `chained_recall()` 而非 `recall()`，以自然语言 query 提供语义检索，entities 参数精确匹配图片标签

### NEKOKIT_MANAGED_DATA

在 `Main` 类中声明可被外部插件接管的数据：

```python
NEKOKIT_MANAGED_DATA = {
    "cateye_context": {
        "description": "CatEye 图片认知上下文",
        "bridge_class": "AngelMemoryBridge",
        "module": "nekokit.tools.image_analyzer.angel_memory_bridge",
    }
}
```

外部插件可通过此声明发现并接管 NekoKit 的数据管理。

### context_backend 配置

在 `cateye_general` 配置分组中提供 `context_backend` 选项：

| 值 | 行为 |
|----|------|
| `internal`（默认） | 使用 NekoKit 内部 kv_store 存储 Context |
| `angel_memory` | 使用天使之魂记忆插件作为 Context 后端 |

当选择 `angel_memory` 但天使之魂插件未加载时，自动降级为 `internal` 模式，并输出警告日志。

---

## CateyeServices 依赖容器

`CateyeServices` 是一个数据类，统一注入核心工具所需的服务：

```python
@dataclass
class CateyeServices:
    preprocess: PreprocessTool
    cache: CacheTool
    context: Optional[ImageContextManager] = None
```

核心工具（OCR/Search/Vision）在初始化时接收 `CateyeServices` 实例，通过 `self._services` 访问预处理、缓存和上下文服务。这种设计将横切关注点的注入集中管理，核心工具只需关注自身业务逻辑。

---

## 扩展性

### 新增搜图供应商

内置供应商在 `BUILTIN_PROVIDERS` 字典中添加信息，在 `BUILTIN_SCENE_MAP` 中添加场景映射，实现 `_call_xxx` 方法，在 `_call_provider` 中添加分发分支。
自定义供应商无需修改代码，用户直接在 WebUI 配置页的 `custom_providers` JSON 编辑器中添加即可。配置项遵循 `CUSTOM_PROVIDER_SPEC` 定义的接口规范，通过通用 `_call_custom` 方法自动处理。

### 新增视觉理解模式

在 `VisionTool._build_system_prompt` 中添加新的模式分支，在参数定义的 `enum` 中添加新模式名称，在配置 Schema 中添加对应的模型配置项。

### 新增图片理解能力

在 `tools/image_analyzer/` 下创建新的工具模块，继承 `BaseTool` 实现业务逻辑，在 `__init__.py` 中导出，在 `main.py` 中创建对应的 FunctionTool 子类并注册。新工具可通过 `CateyeServices` 获得预处理、缓存和上下文服务。

### 新增场景预设

通过 `nkit_ce_scene(action="update")` 动态添加，或修改 `scene_preset_tool.py` 中的 `BUILTIN_PRESETS` 字典添加内置预设。

### 新增 Context 后端

实现 `MemoryBridge` 协议，在 `Main._init_cateye_tools` 中根据 `context_backend` 配置创建桥接实例，传入 `ImageContextManager`。

---

## 平台配置建议

使用 CatEye 工具集时，建议在 AstrBot WebUI 中将 **图片转述模型**（`provider_settings.default_image_caption_provider_id`）留空。原因：

1. **避免双重视觉调用**：AstrBot 框架的图片转述功能会在用户发送图片时自动调用视觉模型生成文字描述，然后清空原始图片 URL。如果同时启用了 CatEye 的 `nkit_ce_vision`，同一张图片会被两个视觉模型重复处理，导致响应延迟和 Token 浪费
2. **保留原始图片**：框架转述后会清空 `req.image_urls`，导致多模态 LLM 无法直接看到图片。CatEye 的 `nkit_ce_vision` 通过独立的视觉模型调用处理图片，不依赖框架的转述机制
3. **按需调用更灵活**：CatEye 的视觉理解由 AI 按需调用，只在用户明确需要图片分析时才消耗视觉模型资源；框架转述则对每张图片都无条件触发

配置路径：AstrBot WebUI → 提供商设置 → 图片转述模型 → 留空

---

## 未来方向

- 内部评价体系：基于历史评价数据为智能体提供工具组合偏好参考
- 场景预设自动推荐：根据历史评价数据和场景匹配度自动推荐最优预设
- 跨图片关联分析：支持多张图片的 Context 条目关联检索
