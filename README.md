# NekoKit

NekoKit 是一个 AstrBot 插件，为 AI 智能体提供开箱即用的工具集。工具既可以由 AI 根据对话内容按需自动调用，也可以通过 `nkit` 人工命令直接使用。

## 人工命令

所有人工命令都在 `nkit` 指令组下：

```text
/nkit help
/nkit tools
/nkit kv list [prefix]
/nkit kv get <key>
/nkit kv set <key> <value>
/nkit kv delete <key>
/nkit file list [prefix]
/nkit file save <key> <mode:path|text|base64> <payload> [--days N]
/nkit file get_path <key>
/nkit file get_url <key>
/nkit file delete <key>
```

`file save` 的 `mode` 需要显式填写：

```text
/nkit file save report:weekly.md path ./weekly.md --days -1
/nkit file save note:summary.txt text 会议纪要...
/nkit file save image:raw.png base64 iVBORw0KGgo...
```

`/nkit help` 查询所有人工命令，`/nkit tools` 查询提供给 LLM 的工具名和描述。命令版 KV Store 与 File Store 复用 LLM 工具的同一套数据隔离配置。

## 工具集一览

### KV 存储

轻量级持久化存储，让 AI 智能体可以记住和检索信息。

| 工具 | 功能 |
|------|------|
| `nkit_kv_get` | 读取存储的值 |
| `nkit_kv_set` | 写入或更新键值对 |
| `nkit_kv_delete` | 删除存储的值 |
| `nkit_kv_list` | 列出当前作用域下的所有键，可按前缀过滤 |

支持 AI 隔离（每个 AI 只能访问自己的数据）和会话隔离（数据仅在当前会话内可见），可在 WebUI 的“存储隔离”配置中灵活开关。

### 文件存储

持久化保存报告、图片、音频、CSV 等文件，支持返回 AstrBot 临时目录中的本地副本路径或 AstrBot 临时下载 URL。

| 工具 | 功能 |
|------|------|
| `nkit_file_save` | 保存或覆盖文件，可设置保留天数 |
| `nkit_file_get_path` | 复制已保存文件到 AstrBot 临时目录，并返回临时副本路径 |
| `nkit_file_get_url` | 获取已保存文件的临时下载 URL，访问一次后 URL 会被销毁 |
| `nkit_file_list` | 列出当前作用域下的文件 |
| `nkit_file_delete` | 删除已保存文件 |

文件保存在插件数据目录的 `file_store` 子目录中，隔离策略与 KV 存储共同使用 WebUI 的“存储隔离”配置。保存时可指定 `retention_days`，默认保留 7 天，`-1` 表示永久保留。`get_path` 会复制文件到 AstrBot 临时目录后返回副本路径，`get_url` 使用 AstrBot 文件服务生成临时链接，适合发送给用户下载。

### CatEye 图片识别

一站式图片理解工具集，覆盖文字提取、来源搜索、智能理解三大场景。核心工具内部自动完成预处理、缓存和上下文记录，AI 只需一次调用即可完成分析。

| 工具 | 功能 |
|------|------|
| `nkit_ce_ocr` | 从图片中提取文字（RapidOCR 引擎） |
| `nkit_ce_search` | 以图搜图（华为云 / trace.moe / SauceNAO / 自定义供应商） |
| `nkit_ce_vision` | 大模型图片理解（日常模式 / 专业模式） |
| `nkit_ce_scene` | 场景预设管理，返回工具组合策略 |

#### 内部机制

核心工具（OCR/Search/Vision）在执行时自动完成以下流程，AI 无需感知：

1. **缓存检查** -- 命中则直接返回结果
2. **图片预处理** -- 按任务类型优化尺寸和格式
3. **执行核心逻辑** -- OCR 识别 / 搜图 / 视觉理解
4. **缓存写入** -- 将结果写入缓存（48 小时生命周期）
5. **上下文记录** -- 将分析摘要写入图片认知上下文（7 天生命周期）

#### 图片认知上下文

每张图片的分析结果会自动积累到独立的上下文中。当 AI 再次分析同一张图片时，历史分析记录会作为上下文注入，使视觉模型能基于已有认知给出更精准的回答。

上下文存储后端可在 WebUI 中配置：
- **内置**（默认）：使用 NekoKit 内部存储
- **天使之魂**：委托天使之魂记忆插件管理，分析结果纳入已有记忆系统

#### 场景预设

内置 5 个场景预设，指导 AI 按步骤组合工具：

| 编码 | 名称 | 工具链路 |
|------|------|---------|
| `extract_text` | 文字提取 | ocr |
| `identify_character` | 角色识别 | search -> vision |
| `find_anime_source` | 番剧溯源 | search |
| `understand_meme` | 表情包解读 | vision |
| `analyze_chart` | 图片分析 | vision |

## 文档

| 文档 | 面向对象 | 内容 |
|------|---------|------|
| [KV 存储使用指南](docs/agent_guides/kv_store.md) | AI 智能体 | 工具 Schema、调用示例、最佳实践 |
| [文件存储使用指南](docs/agent_guides/file_store.md) | AI 智能体 | 文件保存、路径读取、临时 URL、最佳实践 |
| [CatEye 使用指南](docs/agent_guides/cateye.md) | AI 智能体 | 工具 Schema、场景预设、最佳实践 |
| [KV 存储设计文档](docs/design/kv_store.md) | 开发者 | 设计理念、命名空间策略、扩展性 |
| [文件存储设计文档](docs/design/file_store.md) | 开发者 | 文件持久化、索引布局、路径/URL 读取、安全边界 |
| [CatEye 设计文档](docs/design/cateye.md) | 开发者 | 内聚自动化、缓存管理、上下文机制、天使之魂适配 |
| [项目架构文档](docs/developer/architecture.md) | 开发者 | 分层架构、类图、数据流、扩展指南 |

## 未来计划

- **内部评价体系**：基于历史评价数据为智能体提供工具组合偏好参考
- **场景预设自动推荐**：根据历史评价数据和场景匹配度自动推荐最优预设
- **跨图片关联分析**：支持多张图片的 Context 条目关联检索
- **模式自动注入**：场景预设的 model_preference 自动传递给功能工具，AI 无需手动传 mode
- **更多 Context 后端**：支持更多外部记忆/知识库插件作为上下文存储后端
