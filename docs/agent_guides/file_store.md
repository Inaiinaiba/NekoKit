# File Store 工具 -- 智能体使用指南

---

## 一、工作原则

1. **文件用 File Store，文本记忆用 KV**：报告、图片、音频、导出文件等二进制或长文本文件存到 File Store；短配置和备忘仍用 KV。
2. **稳定 key**：使用冒号分层命名，例如 `report:weekly.md`、`image:avatar.png`、`export:orders.csv`。
3. **按需取 URL**：`nkit_file_get_url` 返回临时下载链接，只在需要给用户下载时调用；长期引用请保存 key。
4. **路径优先给工具用**：后续工具需要本地文件时使用 `nkit_file_get_path`，它会返回 AstrBot 临时目录中的副本路径；不要把临时 URL 当成本地路径。

---

## 二、工具速查

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `nkit_file_save` | 保存或覆盖文件 | `key`，以及 `source_path` / `content` / `content_base64` 三选一；可选 `retention_days` |
| `nkit_file_get_path` | 复制文件到 AstrBot 临时目录，并返回副本路径 | `key` |
| `nkit_file_get_url` | 获取临时下载 URL，访问一次后 URL 会被销毁 | `key` |
| `nkit_file_list` | 列出当前作用域文件 | 可选 `prefix` |
| `nkit_file_delete` | 删除文件 | `key` |

---

## 三、典型用法

### 保存已有文件

```text
nkit_file_save(key="report:weekly.md", source_path="/path/to/weekly.md")
```

`source_path` 支持网络地址或本地路径。注意：沙箱中的文件需先取回到本地路径后再传入。

支持范围与识图系列一致：

- `http/https` URL
- 本地绝对路径
- 当前会话 workspace 下的相对路径

### 直接保存文本

```text
nkit_file_save(key="note:summary.txt", content="会议纪要...")
```

### 设置保留时间

默认保留 7 天。需要永久保留时使用 `retention_days=-1`：

```text
nkit_file_save(key="report:archive.md", source_path="/path/to/archive.md", retention_days=-1)
```

### 给用户下载

```text
nkit_file_get_url(key="report:weekly.md")
```

返回的 URL 是 AstrBot 文件服务生成的临时链接，访问一次后 URL 会被销毁；未访问也可能过期。

### 给其他工具继续处理

```text
nkit_file_get_path(key="image:input.png")
```

返回 AstrBot 临时目录下的本地绝对路径，用于 AstrBot 内部共享文件，适合传给图片识别、文件处理或其他插件内部逻辑。该路径是临时副本，长期引用请保存 key。

### 按前缀查看

```text
nkit_file_list(prefix="report:")
```

---

## 四、异常处理

- key 不存在时，`get_path`、`get_url` 和 `delete` 会返回失败。
- `save` 必须且只能提供 `source_path`、`content`、`content_base64` 之一。
- `retention_days` 单位是天，不填默认 7 天，`-1` 表示永久保留。
- 任意文件工具调用都会触发过期清理检查，但每天最多实际清理一次。
- `get_url` 依赖 AstrBot 的 `callback_api_base`；未配置时无法生成可下载 URL。
- URL 是临时访问能力，不要写入长期记忆中当永久链接使用。
