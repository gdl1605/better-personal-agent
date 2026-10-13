"""Resumable conversation handoff; the agent records real user interactions."""
from __future__ import annotations

from .core import load, within, write
from .delivery import publish_dot_view
from .runtime import command, job, schedule


def onboard(ws, response=None):
    path = ws.root / "state/onboarding.json"
    state = load(path, {"phase": "configure", "question": ""})
    collected = None
    if response:
        stage = response["stage"]
        if stage == "configured":
            from .maintenance import maintain
            state = {"phase": "review", "question": response.get("question", ""), "configured_at": ws.now()}
            collected = maintain(ws)
        elif stage in ("reviewed", "used"):
            expected = ("review", "confirm") if stage == "reviewed" else ("first_use",)
            if state["phase"] not in expected:
                raise ValueError("请先完成当前引导步骤；只记录已发生的交互。")
            pages = response.get("wiki_pages", [])
            view = publish_dot_view(ws)
            if not pages or any(not within(view, p, "wiki").is_file() for p in pages):
                raise ValueError("请记录实际使用且允许 dot 读取的 Wiki 页面。")
            if stage == "reviewed":
                if not response.get("summary", "").strip():
                    raise ValueError("需要实际向本人展示的认识摘要。")
                state.update(phase="confirm", review={"summary": response["summary"], "wiki_pages": pages, "shown_at": ws.now()})
            else:
                if not response.get("question", "").strip() or not response.get("answer", "").strip():
                    raise ValueError("首次使用记录需要用户的实际问题和已给出的回答。")
                state.update(phase="ready", first_use={"question": response["question"], "answer": response["answer"], "wiki_pages": pages, "answered_at": ws.now()})
        elif stage == "confirmed":
            if state["phase"] != "confirm" or not response.get("user_reply", "").strip():
                raise ValueError("只有本人实际确认或明确纠正后，才记录认识确认。")
            state.update(phase="first_use", confirmation={"user_reply": response["user_reply"], "at": ws.now()})
            if response.get("summary"):
                state["review"]["summary"] = response["summary"]
            if response.get("question"):
                state["question"] = response["question"]
        else:
            raise ValueError("引导记录的 stage 为 configured/reviewed/confirmed/used。")
        write(path, state)

    phase = state["phase"]
    guide = ws.root / "state/onboarding-instructions.md"
    reply = ws.root / "state/onboarding-response.json"
    lines = [
        "# dot 首次使用引导", "",
        "由你在当前对话带用户完成；命令、路径、来源 ID 和回执由你操作，不让用户执行或解释。",
        "复用已经给出的回答，只询问目标、资料范围、重要认识的确认以及需要本人完成的导出或登录。",
        "不因未提供某来源或尚未读完历史而停住；可以先用本人自述和已整理的资料解决当前问题。",
        f"当前步骤：{phase}；用户当前问题：{state.get('question') or '从已有表达提取；确实缺少时询问当前最想解决的一件事。'}", "",
    ]
    automation = schedule(ws)
    result = {"phase": phase, "instructions": str(guide), "response_path": str(reply), "automation": automation}
    lines += [
        '自动维护在初始化期间配置，不推迟到首次使用以后。向本人说明当前频率、时区和来源范围，沿用已有关闭选择；新库默认每天 21:00 同步续读、每周日 10:00 校准。',
        f"读取 {command(ws, 'schedule')} 的两项 request；当前 Agent 用实际 dot／Codex 原生工具保存或更新尚未配置的任务，登记真实引用和执行位置。",
        '已有同职责任务更新原引用，不重复创建。当前没有原生工具时保留具体未配置项，继续当前引导。', '',
    ]
    if phase == "configure":
        lines += [
            "先简短了解称呼、目标和愿意提供的材料，时区和路径能从环境得到时不用重复询问。",
            "向本人分别说清：整理时哪些资料会交给 Agent 阅读，日常 dot 可用哪些认识或原文；未选择的来源保持关闭。",
            f"你调用 {command(ws, 'init', '--name', '本人称呼', '--timezone', '用户时区', '--goal', '本人目标')} 保存实际选择，参数须替换为实际回答。",
            "你使用 sources 保存实际来源选择，必要时先 discover 按标题展示；参数见项目 docs/commands.md。",
            "需要导出或登录时解释本人实际要做的一步。已经有自述或文件时先继续，其他资料以后补。",
            '选择保存后记录 {"stage":"configured","question":"本人当前具体问题"}；工具随后收取资料并生成真实阅读任务。',
        ]
    elif phase == "review":
        prepared = collected['digestion'] if collected else job(ws)
        result["digestion"] = prepared
        if collected is not None:
            result['maintenance'] = collected
            result["collection"] = collected['collection']
            lines.append(f"先完成本轮获取与同步：{collected['instructions']}；新页和返回包取得后刷新阅读任务，再展示个人认识。")
        lines += [
            f"实际阅读并执行 {prepared['instructions']}，由你提交消化回执；不要把任务生成当成阅读完成。",
            "根据已读材料整理背景、目标、约束和协作偏好；区分本人表达与分析，不把他人建议写成本人事实。",
            f"处理后调用 {command(ws, 'dot')}，实际读取返回的日常入口和相关 Wiki。",
            "向用户简短展示已有个人认识，以及会改变当前建议的重要未知，请本人直接纠正。仅展示获准提供的内容。",
            '展示后记录 {"stage":"reviewed","summary":"实际展示的认识摘要","wiki_pages":["wiki/关于用户/当前认识.md"]}，页面填写实际读到的页面。',
        ]
    elif phase == "confirm":
        lines += [
            "已向本人展示：" + state["review"]["summary"],
            "等待本人的真实确认或纠正；不代替本人确认。已收到明确纠正时先用 feedback 或 digest record 改正对应主落点。",
            "本人明确的纠正本身可以作为确认，不再要求重复确认；用 summary 保存纠正后的认识。",
            '记录 {"stage":"confirmed","user_reply":"本人实际回复","summary":"确认或纠正后的认识","question":"已表达的当前问题"}，然后继续首次使用。',
        ]
    elif phase == "first_use":
        view = publish_dot_view(ws)
        result["context"] = str(view)
        lines += [
            f"实际读取当前日常入口 {view} 的相关 Wiki，用已确认的目标和约束处理用户的问题。",
            "有足够信息就直接给出可以使用的回答或结果，简短说明哪些个人情况影响了建议。问题已有答案就复用，不重复询问。",
            "尚无具体问题时只问当前最想处理的一件事；其他未读资料以后继续。",
            '回答后记录 {"stage":"used","question":"实际问题","answer":"已给用户的实际回答","wiki_pages":["实际采用的 Wiki 路径"]}。未回答时不能登记 used。',
        ]
    else:
        view = publish_dot_view(ws)
        result["context"] = str(view)
        lines += [
            f"首次配置、本人确认和首次使用已有记录。后续日常读取入口为 {view}；在当前环境支持的持续笔记中记住这个真实入口及维护职责。",
            "告诉用户之后可以直接交流、补资料或纠正认识；反馈、消化和同步的写入会刷新已有入口。",
            "检查自动同步续读和独立校准的两项实际安排；初始化仍未保存的任务继续配置，本人关闭的保持关闭。已有认识可以使用，历史按分段位置继续。",
            "用户要求停止使用或删除资料时由你执行 withdraw，并完成返回的远端撤回操作；仅停止采集则使用 sources --disable。",
        ]
    lines += ["", f"由你把实际交互记录写到 {reply}，运行 {command(ws, 'onboard', 'record', reply)}，读取返回说明继续下一步。",
              "这些结构用于你的工具操作，不复制给用户填写；本人尚未回复时保留当前步骤，恢复对话后续接。", ""]
    write(guide, "\n".join(lines))
    return result
