# KV 存储工具集设计文档

## 设计目标

为 AI 智能体提供轻量级持久化存储能力。智能体在多轮对话中常常需要记住用户的偏好、任务进度或中间结果，KV 存储工具集让 AI 能够像使用便签本一样，随时存取关键信息，且数据在重启后依然保留。

---

## 核心理念

### 命名空间隔离

数据隔离是 KV 存储的基石。我们设计了双层隔离机制：

- **AI 隔离**：每个 AI 智能体只能访问自己存储的数据，不同 AI 之间完全隔离，避免数据串扰
- **会话隔离**：可选开启，开启后数据仅在当前对话会话内可见，关闭时同一 AI 的所有会话共享数据

命名空间的构建遵循 `ai_id` + `session_id` 的组合策略，由 `NamespaceStrategy` 抽象类统一管理。隔离策略由管理员在 WebUI 的 `KV 存储` 配置分组中配置，AI 工具调用时无法修改，确保安全边界不被突破。

### 双层抽象

工具集采用 BaseTool 业务层 + FunctionTool 框架适配层的双层架构：

- **BaseTool 层**（`KVStoreTool`）：承载核心业务逻辑，包括 action 分发、命名空间构建、配置读取。不依赖任何框架特定类型，可独立测试
- **FunctionTool 层**（`KVGetTool` 等 4 个类）：负责与 AstrBot 框架对接，处理参数校验、上下文提取、结果格式转换。每个 FunctionTool 持有同一个 `KVStoreTool` 实例

这种分离使得业务逻辑与框架解耦，未来更换框架或复用业务逻辑时只需重写 FunctionTool 层。

### 存储后端可插拔

通过 `StorageBackend` 抽象基类定义统一接口（get/set/delete/list_keys/search/clear_namespace），当前默认实现为 `JSONStorageBackend`，另保留 `SQLiteStorageBackend` 可供后续切换。如需切换为 Redis、LevelDB 等后端，只需实现该接口并在工厂函数中注册即可，上层业务代码无需任何改动。

---

## 工具拆分策略

早期版本采用单工具 + `action` 参数的模式（一个 `kv_store` 工具通过 action 区分 get/set/delete/list），存在以下问题：

- **参数定义模糊**：不同 action 需要不同参数（get 需要 key，set 需要 key+value，list 不需要参数），但 JSON Schema 必须合并声明，导致 AI 在调用时难以判断哪些参数是必填的
- **工具描述冗长**：四种操作的说明挤在一个 description 中，AI 理解成本高
- **选择效率低**：AI 需要先选择工具再选择 action，增加了决策链路

拆分为 4 个独立工具后：

| 工具 | 职责 | 必填参数 |
|------|------|----------|
| `nkit_kv_get` | 读取单个键值 | key |
| `nkit_kv_set` | 写入或更新键值对 | key, value |
| `nkit_kv_delete` | 删除指定键值 | key |
| `nkit_kv_list` | 列出当前作用域所有键 | 无 |

每个工具职责单一、参数明确，AI 可以直接根据意图选择对应工具，减少决策错误。

---

## 数据流简述

1. AI 发起工具调用（如 `nkit_kv_set(key="name", value="Alice")`）
2. FunctionTool 从运行时上下文提取 `ai_id` 和 `session_id`
3. 将上下文注入 `KVStoreTool`，由其根据配置构建命名空间
4. `KVStoreTool` 调用 `StorageBackend` 在对应命名空间下执行操作
5. 操作结果封装为 `ToolResult`，经 FunctionTool 转换为字符串返回给 AI

整个流程中，命名空间的构建对 AI 透明——AI 无需关心隔离策略，所有隔离逻辑在业务层自动完成。

---

## 扩展性设计

### 新增存储后端

继承 `StorageBackend`，实现六个抽象方法，然后在工厂函数中注册即可。推荐在 `tools/kv_store/storage.py` 中添加新实现。

### 新增命名空间策略

继承 `NamespaceStrategy`，实现 `build` 和 `describe` 方法。例如可以添加基于用户 ID 的隔离策略，实现同一 AI 下不同用户的数据隔离。

### 新增工具操作

在 `KVStoreTool.execute` 中添加新的 action 分支，然后在 `main.py` 中创建对应的 FunctionTool 子类并注册。例如添加 `nkit_kv_search` 工具，支持按键名关键词模糊搜索。

### 子包模式

当前 KV 存储工具集已重构为 `tools/kv_store/` 子包结构，与 Cateye 工具集的 `tools/image_analyzer/` 子包结构一致。新增工具集时，只需在 `tools/` 下创建新的子包目录，遵循相同的 BaseTool + FunctionTool 双层模式即可。
