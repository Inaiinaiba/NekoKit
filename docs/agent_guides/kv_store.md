# KV Store 工具 -- 智能体使用指南

---

## 一、工作原则

1. **字符串存储**：`value` 按任意字符串使用，优先保存自然语言、备忘录、配置片段等纯文本内容
2. **先读后写**：更新数据前先 `nkit_kv_get` 读取当前值，在现有数据基础上修改后再 `nkit_kv_set` 写回，避免覆盖丢失
3. **键名规范**：使用冒号分层命名（如 `user:001:profile`、`task:shopping:orders`），便于 `nkit_kv_list` 浏览和按前缀识别

---

## 二、工具速查

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `nkit_kv_get` | 读取值 | `key` |
| `nkit_kv_set` | 写入/更新值 | `key`, `value`（任意字符串，key 已存在则覆盖） |
| `nkit_kv_delete` | 删除值 | `key` |
| `nkit_kv_list` | 列出当前作用域所有键 | 无参数 |

---

## 三、数据隔离

隔离模式由管理员在 WebUI 配置，agent 无法修改。

| 模式 | 默认 | 行为 |
|------|------|------|
| **AI 隔离** | 开启 | 每个 AI 只能访问自己存储的数据，不同 AI 之间完全隔离 |
| **会话隔离** | 关闭 | 开启后数据仅在当前会话内可见；关闭时同一 AI 的所有会话共享数据 |

默认配置（AI 隔离开启 + 会话隔离关闭）下：同一 AI 的所有会话共享数据，跨会话持久化可用。

---

## 四、典型用法

### 维护列表文本

用普通文本存储记录，通过 `nkit_kv_get` 读取 → 修改 → `nkit_kv_set` 写回：

```
nkit_kv_set(key="orders", value="PO-001 | iPhone | 8999")
nkit_kv_get(key="orders")
→ 读取后追加新记录，再 set 写回
```

### 键值配置存储

用文本存储配置项：

```
nkit_kv_set(key="config:display", value="theme=dark; lang=zh")
```

### 按前缀组织数据

```
nkit_kv_list()
→ ["user:001:profile", "user:002:profile", "task:shopping:orders"]
→ 按冒号前缀识别数据类别
```

---

## 五、异常处理

- 键不存在时 `nkit_kv_get` 返回失败，视为空数据初始化新结构
- `nkit_kv_set` 的 `value` 仅接受字符串；不要为了存储而主动构造 JSON
- 并发写入同一 key 可能覆盖，建议单次操作内完成读-改-写
