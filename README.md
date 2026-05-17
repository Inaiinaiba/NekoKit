# NekoKit 🐱

AstrBot 插件工具仓库，为 AI 智能体提供各种实用工具。

## 特性

- **清晰的分层架构**：抽象层、工具层、存储层完全解耦
- **可扩展的抽象基类**：`BaseTool`、`StorageBackend`、`NamespaceStrategy`
- **轻量级设计**：单一职责，高效执行
- **猫系风格**：轻松有趣的交互体验

## 安装

将 `nekokit` 目录放入 AstrBot 的插件目录即可。

## 架构概览

```
nekokit/
├── core.py                      # 核心抽象层（BaseTool, StorageBackend 等）
├── tools/                       # 工具实现层
│   ├── __init__.py             # 统一导出
│   ├── kv_store.py             # KV 存储工具
│   ├── storage.py              # 存储后端实现
│   └── context.py              # 上下文工具
├── main.py                      # AstrBot 插件入口
├── metadata.yaml                 # 元数据
└── README.md
```

## 工具列表

### kv_store

键值存储工具，支持 AI 隔离与会话隔离。

**功能**：
- `get`: 读取数据
- `set`: 写入数据
- `delete`: 删除数据
- `list`: 列出所有键
- `search`: 搜索键

**特性**：
- AI 隔离（默认开启）
- 会话隔离（可选）
- 支持 JSON 和 SQLite 两种存储模式

## 配置

在 AstrBot 插件配置中可以设置：
- `use_sqlite`: 是否使用 SQLite 存储（默认 false，使用 JSON 文件）

## 扩展新工具

1. 继承 `nekokit.core.BaseTool`
2. 实现必要的抽象方法
3. 在 `tools/` 下创建工具文件
4. 在 `main.py` 中注册（可使用 `FunctionTool` 包装器）

## 版本

0.0.2
