# 命令与运行协议

更新时间：2026-10-13。主职责：当前 0.1 的真实入口、配置操作、Agent 回执和传输协议。

用户在项目线程或已关联的 dot 中说“初始化 / 安装”即可，由 Agent 执行 [Bootstrap](../BOOTSTRAP.md)。以下命令是 Agent 的操作接口，也供手动使用。

从项目目录运行 `python3 personal-agent --workspace '/个人库路径' <命令>`，也可运行 `python3 -m personal_agent`。工作区独立于项目。省略路径时沿用 `.personal-agent/workspace.json` 中本项目的 Bootstrap 绑定，初次默认 `~/PersonalContext`；显式 `--workspace` 始终优先。

## Bootstrap

```bash
python3 personal-agent bootstrap
python3 personal-agent --workspace '/个人库路径' bootstrap --name '用户称呼' --goal '已明确的目标'
```

bootstrap 不读取 stdin，创建或恢复基础目录、保存项目绑定并返回 `instructions`、`entry`、`next_question`、实际来源选择、阅读进度和 `automation.tasks`。Agent 读取 instructions，复用已有答案并继续执行。`entry` 是个人库的 BOOTSTRAP.md，`instructions` 是本轮真实路径与选择的说明。初始化默认安排同步续读与独立校准，由当前 Agent 使用真实平台工具保存，不能把生成请求当成保存成功。重复启动保留配置、关闭选择和 Wiki；未选来源不启用。目标与其他资料可以随后补充，不阻止基础启动。

本人已选好首批来源后用 `bootstrap --sources-decided` 保存该问题已回答，包括先只用自述；它不改变来源启用或提供范围。再次启动时沿用已回答的选择。

setup 保留供用户主动在终端交互使用；Agent 自动入口使用 bootstrap。

## 首次认识与实际使用

Bootstrap 后由 dot 执行 `onboard` 并读取返回的 instructions。用户不用执行下面的命令或填写 JSON。当前步骤保存于 `state/onboarding.json`，中断后执行 onboard 即可续接；剩余未读历史不阻止首次使用。

```bash
python3 personal-agent -w ~/PersonalContext onboard
python3 personal-agent -w ~/PersonalContext onboard record '/实际交互记录.json'
```

每个 record 对应已发生的一步，工具返回下一步说明：

| stage | 实际记录 | 后续动作 |
| --- | --- | --- |
| configured | 保存完本人选择后，提供 `question`：当前具体问题，可暂空 | 收取已启用资料，生成实际消化任务；dot 自己阅读并提交结果 |
| reviewed | `summary`：已展示的认识摘要；`wiki_pages`：实际使用的发布页 | 等待本人确认或纠正 |
| confirmed | `user_reply`：本人真实回复；可提供纠正后的 `summary` 和当前 `question` | 先写回纠正，再依据当前 Wiki 回答问题；明确纠正无需二次确认 |
| used | `question`、已给出的 `answer`、实际采用的 `wiki_pages` | 记录首次使用完成，续接日常使用与本人选择的维护 |

配置保存、任务生成和 digest 完成都不自动记录本人确认或首次回答。dot 在真实对话中完成展示和回答后再提交记录。终端 setup 保存选择后也进入同一 review 步骤。

## 创建与配置

```bash
python3 personal-agent -w ~/PersonalContext setup
python3 personal-agent -w ~/PersonalContext init --name '用户称呼' --timezone Asia/Shanghai --goal '当前目标'
python3 personal-agent -w ~/PersonalContext sources
python3 personal-agent -w ~/PersonalContext sources chatgpt --enable --path '/导出.zip' --share wiki
python3 personal-agent -w ~/PersonalContext sources chatgpt --enable --mode app
python3 personal-agent -w ~/PersonalContext sources chatgpt --discover
python3 personal-agent -w ~/PersonalContext sources chatgpt --include '实际会话ID' --from 2026-10-01
python3 personal-agent -w ~/PersonalContext sources wechat --enable --mode import --path '/微信快照目录' --share sources
python3 personal-agent -w ~/PersonalContext sources wechat --discover
python3 personal-agent -w ~/PersonalContext sources wechat --exclude '实际群聊ID'
python3 personal-agent -w ~/PersonalContext sources codex --enable --mode local
```

`--processing digest/archive` 决定是否交给处理 Agent 自动形成理解；`--share none/wiki/sources` 决定日常 dot 发布入口及云端包包含哪些内容。允许语义处理意味着该处理 Agent 可以读取选定材料。姓名、目标和其他初始化信息属于本人填写的来源。

关闭采集用 `--disable`；已有原件和认识继续保留。停止自动消化用 `--processing archive`。改变发布范围用 `--share`，已有发布入口自动刷新。清空路径、日期和会话选择分别用 `--clear-paths`、`--clear-dates`、`--clear-includes`、`--clear-excludes`。收窄范围时撤回旧阅读材料及相关认识，并按新范围接入；扩大范围保留已有认识。

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
  "source_permissions": {"manual": 0, "codex": 0, "chatgpt": 0, "wechat": 0, "health": 0, "payments": 0},
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

revision 和 source_permissions 原样使用本轮任务的值，不取提交时的新版本；上例的 0 只是初始版本。用户改变使用范围后旧回执不恢复已撤回认识，重新取得任务并实际阅读。部分读完用 `partial` 并给 `next_read`。完整文字副本改变也会更新版本，即使回复摘录未变化。Wiki 更新可给 `base_content`；两端同时修改时保留另一份内容供合并。

长聊天的 job 在每份来源下返回 `reading.path`：来自完整文字副本的本轮分段，附原消息位置与文字范围。默认每份 24,000 字符，可用 `digest --max-chars` 调整；这是分配工作的大小，不是完成条件。实际读完这一段且 `reading.more=true` 时，用 `partial` 和 `next_read: {"message": 消息序号, "offset": 文字偏移}` 提交，再执行 digest 从该位置继续。相同版本保留位置，来源更新后重新检查变化。每段比较已有认识、更新实际有变化的 Wiki，不把机械摘要当作个人理解。

### 自动获取 ChatGPT 会话

新工作区的 ChatGPT 默认模式为 app，来源仍需本人选择启用；已有 export 模式保留。以下命令生成当前 Agent 的工具请求并保存实际返回，不在 Python 中模拟平台 API：

```bash
python3 personal-agent -w ~/PersonalContext chatgpt
python3 personal-agent -w ~/PersonalContext chatgpt discover --lane recent --response '/list_threads结构化结果.json'
python3 personal-agent -w ~/PersonalContext chatgpt discover --lane archived --response '/归档列表结构化结果.json'
python3 personal-agent -w ~/PersonalContext chatgpt record --response '/read_thread结构化结果.json'
python3 personal-agent -w ~/PersonalContext chatgpt record --cursor '本次请求的真实游标' --response '/历史页结构化结果.json'
```

Agent 调用返回的真实 `list_threads`、`list_archived_threads(source=chatgpt)`、`read_thread`；JSON 文件保存工具的结构化内容本身。发现结果只纳入所选 ChatGPT ID，摘要列表不能作为正文。快照持续合并到 `inbox/chatgpt/app/`，获取状态在 `state/chatgpt-capture.json`。每次刷新最近消息，并分别继续新消息之间的缺页、尚未取完的历史；既有账号导出中的会话 ID 也可作为刷新入口。取得页面后再次 collect 或 maintain 即进入原有消化队列。

最近列表是工具实际返回的有界范围，不是全量账号历史证明；归档发现沿工具游标继续。工具未提供的旧会话可通过账号导出补充。消息截断、摘要限制和未取得历史保留为获取缺口；当前已取得材料读完不等于完整会话已取完。

`wiki_rebuild` 列出范围变更影响的页面及仍可用来源。dot 仅从当前获准材料重建，提交 wiki_updates 后移除对应重建项。没有剩余证据时保留空白，不恢复旧结论。

```bash
python3 personal-agent -w ~/PersonalContext digest record '/实际回执.json'
python3 personal-agent -w ~/PersonalContext feedback --page 'wiki/想法与选择/目标与选择.md' --section '当前目标' --text '本人明确的新目标'
```

feedback 直接保存当前本人确认，同名章节替换，原表达作为来源保留。自动推断仍由 Agent 根据真实材料维护。feedback、digest record、collect 和 sync 写入后会刷新已创建的 dot 日常入口，无需另跑 dot。

## 停止使用与撤回

```bash
python3 personal-agent -w ~/PersonalContext withdraw wechat
python3 personal-agent -w ~/PersonalContext withdraw wechat --delete-raw
python3 personal-agent -w ~/PersonalContext withdraw --receipt '/原生工具实际撤回结果.json'
```

withdraw 停止来源的采集、消化和发布，移除阅读副本、阅读状态与派生页。原件默认留在本地；`--delete-raw` 同时删除本工作区 raw 和 inbox 中的该来源文件，外部输入文件不删除。重新使用时由本人明确选择，Agent 用 sources --enable、--processing、--share 保存新选择。

出处当前记录到整页，受影响页面回到空白模板并进入重建任务；其他来源继续可用。使用范围的修订随任务和包保存，旧回执和旧返回包不恢复撤回内容。本地受管理的旧包会移除；外部已登记 artifact_ref 的撤回记录在 state/withdrawals.json，由 dot 的原生工具实际处理后登记：

```json
{"id":"撤回请求的真实 id","remote":[{"artifact_ref":"待处理的真实引用","status":"removed","note":"实际删除旧包、解包副本及相关认识的结果"}]}
```

失败填 failed 并记录具体结果；不能用本地删除替代远端成功。未登记的手工副本按用户提供的位置处理。

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

上传命令从 stdin 接收 `{"kind":"personal_context_upload","bundle":"真实文件路径","id":"包ID"}`，执行实际传输后返回 `{"status":"uploaded","artifact_ref":"原生工具的真实持久引用"}`。原生工具直接上传时用 export --receipt 登记真实结果；artifact_ref 使用文件 ID 或资源引用，认证值留在传输层。若工具还返回可维护的持久解包工作区、返回包引用，可分别登记 `workspace_ref`、`return_ref`，供下一轮主动取回。

没有上传接口时包仍可用，状态为待交付。打包、上传、云端应用和语义阅读分别记录。剩余历史不阻止已有 Wiki 发布。

## 周期任务与状态

```bash
python3 personal-agent -w ~/PersonalContext schedule --daily-at 22:00
python3 personal-agent -w ~/PersonalContext schedule --task maintenance --confirm-ref '实际任务引用' --executor codex-local
python3 personal-agent -w ~/PersonalContext schedule --task calibration --weekly-on SU --daily-at 10:00
python3 personal-agent -w ~/PersonalContext schedule --task calibration --confirm-ref '实际云端任务引用' --executor dot-cloud
python3 personal-agent -w ~/PersonalContext maintain
python3 personal-agent -w ~/PersonalContext maintain --finish
python3 personal-agent -w ~/PersonalContext calibrate
python3 personal-agent -w ~/PersonalContext status
```

新工作区默认按用户时区每天 21:00 同步续读、每周日 10:00 校准，可以调整；旧工作区已有设置与关闭选择保留。schedule 返回两项 `tasks`，各自包含 selection、prompt、request、已登记引用和状态。当前 Agent 在初始化期间用实际 dot schedule 或 Codex automation_update 保存，Codex 默认使用当前聊天 heartbeat。校准优先 dot 云端；只有本地工具时如实登记实际位置，不能声称云端独立运行。已存在的同职责任务更新原引用，不重复创建。

平台保存后分别 `--confirm-ref` 登记，保留实际安排和正文；之后修改时间、时区或任务内容会提示待更新原任务。取消选择用 `schedule --task maintenance|calibration --disable`，已有平台任务由当前 Agent 取消并读取真实结果。生成说明、登记选择和平台保存是不同状态。

maintain 返回完整 `instructions`，先合并已取回的返回包，收取本机更新，并返回 ChatGPT 原生工具请求和消化任务。Agent 按说明取得新页、云端更新后再次 maintain 刷新任务；实际理解后 `maintain --finish` 发布当前 Wiki 与获准副本。没有 Agent 适配器时，本命令不把任务生成当成理解完成，也不在阅读前交付本轮最终结果；显式 `--run-agent` 则应用适配器的真实回执后继续发布。

默认交换目录为 `exchange/`，原生文件工具将另一端更新放在 `incoming/*.zip`；maintain 自动合并未接收过的包，保留双方修改，具体失败只影响对应包。完成阅读后生成 `outgoing/context.zip`；云端工作区生成 `outgoing/return.zip`。上传使用已配置适配器或当前 Agent 的原生文件／Library 工具；没有该工具时仍为待交付。可用 `runtime --sync-directory '/真实交换目录'` 指向已有共享位置，用 `runtime --no-cloud-sync` 关闭取回与交付、`--cloud-sync` 恢复。导出的云端副本不继承本机任务引用或再次创建相同日程。

calibrate 生成独立的认识回看任务：依据新表达、矛盾与实际反馈修订对应 Wiki，只有影响建议且已有材料不能澄清时询问本人，再按授权同步。历史未读完不阻止使用、交付已有理解。无可行动变化保持安静。
