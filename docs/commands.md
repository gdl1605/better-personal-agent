# 命令与运行协议

更新时间：2026-10-12。主职责：当前 0.1 的真实入口、配置操作、Agent 回执和传输协议。

从项目目录运行 `python3 personal-agent --workspace '/个人库路径' <命令>`，也可运行 `python3 -m personal_agent`。工作区独立于项目；默认位置是 `~/PersonalContext`。

## 创建与配置

```bash
python3 personal-agent -w ~/PersonalContext setup
python3 personal-agent -w ~/PersonalContext init --name '用户称呼' --timezone Asia/Shanghai --goal '当前目标'
python3 personal-agent -w ~/PersonalContext sources
python3 personal-agent -w ~/PersonalContext sources chatgpt --enable --path '/导出.zip' --share wiki
python3 personal-agent -w ~/PersonalContext sources chatgpt --discover
python3 personal-agent -w ~/PersonalContext sources chatgpt --include '实际会话ID' --from 2026-10-01
python3 personal-agent -w ~/PersonalContext sources wechat --enable --mode import --path '/微信快照目录' --share sources
python3 personal-agent -w ~/PersonalContext sources wechat --discover
python3 personal-agent -w ~/PersonalContext sources wechat --exclude '实际群聊ID'
python3 personal-agent -w ~/PersonalContext sources codex --enable --mode local
```

`--processing digest/archive` 决定是否交给处理 Agent 自动形成理解；`--share none/wiki/sources` 决定日常 dot 发布入口及云端包包含哪些内容。允许语义处理意味着该处理 Agent 可以读取选定材料。姓名、目标和其他初始化信息属于本人填写的来源。

关闭采集用 `--disable`；已有原件和认识继续保留。停止自动消化用 `--processing archive`。改变发布范围用 `--share`，再次执行 `dot` 更新发布入口。清空路径、日期和会话选择分别用 `--clear-paths`、`--clear-dates`、`--clear-includes`、`--clear-excludes`。

会话发现返回标题和 ID，dot 可以让用户按标题选择，再保存真实 ID。健康等普通文件按路径选择；日期筛选要求其逐条记录有可识别日期字段。

## 接入与消化

```bash
python3 personal-agent -w ~/PersonalContext collect
python3 personal-agent -w ~/PersonalContext collect --source wechat
python3 personal-agent -w ~/PersonalContext collect --source wechat --force
python3 personal-agent -w ~/PersonalContext digest
python3 personal-agent -w ~/PersonalContext digest --source chatgpt --limit 10
```

`--force` 用于实际解析器改动后的重新接入；平时根据文件和选择的变化增量处理。digest 返回 job、instructions 和 response_path。批次只影响本轮读取，其他材料继续留在队列。

Agent 实际读取 instructions、列出的来源及相关 Wiki，再写以下 JSON：

```json
{
  "reviews": [{
    "key": "来源的真实key",
    "revision": {"document": "队列中的真实路径", "edition": 1},
    "status": "done",
    "topics": ["当前主题"],
    "wiki_pages": ["wiki/关于用户/当前认识.md"],
    "note": "实际读到的个人含义或无新个人信息的结论"
  }],
  "wiki_updates": [{
    "path": "wiki/关于用户/当前认识.md",
    "content": "完整的更新后 Markdown 内容",
    "source_keys": ["实际采用的来源key"]
  }]
}
```

revision 原样使用当时读取的值，不取提交时的新版本。部分读完用 `partial` 并给 `next_read`。完整文字副本改变也会更新版本，即使回复摘录未变化。Wiki 更新可给 `base_content`；两端同时修改时保留另一份内容供合并。

```bash
python3 personal-agent -w ~/PersonalContext digest record '/实际回执.json'
python3 personal-agent -w ~/PersonalContext feedback --page 'wiki/想法与选择/目标与选择.md' --section '当前目标' --text '本人明确的新目标'
```

feedback 直接保存当前本人确认，同名章节替换，原表达作为来源保留。自动推断仍由 Agent 根据真实材料维护。

## dot 发布与同步

```bash
python3 personal-agent -w ~/PersonalContext dot
python3 personal-agent -w ~/PersonalContext export --output '/交付包.zip'
python3 personal-agent -w ~/PersonalContext export --upload
python3 personal-agent -w ~/PersonalContext export --receipt '/原生工具实际返回的回执.json'
python3 personal-agent -w ~/PersonalContext sync --incoming '/返回包.zip'
```

dot 生成按提供范围过滤的日常入口和实际命令。生成前将上次发布入口里的 Wiki 修改合并回主库，再更新已管理的发布文件；用户另外加入的文件保留。原生电脑访问权限由平台管理，发布路径不构成操作系统层面的原件隔离。

包内有工具、配置、Wiki、获准提供的来源和 QUICKSTART。云端从包内工具执行，修改后用 `export --return` 生成返回包。sync 按共同基线比较；一端变化则应用，两端变化则保留双方。删除不自动传播。

Wiki 页面通过 `source_keys` 记录依据。包含未选择交付来源的页面留在本地；无来源的非模板页面同样留在本地，Agent 可以补登记实际来源。Wiki-only 的材料不带全文，其来源身份保留用于确认回写。

## 可选运行适配器

```bash
python3 personal-agent -w ~/PersonalContext runtime --agent-command-json '["你的Agent封装程序"]'
python3 personal-agent -w ~/PersonalContext runtime --uploader-command-json '["你的上传封装程序"]'
python3 personal-agent -w ~/PersonalContext digest --run-agent
python3 personal-agent -w ~/PersonalContext maintain --run-agent --export
```

命令是参数数组，直接执行而不经过 shell。凭据由运行环境私密提供，不放到参数或上下文包中。

Agent 命令从 stdin 接收 job JSON，实际阅读后在 stdout 返回上面的 reviews/wiki_updates JSON。未配置时返回待 Agent 处理，不把任务生成当成已经阅读。stderr 属于该适配器自身的诊断。

上传命令从 stdin 接收 `{"kind":"personal_context_upload","bundle":"真实文件路径","id":"包ID"}`，执行实际传输后返回 `{"status":"uploaded","artifact_ref":"原生工具的真实持久引用"}`。原生工具直接上传时用 export --receipt 登记真实结果；artifact_ref 使用文件 ID 或资源引用，认证值留在传输层。

没有上传接口时包仍可用，状态为待交付。打包、上传、云端应用和语义阅读分别记录。剩余历史不阻止已有 Wiki 发布。

## 周期任务与状态

```bash
python3 personal-agent -w ~/PersonalContext schedule --daily-at 22:00
python3 personal-agent -w ~/PersonalContext schedule --confirm-ref 'dot保存后实际返回的任务引用'
python3 personal-agent -w ~/PersonalContext maintain
python3 personal-agent -w ~/PersonalContext status
```

schedule 生成给 dot 的自然语言请求；保存安排和平台任务存在分别记录。dot 根据本人选择调用原生 schedule 工具并读取结果后登记引用。取消选择用 `schedule --disable`，已有平台任务由 dot 取消并读取真实结果。

maintain 收取已启用来源、续接阅读并按显式参数导出或上传。即使历史尚未全部读完，也可发布当前有依据的 Wiki。通知按有理解变化、失败或需本人操作执行，无可行动变化保持安静。
