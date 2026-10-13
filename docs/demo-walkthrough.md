# 示例使用流程与已观察结果

日期：2026-10-12。示例全部为虚构资料，输入在 examples/，没有使用个人真实聊天或账单。

通过真实 CLI 创建工作区，启用 ChatGPT 和微信，按标题发现会话，再选择微信会话与日期范围。接入后得到 3 份材料（含本人初始填写），微信凭据在阅读副本中被替换。

本轮 Agent 实际读取生成的三份来源，区分本人阶段安排、另一分支的助手建议和朋友观点；提交真实阅读回执，更新目标页和当前认识页。随后通过 feedback 提交新的本人确认，同一章节替换，旧表达保留为来源；再次读取确认并记录进度。

结果：当前 4 份来源版本已记录阅读，待阅读为 0；当前阶段安排由“暂停项目”更新为“课程作业已完成，恢复项目”，工作日晚间加班的原有约束继续保留。dot 发布入口与包含工具的包均实际生成。

当前工作区是临时演示位置 /private/tmp/personal-agent-demo-20261012-01a12f27。来源解析、CLI、回执和 Wiki 回写由当前实现运行；本次语义理解由本轮 Agent 完成，没有派发外部 dot 消息或实际上传云端。

普通用户可复现：

```bash
python3 personal-agent --workspace /tmp/my-personal-agent-demo init --name 示例用户 --goal 在课余时间推进项目
python3 personal-agent --workspace /tmp/my-personal-agent-demo sources chatgpt --enable --path examples/chatgpt.json --share sources
python3 personal-agent --workspace /tmp/my-personal-agent-demo sources wechat --enable --path examples/wechat.json --share sources
python3 personal-agent --workspace /tmp/my-personal-agent-demo collect
python3 personal-agent --workspace /tmp/my-personal-agent-demo digest
```

把 digest 返回的 instructions 交给 dot 或当前 Agent，实际阅读、更新 Wiki 后提交其真实 response；再运行 dot 和 export。不得用模板里的示例回执代替阅读。
