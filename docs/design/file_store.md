# 文件存储工具集设计文档

## 设计目标

File Store 为 AI 智能体提供文件级持久化能力，补足 KV Store 只适合短文本和配置片段的边界。它用于保存报告、图片、音频、CSV、导出结果等文件，并提供本地路径与临时下载 URL 两种读取方式。

工具集遵循 NekoKit 现有模式：

- **FunctionTool 层**：在 `main.py` 中暴露 `nkit_file_*` 独立工具。
- **BaseTool 层**：`FileStoreTool` 负责 action 分发、命名空间构建和业务校验。
- **Storage 层**：`FileStorageBackend` 负责索引、blob 文件、列表和删除。

---

## 工具拆分

| 工具 | 职责 | 必填参数 |
|------|------|----------|
| `nkit_file_save` | 保存或覆盖文件 | `key`，以及 `source_path` / `content` / `content_base64` 三选一 |
| `nkit_file_get_path` | 复制文件到 AstrBot 临时目录，并返回副本路径 | `key` |
| `nkit_file_get_url` | 获取临时下载 URL，访问一次后 URL 会被销毁 | `key` |
| `nkit_file_list` | 列出当前作用域文件 | 无 |
| `nkit_file_delete` | 删除文件 | `key` |

拆分为独立工具后，AI 不需要先选择 action，再判断参数组合；取路径和取 URL 也不会混淆。

---

## 输入解析

文件保存的输入适配放在 FunctionTool 层，尽量复用识图系列的路径解析逻辑。`nkit_file_save` 支持：

- 显式 `source_path`：支持网络地址或本地路径，包括 `http/https` URL、本地绝对路径、当前会话 workspace 下的相对路径。沙箱中的文件需先取回到本地路径后再传入。
- 显式 `content`：直接保存 UTF-8 文本。
- 显式 `content_base64`：直接保存 base64 内容。
解析完成后，BaseTool 层只接收明确提供的本地 `source_path` 或直接内容，保持业务层和框架输入细节解耦。

---

## 持久化布局

插件入口使用：

```python
self.data_dir = str(StarTools.get_data_dir("nekokit"))
```

File Store 根目录为：

```text
{self.data_dir}/file_store/
```

内部结构：

```text
file_store/
├── indexes/
│   └── {namespace_id}.json
└── blobs/
    └── {namespace_id}/
        └── {filename} 或 {file_id}{suffix}
```

索引记录 key、文件名、blob 名称、大小、创建时间、更新时间、保留天数和过期时间。通过 `source_path` 保存时，实际 blob 文件名尽量保留来源文件名；如果当前命名空间目录下已经存在同名文件，则追加数字后缀。通过 `content` 或 `content_base64` 直接保存内容时，实际 blob 文件名使用 `sha256(namespace + key)` 生成的稳定 file_id，避免路径穿越、特殊字符和 key 碰撞。

---

## 保留时间与清理

`nkit_file_save` 支持可选参数 `retention_days`：

- 不传时默认 7 天。
- `-1` 表示永久保留。
- 其他非负整数表示保留天数，单位为天。

每次触发任意文件工具时，`FileStoreTool.execute` 都会调用存储层清理检查。存储层通过 `cleanup_state.json` 记录当天是否清理过，因此每天最多实际扫描清理一次。

读取和列表会跳过已过期条目，即使当天清理已经执行过、过期文件尚未从磁盘删除，也不会继续向 AI 暴露。

---

## 命名空间

File Store 复用 KV Store 的隔离配置：

- `ai_isolation`：默认开启，不同 AI 人格文件互不可见。
- `session_scope`：默认关闭，开启后同一 AI 的不同会话文件互不可见。

实现复用 `DefaultNamespaceStrategy`、`get_ai_id` 和 `get_session_id`。管理员通过 WebUI 的“存储隔离”配置组控制 KV 与文件存储的隔离行为，AI 工具调用不能覆盖。

---

## 读取方式

### get_path

`nkit_file_get_path` 从持久化 blob 复制一份文件到 AstrBot 临时目录（`get_astrbot_temp_path()`）的 `nekokit/` 子目录，并返回临时副本的本地绝对路径。该路径用于插件内部或其他工具继续处理，不注册 AstrBot 文件服务；长期引用仍应保存 key，而不是保存临时路径。

### get_url

`nkit_file_get_url` 使用 AstrBot 文件服务生成临时下载 URL：

```python
from astrbot.core import astrbot_config, file_token_service

callback_host = astrbot_config.get("callback_api_base")
token = await file_token_service.register_file(path)
url = f"{str(callback_host).removesuffix('/')}/api/file/{token}"
```

基于 AstrBot 当前实现，`register_file` 返回 token 而非完整 URL；默认过期时间约 300 秒，且 `handle_file` 会在使用后弹出 token。因此 `get_url` 返回值只适合作为临时下载链接，访问一次后 URL 会被销毁。

---

## 安全边界

- key 不会直接成为文件路径。
- blob 路径会在写入、读取和删除前检查是否仍位于 namespace blob 目录下。
- `save` 读取 `source_path` 时要求源路径存在且是普通文件。
- `get_url` 在 `callback_api_base` 未配置时返回失败。
- 删除只删除当前命名空间索引中 key 对应的 blob 文件。

---

## 后续扩展

- 独立的 `file_store` 配置分组，例如单文件大小上限、总目录大小上限、是否允许 `content_base64`。
- 文件 MIME 类型探测与返回。
- 按更新时间、大小或 key 模糊搜索。
- 清理不存在 blob 的索引项，或清理未被索引引用的孤儿 blob。
