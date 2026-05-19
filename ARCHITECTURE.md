# NekoKit 架构概览

NekoKit 是一个 AstrBot 插件，为 AI 智能体提供开箱即用的工具集。所有工具由 AI 根据对话内容按需自动调用，用户无需手动操作。

## 工具集一览

### 🗄️ KV 存储

轻量级持久化存储，让 AI 智能体可以记住和检索信息。

| 工具 | 功能 |
|------|------|
| `get_kv` | 读取存储的值 |
| `set_kv` | 写入或更新键值对 |
| `delete_kv` | 删除存储的值 |
| `list_kv` | 列出当前作用域下的所有键 |

支持 AI 隔离（每个 AI 只能访问自己的数据）和会话隔离（数据仅在当前会话内可见），可在 WebUI 配置中灵活开关。

### 🐱 Cateye 图片识别

一站式图片理解工具集，覆盖文字提取、来源搜索、智能理解三大场景。

| 工具 | 功能 |
|------|------|
| `cateye_ocr` | 从图片中提取文字 |
| `cateye_search` | 以图搜图（华为云 / trace.moe / SauceNAO） |
| `cateye_vision` | 大模型图片理解（日常模式 / 专业模式） |
| `cateye_preprocess` | 按任务类型优化图片尺寸和格式 |
| `cateye_cache` | 检查/存储缓存，避免重复调用 |

核心工具（OCR / 搜图 / 视觉）内部自动执行缓存检查和图片预处理，无需手动组合。辅助工具（预处理 / 缓存）独立暴露，供 AI 在需要时主动调用。

## 📚 更多文档

| 文档 | 面向对象 | 内容 |
|------|---------|------|
| [KV 存储使用指南](docs/agent_guides/kv_store.md) | AI 智能体 | 工具 Schema、调用示例、最佳实践 |
| [Cateye 使用指南](docs/agent_guides/cateye.md) | AI 智能体 | 工具 Schema、调用示例、组合策略 |
| [KV 存储设计文档](docs/design/kv_store.md) | 开发者 | 设计理念、命名空间策略、扩展性 |
| [Cateye 设计文档](docs/design/cateye.md) | 开发者 | 工具解耦、缓存机制、供应商抽象 |
| [项目架构文档](docs/developer/architecture.md) | 开发者 | 分层架构、类图、数据流、扩展指南 |
