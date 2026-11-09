# 铲子 invoke 须进观察

对照对象：`shovel_search` 命中渲染（`observe/tool_digest._render_hits`）。  
目的：调用侧 LLM 必须看见文首 `invoke` 契约，避免猜 `-H/-P` 一类假 CLI。  
关联：[脚本铲子生命周期](../功能/脚本铲子生命周期/说明.md)。不等于放行议题。

## 缺口

`search_shovels` 的 `data.hits` 已含 `invoke`，但观察正文只渲 id/title/when_to_use/outcome/path，**裁掉 invoke**。规划提示仅点名铲子名 +「口令仅 env」，模型易编造参数。

## 已改

铲子类 hit（有 `outcome` / `when_to_use`）渲染行追加 `| invoke=…`；字段空则 `| invoke=(missing)`。自检：`demo_shovel_lifecycle`。

## 明确不做

- 不为某一把铲写专用提示词分支。
- 不把整份脚本源码默认灌进 hits（需要时再 `fs_read`）。
