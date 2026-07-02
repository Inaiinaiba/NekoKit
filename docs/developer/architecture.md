# NekoKit 项目架构文档

> 面向开发者。本文档描述 NekoKit 的分层架构、核心类关系、数据流和扩展指南。

## 目录结构

```
nekokit/
├── _conf_schema.json            # 插件配置 Schema，用于 WebUI 可视化配置
├── CHANGELOG.md                 # 版本更新日志
├── README.md                    # 架构概览（面向普通用户）
├── metadata.yaml                # 插件元数据（名称、版本、仓库等）
├── main.py                      # AstrBot 插件入口 + FunctionTool 注册
├── core.py                      # 核心抽象层
├── __init__.py                  # 包标识 + 版本号
├── requirements.txt             # 依赖
├── docs/
│   ├── agent_guides/
│   │   ├── kv_store.md          # KV 存储工具使用指南（面向 AI）
│   │   └── cateye.md            # 图片识别工具使用指南（面向 AI）
│   ├── design/
│   │   ├── kv_store.md          # KV 存储工具集设计文档
│   │   └── cateye.md            # CatEye 图片识别工具集设计文档
│   └── developer/
│       └── architecture.md      # 本文件 -- 项目架构文档
└── tools/
    ├── __init__.py              # 统一导出
    ├── kv_store/                # KV 存储子包
    │   ├── __init__.py          # 子包导出
    │   ├── context.py           # 上下文工具（获取 AI ID、会话 ID）
    │   ├── kv_store_tool.py     # KV 存储核心实现
    │   └── storage.py           # 存储后端（默认 JSON 文件，保留 SQLite 实现）
    └── image_analyzer/          # CatEye 图片识别子包
        ├── __init__.py          # 子包导出
        ├── _internal.py         # 内部共享工具（下载、预处理、哈希）
        ├── ocr_tool.py          # OCR 工具（RapidOCR）
        ├── image_search_tool.py # 以图搜图工具（内置 3 供应商 + 自定义供应商通用调用）
        ├── vision_tool.py       # 视觉理解工具
        ├── preprocess_tool.py   # 预处理工具（内部，不暴露给 AI）
        ├── cache_tool.py        # 缓存工具（内部，不暴露给 AI）
        ├── scene_preset_tool.py # 场景预设工具
        ├── cateye_services.py   # CateyeServices 依赖容器
        ├── image_context_manager.py # 图片认知上下文管理器
        ├── memory_bridge.py     # MemoryBridge 协议定义
        └── angel_memory_bridge.py   # 天使之魂记忆桥接实现
```

## 分层架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FunctionTool 层                              │  main.py
│  nkit_kv_get | nkit_kv_set | nkit_kv_delete | nkit_kv_list          │
│  nkit_ce_ocr | nkit_ce_search | nkit_ce_vision | nkit_ce_scene      │
├──────────────────────────────┬───────────────────────────────────────┤
│     KVStoreTool (BaseTool)   │     CatEye 工具集 (BaseTool)          │
│  业务逻辑、命名空间策略、     │  OCRTool | ImageSearchTool            │
│  配置管理                    │  VisionTool | ScenePresetTool          │
│                              │  内部工具：PreprocessTool             │
│                              │           CacheTool ──→ KVStoreTool   │
│                              │           ImageContextManager         │
│                              │  依赖容器：CateyeServices             │
│                              │  桥接协议：MemoryBridge               │
│                              │           AngelMemoryBridge           │
│                              │  共享 _internal                       │
├──────────────────────────────┼───────────────────────────────────────┤
│      StorageBackend          │           外部服务                     │
│     JSON 文件存储（默认）     │  RapidOCR | trace.moe | SauceNAO      │
│                              │  华为云 | AstrBot LLM                 │
│                              │  天使之魂 MemoryRuntime（可选）        │
└──────────────────────────────┴───────────────────────────────────────┘
```

### 各层职责

| 层次 | 文件 | 职责 |
|------|------|------|
| **FunctionTool** | `main.py` | 定义 8 个独立的 AstrBot FunctionTool，每个工具负责：参数校验、调用 BaseTool、结果转换 |
| **BaseTool - KV** | `tools/kv_store/kv_store_tool.py` | 实现 `KVStoreTool(BaseTool)`，包含核心业务逻辑：action 分发、命名空间构建、配置读取 |
| **BaseTool - OCR** | `tools/image_analyzer/ocr_tool.py` | 实现 `OCRTool(BaseTool)`，RapidOCR 文字识别 + 线程池异步，内化缓存和预处理 |
| **BaseTool - 搜图** | `tools/image_analyzer/image_search_tool.py` | 实现 `ImageSearchTool(BaseTool)`，多供应商搜图 + 场景自动选择，内化缓存和预处理 |
| **BaseTool - 视觉** | `tools/image_analyzer/vision_tool.py` | 实现 `VisionTool(BaseTool)`，双模式大模型视觉理解，内化缓存、预处理和上下文注入 |
| **BaseTool - 场景** | `tools/image_analyzer/scene_preset_tool.py` | 实现 `ScenePresetTool(BaseTool)`，内部调用 KVStoreTool 管理场景预设 |
| **BaseTool - 预处理** | `tools/image_analyzer/preprocess_tool.py` | 实现 `PreprocessTool(BaseTool)`，按任务类型预设参数表优化图片（内部工具） |
| **BaseTool - 缓存** | `tools/image_analyzer/cache_tool.py` | 实现 `CacheTool(BaseTool)`，内部调用 KVStoreTool 管理缓存（内部工具） |
| **依赖容器** | `tools/image_analyzer/cateye_services.py` | `CateyeServices` 数据类，统一注入 preprocess/cache/context 服务 |
| **Context 管理** | `tools/image_analyzer/image_context_manager.py` | `ImageContextManager`，管理图片认知上下文，7 天 TTL，支持桥接后端 |
| **桥接协议** | `tools/image_analyzer/memory_bridge.py` | `MemoryBridge` Protocol，定义外部记忆系统接入接口 |
| **天使之魂桥接** | `tools/image_analyzer/angel_memory_bridge.py` | `AngelMemoryBridge`，桥接到天使之魂记忆插件的 MemoryRuntime |
| **StorageBackend** | `tools/kv_store/storage.py` | 实现 `JSONStorageBackend` 和 `SQLiteStorageBackend`，当前默认使用 JSON 文件存储 |
| **Core 抽象** | `core.py` | 定义 `StorageBackend`、`NamespaceStrategy`、`BaseTool`、`ToolResult` 等抽象基类 |
| **Context** | `tools/kv_store/context.py` | 从 AstrBot 运行时上下文提取 AI ID 和会话 ID |
| **Internal** | `tools/image_analyzer/_internal.py` | 图片下载、预处理、哈希计算、Base64 编码等共享工具 |

## 核心类图

```
StorageBackend (ABC)        NamespaceStrategy (ABC)        BaseTool (ABC)
    ├── JSONStorageBackend       └── DefaultNamespaceStrategy   ├── KVStoreTool
    └── SQLiteStorageBackend                                  │
                                                               ├── OCRTool ──→ CateyeServices
                                                               ├── ImageSearchTool ──→ CateyeServices
                                                               ├── VisionTool ──→ CateyeServices
                                                               ├── ScenePresetTool ──→ KVStoreTool
                                                               ├── PreprocessTool
                                                               └── CacheTool ──→ KVStoreTool

CateyeServices (dataclass)
    ├── preprocess: PreprocessTool
    ├── cache: CacheTool
    └── context: Optional[ImageContextManager]

ImageContextManager
    ├── _kv_tool: KVStoreTool
    ├── _bridge: Optional[MemoryBridge]
    └── ImageContext / KnowledgeEntry

MemoryBridge (Protocol)
    └── AngelMemoryBridge ──→ MemoryRuntime (天使之魂)

ToolResult
  ├── success
  ├── message
  └── data

FunctionTool (AstrBot)
    ├── KVGetTool      ──→  KVStoreTool.execute(action="get")
    ├── KVSetTool      ──→  KVStoreTool.execute(action="set")
    ├── KVDeleteTool   ──→  KVStoreTool.execute(action="delete")
    ├── KVListTool     ──→  KVStoreTool.execute(action="list")
    ├── CateyeOCRTool      ──→  OCRTool.execute()
    ├── CateyeSearchTool   ──→  ImageSearchTool.execute()
    ├── CateyeVisionTool   ──→  VisionTool.execute()
    └── CateyeSceneTool    ──→  ScenePresetTool.execute() ──→ KVStoreTool
```

## 双层抽象设计

NekoKit 采用**双层抽象**模式组织所有工具：

- **BaseTool 层**（`core.py` 定义抽象基类）：实现业务逻辑，不依赖 AstrBot 框架细节。每个工具继承 `BaseTool`，实现 `get_name()`、`get_description()`、`get_parameters()` 和 `async execute(**kwargs)` 方法。
- **FunctionTool 层**（`main.py` 中定义）：适配 AstrBot 框架，负责参数校验、上下文注入、结果转换。通过 `create_with_tool()` 工厂方法将 BaseTool 包装为 FunctionTool。

核心工具（OCR/Search/Vision）通过 `CateyeServices` 依赖容器获得预处理、缓存和上下文服务，这些横切关注点在 BaseTool 层内部自动完成，对 FunctionTool 层透明。

## 数据流

### KV 存储数据流

```
AI 调用 (nkit_kv_get/nkit_kv_set/nkit_kv_delete/nkit_kv_list)
        │
        ▼
FunctionTool.call(context, **kwargs)
        │
        ▼
KVStoreTool.set_context(context)
KVStoreTool.execute(action=..., **kwargs)
        │
        ├── 1. 从 context 提取 ai_id, session_id
        ├── 2. 从 config 读取 ai_isolation, session_scope
        ├── 3. 构建 namespace
        └── 4. 调用 StorageBackend 执行操作
                │
                ▼
        JSON 文件 (data/nekokit/kvstore_*.json)
```

### CatEye 核心工具数据流

核心工具内部自动执行缓存检查、预处理、核心逻辑、缓存写入和上下文记录：

```
AI 调用 nkit_ce_ocr/nkit_ce_search/nkit_ce_vision
        │
        ▼
FunctionTool.call(**kwargs)
        │
        ▼
BaseTool.execute(**kwargs)
        │
        ├── 1. 缓存检查：CacheTool.check(image_url, task_type)
        │       └── 命中 → 直接返回缓存结果
        ├── 2. 图片预处理：PreprocessTool.execute(image_url, task_type)
        ├── 3. 执行核心逻辑（OCR/搜图/视觉理解）
        │       └── 外部服务（RapidOCR / trace.moe / SauceNAO / 华为云 / AstrBot LLM）
        ├── 4. 缓存写入：CacheTool.store(image_url, task_type, result)
        │       └── 内部 KVStoreTool → StorageBackend → cateye_internal_*.json
        └── 5. 上下文记录：ImageContextManager.add_knowledge(...)
                └── 内部 KVStoreTool 或 MemoryBridge → 天使之魂
```

### CatEye 缓存数据流

CacheTool 内部持有 KVStoreTool 引用，缓存键格式为 `cat_eye:cache:{image_hash}_{task_type}`：

```
CacheTool.execute(action="check", image_url=..., task_type="ocr")
        │
        ├── 1. 下载图片，计算 MD5 + dHash
        ├── 2. 生成 cache_key = "cat_eye:cache:{image_hash}_ocr"
        └── 3. 调用 self._kv_tool.execute(action="get", key=cache_key)
                │
                ▼
        内部 KVStoreTool → StorageBackend → cateye_internal_*.json
```

### CatEye Context 数据流

ImageContextManager 管理图片认知上下文，存储键格式为 `cat_eye:ctx:{image_hash}`：

```
ImageContextManager.add_knowledge(image_url, source, content)
        │
        ├── bridge 存在 → MemoryBridge.add_knowledge(...)
        │       └── AngelMemoryBridge → 天使之魂 MemoryRuntime.remember()
        │               检索时使用 chained_recall()（自然语言 query + entities 精确匹配）
        └── bridge 不存在 → 内部 KVStoreTool
                ├── _get_or_create_context(image_url, image_hash)
                └── _save_context(ctx)
                        └── StorageBackend → cateye_internal_*.json
```

### CatEye 场景预设数据流

```
AI 调用 nkit_ce_scene(action="get", scene_code="extract_text")
        │
        ▼
ScenePresetTool.execute()
        │
        ├── 1. 检查 BUILTIN_PRESETS 字典
        ├── 2. 若为内置预设，检查 kv_store 是否有自定义覆盖
        └── 3. 若为自定义预设，调用 self._kv_tool.execute(action="get", key="cat_eye:scene:{code}")
                │
                ▼
        内部 KVStoreTool → StorageBackend → cateye_internal_*.json
```

## 配置加载流程

```
AstrBot 启动
    │
    ├── 检测 _conf_schema.json → 生成 data/config/nekokit_config.json
    ├── 实例化 Main(context, config)
    │       │
    │       ├── 初始化公开 KVStoreTool → kvstore_*.json
    │       ├── 初始化内部 KVStoreTool → cateye_internal_*.json
    │       ├── 读取 config.kv_store → ai_isolation / session_scope
    │       ├── 仅对公开 KVStoreTool 应用用户隔离配置
    │       │
    │       └── _init_cateye_tools(config)
    │               │
    │               ├── 构建 cateye_config（合并 cateye_general / cateye_ocr / cateye_search / cateye_vision 配置）
    │               ├── 构建 proxy_config（合并 network_proxy 配置）
    │               │
    │               ├── 初始化 PreprocessTool（传入 config）
    │               ├── 初始化 CacheTool（传入 config + 内部 kv_tool）
    │               │
    │               ├── 根据 context_backend 配置创建桥接实例
    │               │   ├── "angel_memory" → _create_angel_memory_bridge()
    │               │   │   └── 查找已加载的天使之魂插件 → AngelMemoryBridge
    │               │   └── "internal" 或降级 → bridge=None
    │               │
    │               ├── 初始化 ImageContextManager（传入 kv_tool + bridge）
    │               │
    │               ├── 构建 CateyeServices(preprocess, cache, context)
    │               │
    │               ├── 初始化 OCRTool（传入 config + services）
    │               ├── 初始化 ImageSearchTool（传入 config + proxy_config + services）
    │               ├── 初始化 VisionTool（传入 config + star_context + services）
    │               └── 初始化 ScenePresetTool（传入 config + kv_tool）
    │
    └── 注册 8 个工具至 AstrBot
```

## 扩展指南

### 新增工具集

NekoKit 采用子包模式组织工具集，每个工具集是一个独立的子包目录。

1. 在 `tools/` 下创建新的子包目录（如 `tools/new_toolset/`）
2. 在子包中创建 `__init__.py` 和工具模块，继承 `BaseTool` 实现业务逻辑
3. 如有内部共享逻辑，创建 `_internal.py` 模块
4. 如需依赖注入，创建类似 `CateyeServices` 的数据类容器
5. 在 `main.py` 中导入新工具，创建对应的 FunctionTool 子类
6. 在 `Main.__init__` 中初始化新工具
7. 在 `Main._register_tools()` 中添加注册
8. 在 `_conf_schema.json` 中添加配置项
9. 在 `docs/agent_guides/` 中添加 AI 使用指南
10. 在 `docs/design/` 中添加设计文档

### 子包结构模板

```
tools/new_toolset/
├── __init__.py          # 导出工具类
├── _internal.py         # 内部共享工具（可选）
├── services.py          # 依赖容器（可选）
├── tool_a.py            # 工具 A 实现
└── tool_b.py            # 工具 B 实现
```

### 新增搜图供应商

内置供应商在 `BUILTIN_PROVIDERS` 字典中添加信息，在 `BUILTIN_SCENE_MAP` 中添加场景映射，实现 `_call_xxx` 方法，在 `_call_provider` 中添加分发分支。
自定义供应商无需修改代码，用户直接在 WebUI 配置页的 `custom_providers` JSON 编辑器中添加即可。配置项遵循 `CUSTOM_PROVIDER_SPEC` 定义的接口规范。供应商接口规范包含以下字段：

- `key` / `name` / `url`：必填，标识、显示名、API 端点
- `method` / `content_type`：请求方式（GET/POST）和格式（form/json）
- `image_field` / `image_encoding`：图片字段名和编码（binary/base64）
- `headers` / `params` / `body`：自定义请求头和参数，支持 `{api_key}` 占位符
- `response_path` / `result_mapping`：响应解析路径和字段映射
- `api_key` / `scene`：认证信息和适用场景

通用 `_call_custom` 方法根据上述配置自动处理请求构建和响应解析，无需为每个新供应商编写专用方法。

### 新增 Context 后端

1. 在 `memory_bridge.py` 中 `MemoryBridge` 协议已定义接口
2. 创建新的桥接类实现 `MemoryBridge` 协议
3. 在 `Main._init_cateye_tools` 中根据 `context_backend` 配置创建桥接实例
4. 将桥接实例传入 `ImageContextManager`
