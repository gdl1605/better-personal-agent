# 微信记录基础凭据脱敏脚本

更新时间：2026-10-12。当前已实现的独立工具：[redact_wechat.py](../scripts/redact_wechat.py)。

首轮优先筛选微信聊天记录。Codex 与 ChatGPT 记录按本轮用户选择暂不处理。脚本使用 Python 标准库，支持选定的 JSON、Markdown 和 UTF-8 文本；输出独立副本，原件继续保存。

## 使用

```bash
python3 scripts/redact_wechat.py '/你的微信记录目录' --output '/你的脱敏副本目录'
```

也可选择多个目录，并保留同一来源根下的结构：

```bash
python3 scripts/redact_wechat.py '/个人库/inbox/wechat/wechat-use' '/个人库/sources/wechat/conversations' --source-root '/个人库' --output '/个人库/exports/wechat-redacted'
```

source-root 只用于相对路径，不扩大读取范围。输出不能覆盖原件。图片、音视频与聊天数据库不由这份基础文本脚本解析。

## 当前识别范围

- JSON 中明确的密码、API key、微信 AES/解密密钥、授权 Cookie 和访问令牌字段。
- 消息文字里的 XML 属性与标签、嵌套或转义 JSON、带明确字段名的赋值和中文密码表达。
- Bearer/Basic 授权值、私钥块、已识别的服务密钥与 JWT 格式。
- URL 中明确的凭据参数、访问签名和账号密码连接串。

秘密值替换为 `[REDACTED_CREDENTIAL]`。普通业务 key、模型 token 数量与明确占位符继续保留；姓名、金额、发送者和时间等个人语义不做统一匿名化。

这是已列规则的第一遍筛选。未识别格式不自动认定为已脱敏；有实际遗漏时扩充对应规则，不增加笼统关键词门禁。

## 结果

输出目录中的 `redaction-report.json` 记录处理文件、替换位置数量、规则类型和具体失败文件，不记录秘密值。数量表示替换位置，同一凭据在原始 JSON 和来源视图中的多处表示分别计数。

未发现匹配的文件保留原始字节副本；发生替换的 JSON 保持数据结构与非凭据字段，文字只替换相应秘密值。某个文件读取或解析失败时继续处理其他文件，并报告实际错误。

现有 6 项直接测试覆盖凭据删除、消息身份与时间、XML/嵌套 JSON、URL/授权头/私钥、普通 key/token 保留，以及原件与报告行为：

```bash
python3 -m unittest discover -s tests -p 'test_redact_wechat.py' -v
```

后续迁入来源适配器时，在微信材料交给 Agent 阅读与打包上传之前调用同一处理逻辑。
