import shutil
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SOUL AI Reader V6.12 — 持久化消息计数 + 双方10条人工接管

基于用户上传的 soul_ai_reader_v4_3.py：
- 保留原有豆包提示词、用户资料、独立记忆、消息指纹、待核实发送保护、
  btn_send 检查、发送前核对、发送结果检查和退出码。
- 新增统一消息解析：文字 / 图片表情（只识别类型）/ 语音 / Soul audioContent 转写。
- 图片表情不识别具体图案；语音转写直接读取 Soul 页面上的 audioContent。
- 默认只做 DRY-RUN；只有明确加 --send 才允许点击发送。
- 双方可见消息合计达到10条（文字/表情/语音各计1条）后，停止 AI 并返回 MANUAL。
- 语音转写文本属于语音消息本身，不重复计数。
- 本版不负责匹配、弹窗、返回聊天列表或扫描消息中心。
- 每个聊天对象独立保存累计计数与最近消息序列；重启后不清零。
- 首次运行以当前 XML 可见消息建立基准；后续通过消息序列重叠识别新增消息。
- 若无法可靠对齐消息序列，停止自动回复并提示人工核对，避免误计数。

首次测试建议：
    python3 soul_ai_reader_v6_9.py
这会生成回复草稿但不会发送。
真实发送必须显式使用：
    python3 soul_ai_reader_v6_9.py --send
"""

import sys
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import re
import json
import hashlib
import os
import time
from datetime import datetime

ADB = os.environ.get("ADB_PATH") or shutil.which("adb") or "/opt/homebrew/bin/adb"
REMOTE_XML = "/sdcard/soul_reader_v69.xml"
LOCAL_XML = "/tmp/soul_reader_v69.xml"
PACKAGE = "cn.soulapp.android"
SCREEN_MID_X = 540

RID = {
    "item_root": f"{PACKAGE}:id/item_root",
    "avatar": f"{PACKAGE}:id/avatar",
    "chat_avatar": f"{PACKAGE}:id/chat_avatar",
    "text": f"{PACKAGE}:id/content_text",
    "sticker": f"{PACKAGE}:id/img_static",
    "voice": f"{PACKAGE}:id/voice_bubble",
    "voice_text": f"{PACKAGE}:id/audioContent",
}

def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=45)

def parse_bounds(bounds):
    nums = list(map(int, re.findall(r"\d+", bounds or "")))
    return tuple(nums) if len(nums) == 4 else None

def node_center(node):
    b = parse_bounds(node.attrib.get("bounds", ""))
    return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) if b else None

def is_rid(node, key):
    value = node.attrib.get("resource-id", "")
    return value == RID[key] or value.endswith("/" + RID[key].split("/")[-1])

def parse_sender(item):
    nodes = list(item.iter("node"))
    avatars = [n for n in nodes if is_rid(n, "avatar")]
    if not avatars:
        avatars = [n for n in nodes if is_rid(n, "chat_avatar")]
    points = [node_center(n) for n in avatars]
    points = [p for p in points if p]
    if not points:
        return "未知"
    x = points[0][0]
    if x < SCREEN_MID_X:
        return "对方"
    if x > SCREEN_MID_X:
        return "我"
    return "未知"

def parse_message_items(root):
    records = []
    item_nodes = [n for n in root.iter("node") if is_rid(n, "item_root")]

    for item in item_nodes:
        nodes = list(item.iter("node"))
        sender = parse_sender(item)
        text_nodes = [n for n in nodes if is_rid(n, "text") and n.attrib.get("text", "").strip()]
        sticker_nodes = [n for n in nodes if is_rid(n, "sticker")]
        voice_nodes = [n for n in nodes if is_rid(n, "voice")]
        transcript_nodes = [n for n in nodes if is_rid(n, "voice_text") and n.attrib.get("text", "").strip()]

        # 语音转写是语音消息的附属文字，不重复作为独立消息。
        if voice_nodes:
            transcript = " ".join(n.attrib.get("text", "").strip() for n in transcript_nodes)
            target = voice_nodes[0]
            kind = "voice"
            content = transcript if transcript else "[语音消息，尚未转写]"
        elif sticker_nodes:
            target = sticker_nodes[0]
            kind = "sticker"
            content = "[发送了表情]"
        elif text_nodes:
            target = text_nodes[0]
            kind = "text"
            content = " ".join(n.attrib.get("text", "").strip() for n in text_nodes)
        else:
            continue

        b = parse_bounds(target.attrib.get("bounds", ""))
        cy = (b[1] + b[3]) / 2 if b else float(len(records))
        records.append({
            "sender": sender,
            "type": kind,
            "text": content,
            "cy": cy,
            "bounds": b,
            "transcribed": bool(voice_nodes and transcript_nodes),
        })

    records.sort(key=lambda m: m["cy"])
    return records

print("\n正在读取 Soul 当前聊天页面……\n")
result = adb("shell", "uiautomator", "dump", REMOTE_XML)
if result.returncode != 0:
    print("❌ 无法获取手机页面")
    print(result.stderr)
    raise SystemExit(40)

result = adb("pull", REMOTE_XML, LOCAL_XML)
if result.returncode != 0:
    print("❌ 无法读取 XML")
    print(result.stderr)
    raise SystemExit(40)

try:
    root = ET.parse(LOCAL_XML).getroot()
except Exception as exc:
    print(f"❌ XML 解析失败：{exc}")
    raise SystemExit(40)

messages = parse_message_items(root)
avatars = []  # V6.9 使用消息容器内的头像判断方向；保留旧函数调用兼容
other_messages = [m for m in messages if m["sender"] == "对方"]
unknown_messages = [m for m in messages if m["sender"] == "未知"]

print("========== V6.9 统一消息识别 ==========")
for i, msg in enumerate(messages, 1):
    print(f"#{i} [聊天消息已隐藏]")
    if msg["type"] == "voice":
        print("   Soul转写：" + ("已获取" if msg["transcribed"] else "尚未获取"))
if unknown_messages:
    print(f"⚠️ 有 {len(unknown_messages)} 条消息发送方向未知；不会把它们当作对方新消息。")
print("======================================")

def build_chat_history(messages, avatars=None):
    lines = []
    for msg in messages:
        sender = msg.get("sender", "未知")
        kind = msg.get("type", "text")
        text = msg.get("text", "")
        if sender not in {"对方", "我"}:
            continue
        if kind == "sticker":
            body = "[发送了表情]"
        elif kind == "voice" and not msg.get("transcribed"):
            body = "[语音消息，尚未转写]"
        else:
            body = text
        lines.append(f"{sender}：{body}")
    return "\n".join(lines)

# ===== SOUL V4：用户身份与独立记忆 =====
import json
import hashlib
import os
import time
from datetime import datetime

# 当前聊天对象必须从私聊页标题读取，不能猜测或使用固定昵称。
username_nodes = [
    n for n in root.iter("node")
    if n.attrib.get("resource-id", "").endswith("/tv_title")
    and n.attrib.get("text", "").strip()
]
username = username_nodes[0].attrib["text"].strip() if username_nodes else ""

if not username:
    print("❌ 没有识别到私聊页标题 tv_title；为防止串聊，本轮停止。")
    raise SystemExit(40)

print("\n当前聊天对象：[已隐藏]")

MEMORY_DIR = Path(__file__).resolve().parent / "SOUL_MEMORY"
MEMORY_DIR.mkdir(exist_ok=True)

def safe_user_key(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]

COUNTER_FILE = Path(__file__).resolve().parent / "soul_conversation_counter.json"

def message_signature(item):
    payload = "\x1f".join([item.get("sender", "未知"), item.get("type", "text"), item.get("text", "")])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def load_counter_store():
    try:
        data = json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_counter_store(data):
    tmp = COUNTER_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(COUNTER_FILE)

def update_persistent_counter(chat_name, visible_messages):
    known = [m for m in visible_messages if m.get("sender") in {"对方", "我"}]
    current = [message_signature(m) for m in known]
    store = load_counter_store()
    key = safe_user_key(chat_name)
    entry = store.get(key)

    # 第一次运行：以当前 XML 实际可见的消息建立基准。
    if not isinstance(entry, dict):
        count = len(current)
        status = "MANUAL" if count >= 10 else "AUTO"
        store[key] = {
            "username": chat_name, "message_count": count,
            "sequence": current[-40:], "status": status,
            "initialized_at": datetime.now().isoformat(timespec="seconds"),
            "last_updated": datetime.now().isoformat(timespec="seconds")
        }
        save_counter_store(store)
        return count, status, "initialized_from_visible_messages"

    if entry.get("status") == "MANUAL":
        return int(entry.get("message_count", 10)), "MANUAL", "already_manual"

    previous = entry.get("sequence", [])
    if not isinstance(previous, list):
        return int(entry.get("message_count", 0)), "UNCERTAIN", "invalid_saved_sequence"

    added = None
    # 页面仍包含旧序列，并在尾部追加新消息。
    if len(current) >= len(previous) and current[:len(previous)] == previous:
        added = current[len(previous):]
    else:
        # 页面滚动后，旧序列尾部与当前页面头部应有重叠。
        for size in range(min(len(previous), len(current)), 0, -1):
            if previous[-size:] == current[:size]:
                added = current[size:]
                break
        if added is None and current == previous:
            added = []
        if added is None and not current:
            added = []
        if added is None:
            return int(entry.get("message_count", 0)), "UNCERTAIN", "sequence_cannot_be_aligned"

    count = int(entry.get("message_count", 0)) + len(added)
    status = "MANUAL" if count >= 10 else "AUTO"
    entry.update({
        "message_count": count, "sequence": current[-40:], "status": status,
        "last_updated": datetime.now().isoformat(timespec="seconds")
    })
    store[key] = entry
    save_counter_store(store)
    return count, status, "updated"

USER_MEMORY_FILE = MEMORY_DIR / f"{safe_user_key(username)}.json"
PENDING_SEND_FILE = MEMORY_DIR / f"{safe_user_key(username)}.pending.json"

def write_pending_send(payload):
    tmp = PENDING_SEND_FILE.with_suffix(".pending.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PENDING_SEND_FILE)

def clear_pending_send():
    if PENDING_SEND_FILE.exists():
        PENDING_SEND_FILE.unlink()

# 未核实的上一次发送必须先人工核对，禁止自动重发。
if PENDING_SEND_FILE.exists():
    try:
        pending = json.loads(PENDING_SEND_FILE.read_text(encoding="utf-8"))
        print("⚠️ 存在待核实发送记录，正文已隐藏")
        print("⚠️ 为防止重复发送，本轮停止。请先人工检查手机聊天页面。")
        raise SystemExit(40)
    except json.JSONDecodeError:
        print("❌ 待核实记录文件损坏；为防止重复发送，本轮停止。")
        raise SystemExit(40)

def load_user_memory():
    try:
        data = json.loads(USER_MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("memory root is not an object")
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        data = {"username": username, "processed": [], "history": []}
    data.setdefault("username", username)
    data.setdefault("processed", [])
    data.setdefault("history", [])
    return data

def save_user_memory(data):
    tmp = USER_MEMORY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USER_MEMORY_FILE)

def message_fingerprint(user, message_text, message_time, recent_context):
    # 用户名 + 消息时间 + 内容 + 最近上下文；避免不同用户同文冲突。
    payload = "\x1f".join([user, message_time or "", message_text, recent_context[-180:]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

# ========== SoulBot AI 大脑 V2 ==========

import os
import json
from openai import OpenAI

PROFILE_FILE = str(Path(__file__).resolve().parent / "soul_profile.json")


def load_profile():
    with open(PROFILE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def ask_doubao(profile, chat_history):

    # 限制单次模型请求时长，并关闭 SDK 自动重试，避免网络异常时长时间卡住。
    # 超时或连接失败会由外层异常保护处理：本轮不发送、不写入已处理记录。
    client = OpenAI(
        api_key=os.environ["ARK_API_KEY"],
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        timeout=25.0,
        max_retries=0,
    )

    request_started_at = time.monotonic()
    print("⏳ 正在请求狗头军师（单次最多等待25秒）……")
    response = client.responses.create(
        model="doubao-seed-2-1-lite-260915",

        instructions="""
你正在帮助用户进行一对一社交聊天。

目标：
像一个普通真人在手机上聊天。
不要像客服、AI助手、情感陪聊，也不要只会被动回答。
聊天本质上是双方交换信息：接住对方的话，也要自然把话题递回去。

【基本规则】

1. 用户本人的姓名、年龄、所在地、家乡、职业、兴趣、感情状态、经历，只能来自“用户授权资料”。
2. 绝对禁止编造具体经历、已发生的事件、真实身份信息、实时位置、实际工作安排或重要承诺。
3. 普通寒暄和轻松闲聊不需要逐字核实事实。可以使用不声称具体事实的自然回应，例如“无聊呢，正发呆”“没干嘛，随便看看”“刚好在休息”，并自然接回话题。
4. 只有当问题确实涉及必须准确的个人事实、实时情况、金钱、健康、安全、重要安排或承诺，而且资料不足以回答时，才输出：【需要本人回答】
5. 默认只发一条消息。
5. 通常3～20个中文字，能短就短。
6. 使用自然口语、短句、省略句，不追求语法完整。
7. 不复述、总结、分析对方的话。
8. 不主动解释自己的性格、原则、动机。
9. 少用哈哈、呀、啦、呢、～和表情，合适时偶尔一个即可。
10. 禁止客服式、讨好式、情感陪伴式表达。
11. 禁止使用“听起来、感觉你、看来你、我能理解、情绪价值、分寸感、拉满、脑洞太大、太有意思了”等AI腔表达。
12. 不刻意幽默，不要每句话都包装成段子。
13. 对方使用粤语时，可以自然使用简单粤语。

【真人聊天的回球逻辑】

14. 不要只回答字面问题。对方主动了解用户时，回答以后通常可以自然反问对方。

例如：
对方：你哪里人？
回复：广东的，你呢？

对方：你做什么的？
回复：做互联网相关工作的，你呢？

对方：忙什么？
回复：刚忙点工作，你干嘛呢？

对方：吃饭了吗？
回复：刚吃了，你呢？

15. 对方主动透露自己的生活状态时，优先顺着这个细节自然问一句。

例如：
对方：我在吃饭
回复：吃啥好吃的？

对方：刚下班
回复：这么晚才下班？

对方：在喝酒
回复：跟朋友喝啊？

对方：出去玩
回复：去哪玩？

对方：在家躺着
回复：今天不用上班啊？

16. 反问不是硬性要求。
如果自然就问，不自然就不问。
不要为了维持聊天机械地每句话都加“你呢”。

17. 不要查户口。
连续主动问了1～2个问题以后，下一轮优先回应、分享一句、调侃一下，不要连续追问。

18. 对方只回复“嗯、哦、哈哈、行”等很短的内容时，根据上下文判断。
没有自然话题就不用硬找问题。

19. 对方明显准备结束聊天，例如“先忙了、睡了、不聊了”，不要强行挽留或继续盘问。

20. 对方调侃时，优先简单接住。
不要写一段认真解释。

21. 聊天要有来有回：
有时接球，有时回球，有时主动开启一个相关的小话题。
不要永远让对方负责找话题。

【聊天升级：主动、年轻、自然】
- 你的语气像一个有生活、有主见、会好奇的年轻人，而不是长辈说教、夸奖式陪聊或客服。
- 每次先看最近3～6条对话，寻找双方尚未聊开的线索；不要只对对方最后几个字作机械回应。
- 当对方表达了有内容的信息，优先使用“接住一个细节 + 推进一个小话题”的结构；有自然机会时加一个有具体内容的问题。
- 如果对方问了你的事实问题，先根据授权资料回答，再视语境问一个有关联的问题；不要每次都只问“你呢”。
- 对方说“社恐模式就是不说话”：可回“那你今天破例了？平时熟了才话多？”
- 对方说“系统安排的吧”：可回“那它眼光还行😂 你平时信缘分吗？”
- 对方说“哈哈哈”且上一轮刚聊缘分：可选“你笑啥，是觉得我太认真了？”，也可以 SILENT，依据上下文。
- 多用轻松、具体的兴趣点（当下正在做什么、最近的发现、周末喜欢去哪），少用“感觉你挺……”“那还挺……”“看来……”等空泛评价。
- 一条消息只问一个问题，不能连续多轮查户口；聊天双方可以互相分享，不需要每一句都提问。
- 对方只回“嗯嗯/哦/好的/单表情”且没有自然延展空间，优先 SILENT，不硬聊。
- 不要编造真实生活事件或亲身经历，不主动炫耀资料，不主动催促见面或索取联系方式。

22. 一次只输出真正准备发送给对方的那一句话。
不要解释为什么这样回复。

23. 宁可普通自然，也不要显得特别会聊天。
目标不是“高情商回复”，而是“像真人”。
""",

        input=f"""
【用户授权资料】
{json.dumps(profile, ensure_ascii=False, indent=2)}

【当前Soul聊天记录】
{chat_history}

请结合完整聊天上下文，判断当前聊天应该采取什么动作，并处理对方最后一条消息。

你必须只输出以下三种格式之一：

REPLY|这里填写真正要发送给对方的一句话
SILENT|
EXIT|

【状态判断】

REPLY：
对方仍在正常交流，有实际内容、问题、分享、解释、玩笑、情绪或值得自然回应的信息。

SILENT：
当前这句话没有必要回复，但暂时不能说明对方不想继续聊天。
例如单独一次“嗯”“哦”“哈哈”“好的”“行”或单独表情。
必须结合上下文判断，不能只看关键词。

EXIT：
结合最近几轮聊天，对方已经连续表现出明显低投入、敷衍或无继续交流意愿。
例如连续多轮只有“嗯、哦、好的、哈哈、表情”等低信息回应。
不要因为偶尔一次短回复就 EXIT。

【真人聊天性格】

1. 首先回应对方当前真正表达的内容，不要无视最后一句突然换话题。
2. 聊天自然、松弛、有自己的节奏，不当客服，不刻意讨好。
3. 不把“维持聊天”当任务。允许沉默，也允许一段聊天自然结束。
4. 对方投入多，可以适当多聊；对方投入少，也降低自己的投入。
5. 不要求每句话都反问。“你呢？”只在自然的时候使用。
6. 不连续追问，不把聊天变成采访。
7. 幽默不是任务。只有当前内容自然适合时才调侃或开玩笑；普通聊天就普通回答。
8. 不为了显得有趣而硬造梗。
9. 个人资料只是背景记忆，不是主动找话题的素材库。
10. 不反复主动强调兴趣爱好、职业、居住地等个人资料。
11. 只有对方主动问到、当前话题直接相关，或分享该信息非常自然时，才使用个人资料。
12. 不主动推动线下见面、到家里、一起出去等安排，也不要主动制造邀约。
13. 避免主动使用“约”字。
14. 普通寒暄不要过度谨慎，更不要动不动要求本人接管。
例如对方问“在干嘛”，可以自然回复“无聊呢，正发呆，你呢？”、“没干嘛，随便看看”。
这类轻松回应不需要证明用户此刻真的在发呆；它属于不涉及具体经历的社交表达。
但不能编造具体活动或经历，例如“刚打完羽毛球”“刚从公司回来”，除非聊天记录或授权资料明确支持。
只有涉及必须准确的个人事实、实时位置/行程、金钱、健康、安全、重要安排或承诺，且没有可靠依据时，才使用：
REPLY|【需要本人回答】
15. 回复通常简短、口语化，像手机聊天，不需要语法完整。
16. 可以偶尔使用“哈哈、呀、呢、😂”等，但不要形成固定口头禅。
17. 不使用客服式、高情商模板式、说教式语言。
18. 不解释自己的回复策略，不说自己是AI。
19. 一次只生成一条真正准备发送的消息。
20. 判断聊天状态比强行把聊天继续下去更重要。
21. “在干嘛”“吃了吗”“忙不忙”“怎么还没睡”等日常寒暄，默认属于轻松社交场景：
优先给出自然、简短、不会捏造具体经历的回应，不要输出【需要本人回答】。
22. 可以用“发呆呢”“闲着呢”“随便看看”“刚好休息”等宽泛表达，但不要把它扩写成具体、可核验的虚构经历。

再次强调：
先判断 REPLY / SILENT / EXIT。
只有 REPLY 后面可以有回复文字。
SILENT 和 EXIT 后面不要添加任何解释。
"""

    )

    elapsed = time.monotonic() - request_started_at
    print(f"✅ 狗头军师响应完成，用时 {elapsed:.1f} 秒")
    return response.output_text.strip()


print("\n========== SoulBot 大脑 V2 ==========\n")

# 每次启动都从安全状态开始，避免未初始化或沿用旧状态。
chat_action = "INVALID"
reply = ""
raw_reply = ""
message_id = ""
message_time = ""
latest = None
user_memory = None

try:

    profile = load_profile()

    chat_history = build_chat_history(messages, avatars)

    print("读取到的聊天上下文：\n")
    print("[聊天上下文已隐藏]")

    print("\n------------------------------")

    if other_messages:

        conversation_count, counter_status, counter_note = update_persistent_counter(username, messages)
        print(f"\n当前聊天双方累计消息：{conversation_count}/10")
        print(f"计数状态：{counter_status}（{counter_note}）")

        latest = max(
            other_messages,
            key=lambda m: m["cy"]
        )

        print("\n对方最新消息：")
        print("[最新消息已隐藏]")

        # 在消息 Y 坐标附近寻找时间标签；未找到时留空，不伪造时间。
        time_nodes = []
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            if rid.endswith("/timestamp") and node.attrib.get("text", "").strip():
                b = parse_bounds(node.attrib.get("bounds", ""))
                if b:
                    cy = (b[1] + b[3]) / 2
                    time_nodes.append((abs(cy - latest["cy"]), node.attrib["text"].strip()))
        message_time = min(time_nodes, key=lambda x: x[0])[1] if time_nodes else ""

        user_memory = load_user_memory()
        # 指纹只使用“最新消息之前”的上下文，避免我方发送回复后上下文变化，
        # 导致同一条对方消息在下一轮被误判为新消息。
        latest_marker = f"对方：{latest['text']}"
        stable_context = chat_history.rsplit(latest_marker, 1)[0] if latest_marker in chat_history else ""
        message_id = message_fingerprint(username, f'{latest.get("type", "text")}:{latest["text"]}', message_time, stable_context)
        processed_ids = {item.get("id") for item in user_memory["processed"] if isinstance(item, dict)}

        if message_id in processed_ids:
            print("🟡 该用户的这条消息已处理过，本轮不再调用 AI。")
            chat_action = "DUPLICATE"
            reply = ""
            raw_reply = ""
        elif counter_status == "UNCERTAIN":
            print("⚠️ 消息序列无法可靠对齐；为避免漏计或重复计数，本轮停止自动回复，请人工核对。")
            chat_action = "MANUAL"
            reply = ""
            raw_reply = ""
        elif counter_status == "MANUAL" or conversation_count >= 10:
            print("🧑‍💻 双方累计达到 10 条消息，切换人工接管；本轮不调用 AI、不发送。")
            chat_action = "MANUAL"
            reply = ""
            raw_reply = ""
        else:
            memory_history = user_memory.get("history", [])[-12:]
            memory_context = "\n".join(
                f'{item.get("role", "对方")}：{item.get("text", "")}'
                for item in memory_history if item.get("text")
            )
            full_context = chat_history
            if memory_context:
                full_context = "【该用户此前的独立记忆/近期对话】\n" + memory_context + "\n\n【当前页面聊天记录】\n" + chat_history

            raw_reply = ask_doubao(profile, full_context)

        print("\n🧠 SoulBot V3 原始判断：")
        print("[AI原始回复已隐藏]")

        if chat_action in {"DUPLICATE", "MANUAL"}:
            pass
        else:
            raw_reply = raw_reply.strip()

            if raw_reply.startswith("REPLY|"):
                chat_action = "REPLY"
                reply = raw_reply.split("|", 1)[1].strip()

                if reply == "【需要本人回答】":
                    chat_action = "MANUAL"
                    reply = ""
                    print("🙋 需要本人回答，不自动发送")

                elif not reply:
                    chat_action = "SILENT"
                    print("🤫 没有实际内容，按 SILENT 处理")

                else:
                    print("💬 决定：REPLY")
                    print("🤖 AI回复已生成，正文已隐藏")

            elif raw_reply.startswith("SILENT|") or raw_reply == "SILENT":
                chat_action = "SILENT"
                reply = ""
                print("🤫 决定：SILENT")

            elif raw_reply.startswith("EXIT|") or raw_reply == "EXIT":
                chat_action = "EXIT"
                reply = ""
                print("🚪 决定：EXIT")

            else:
                chat_action = "INVALID"
                reply = ""
                print("⚠️ 返回格式异常，本轮禁止自动发送")
    else:
        print("\n没有发现对方文字消息。")

except Exception as e:

    print("\n❌ SoulBot运行失败：")
    print(e)
    # API/读取异常不记为已处理，也绝不进入自动发送。
    chat_action = "INVALID"
    reply = ""

print("\n=====================================")

# ========== SOUL V4：安全填写、发送控件识别与发送验证 ==========

def fresh_xml_root():
    result = adb("shell", "uiautomator", "dump", REMOTE_XML)
    if result.returncode != 0:
        raise RuntimeError("无法重新获取手机 XML")
    result = adb("pull", REMOTE_XML, LOCAL_XML)
    if result.returncode != 0:
        raise RuntimeError("无法拉取最新手机 XML")
    return ET.parse(LOCAL_XML).getroot()

def node_center(node):
    b = parse_bounds(node.attrib.get("bounds", ""))
    if not b:
        return None
    return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)

def get_chat_title(xml_root):
    """只从聊天标题栏容器读取昵称，避免误读资料页里的其他 tv_title。"""
    for parent in xml_root.iter("node"):
        if parent.attrib.get("resource-id", "").endswith("/c_ct_chat_title_info"):
            for child in parent.iter("node"):
                if child.attrib.get("resource-id", "").endswith("/tv_title"):
                    value = child.attrib.get("text", "").strip()
                    if value:
                        return value
    return ""

def fill_soul_input(text):
    current = fresh_xml_root()
    title = get_chat_title(current)
    if title != username:
        raise RuntimeError(f"聊天对象已变化：预期 {username}，当前 {title or '未知'}；停止输入")

    input_node = next((n for n in current.iter("node")
                       if n.attrib.get("resource-id", "").endswith("/et_sendmessage")), None)
    point = node_center(input_node) if input_node is not None else None
    if not point:
        raise RuntimeError("XML 中没有找到可定位的 et_sendmessage 输入区域")

    subprocess.run([ADB, "shell", "input", "tap", str(point[0]), str(point[1])], check=True)
    time.sleep(0.5)
    subprocess.run([ADB, "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"], check=True)
    time.sleep(0.2)
    # ADB Keyboard 的输入广播；先写草稿，不在此处点击发送。
    subprocess.run([ADB, "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text], check=True)
    time.sleep(0.7)

    # 输入后重新检查聊天标题，防止期间切换到其他会话。
    after = fresh_xml_root()
    title_after = get_chat_title(after)
    if title_after != username:
        raise RuntimeError("填写后聊天对象发生变化；不发送")
    return after

def find_send_control(xml_root):
    """只接受 send_test.xml 已验证的 btn_send，且必须唯一、可点击、已启用。"""
    candidates = []
    for node in xml_root.iter("node"):
        rid = node.attrib.get("resource-id", "")
        if not rid.endswith("/btn_send"):
            continue
        if node.attrib.get("clickable") != "true":
            continue
        if node.attrib.get("enabled") != "true":
            continue
        if node.attrib.get("text", "").strip() != "发送":
            continue
        point = node_center(node)
        if point:
            candidates.append((node, point))
    return candidates[0] if len(candidates) == 1 else None

def outgoing_match_count(xml_root, expected_text):
    """统计完全匹配的消息文本；不以屏幕左右坐标作为唯一判断条件。"""
    title = get_chat_title(xml_root)
    if title != username:
        raise RuntimeError(f"发送验证时聊天对象不匹配：{title or '未知'}")

    return sum(
        1 for node in xml_root.iter("node")
        if node.attrib.get("resource-id", "").endswith("/content_text")
        and node.attrib.get("text", "").strip() == expected_text
    )

def verify_sent(expected_text, baseline_count, attempts=3, interval=0.8):
    """只在发送后匹配气泡数量比发送前增加时，才认定发送成功。"""
    for attempt in range(attempts):
        check_root = fresh_xml_root()
        if outgoing_match_count(check_root, expected_text) > baseline_count:
            return True
        if attempt + 1 < attempts:
            time.sleep(interval)
    return False

try:
    if other_messages and globals().get("chat_action") == "REPLY" and globals().get("reply", "").strip():
        blocked_words = ["约", "技师", "钱", "帐号", "账号", "加微信", "微信", "人民币", "比特币", "习近平", "革命"]
        hit_words = [word for word in blocked_words if word in reply]
        if hit_words:
            print("⚠️ 回复命中禁用输出词：", "、".join(hit_words))
            chat_action = "MANUAL"
        else:
            print("准备填写消息：[接收对象已隐藏]")
            print("回复草稿已生成，正文已隐藏")
            if "--send" not in sys.argv:
                print("🧪 默认安全模式：只读取页面并生成回复，不填写输入框、不点击发送、不写入已处理记忆。")
                print("🧪 目标聊天对象：[已隐藏]")
                print("🧪 AI 回复草稿：[内容已隐藏]")
                chat_action = "DRY_RUN"
            else:
                # 发送前先记录相同文字数量，并持久化待核实状态；崩溃/验证失败时禁止重发。
                before_send_root = fresh_xml_root()
                baseline_count = outgoing_match_count(before_send_root, reply)
                after_fill = fill_soul_input(reply)
                # 点击前再取一次实时 XML：确认仍在同一聊天、输入框内容完全匹配、发送按钮唯一且可用。
                pre_tap_root = fresh_xml_root()
                title_pre_tap = get_chat_title(pre_tap_root)
                if title_pre_tap != username:
                    raise RuntimeError(f"点击发送前聊天对象变化：{title_pre_tap or '未知'}；停止发送")

                draft_nodes = [n for n in pre_tap_root.iter("node")
                               if n.attrib.get("resource-id", "").endswith("/et_sendmessage")]
                draft_text = draft_nodes[0].attrib.get("text", "").strip() if len(draft_nodes) == 1 else None
                if draft_text != reply:
                    raise RuntimeError("发送前输入框草稿与预期回复不一致；停止发送")

                send_control = find_send_control(pre_tap_root)
                if not send_control:
                    print("⚠️ 未能唯一识别已启用的 btn_send。草稿保留，不自动发送。")
                    chat_action = "SEND_FAILED"
                else:
                    _, (sx, sy) = send_control
                    # 先落盘：即使点击后程序异常，也不会自动再次发送同一条回复。
                    write_pending_send({
                        "username": username,
                        "message_id": message_id,
                        "reply": reply,
                        "baseline_count": baseline_count,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "status": "about_to_tap"
                    })
                    subprocess.run([ADB, "shell", "input", "tap", str(sx), str(sy)], check=True)
                    pending = json.loads(PENDING_SEND_FILE.read_text(encoding="utf-8"))
                    pending["status"] = "tap_sent_waiting_verification"
                    write_pending_send(pending)
                    time.sleep(1.2)
                    check_root = fresh_xml_root()
                    sent_count = outgoing_match_count(check_root, reply)
                    draft_nodes_after = [n for n in check_root.iter("node")
                                         if n.attrib.get("resource-id", "").endswith("/et_sendmessage")]
                    draft_after = draft_nodes_after[0].attrib.get("text", "").strip() if len(draft_nodes_after) == 1 else None
                    if sent_count > baseline_count or (draft_after == "" and draft_after is not None):
                        print("✅ 发送已得到确认（新增匹配消息或输入框已清空）")
                        user_memory = load_user_memory()
                        user_memory["processed"].append({"id": message_id, "status": "sent_confirmed", "at": datetime.now().isoformat(timespec="seconds")})
                        user_memory["history"].append({"role": "对方", "text": latest["text"], "time": message_time})
                        user_memory["history"].append({"role": "我", "text": reply, "time": datetime.now().strftime("%H:%M")})
                        user_memory["processed"] = user_memory["processed"][-500:]
                        user_memory["history"] = user_memory["history"][-100:]
                        save_user_memory(user_memory)
                        clear_pending_send()
                    else:
                        print("⚠️ 发送结果待人工核实：没有足够证据确认成功或失败。")
                        print("⚠️ 已保存待核实记录；程序将阻止自动重发，直到人工检查。")
                        chat_action = "SEND_FAILED"
    elif other_messages and globals().get("chat_action") in {"SILENT", "EXIT"}:
        # 有效的 AI 决策可记录，避免轮询时反复调用；API 异常不会走到这里。
        user_memory = load_user_memory()
        user_memory["processed"].append({"id": message_id, "status": chat_action.lower(), "at": datetime.now().isoformat(timespec="seconds")})
        user_memory["history"].append({"role": "对方", "text": latest["text"], "time": message_time})
        user_memory["processed"] = user_memory["processed"][-500:]
        user_memory["history"] = user_memory["history"][-100:]
        save_user_memory(user_memory)
        print(f"已记录本轮 {chat_action} 决策，不发送消息。")
    elif globals().get("chat_action") == "DUPLICATE":
        print("重复消息：跳过 AI 和发送。")
    elif globals().get("chat_action") == "MANUAL":
        print("🙋 需要本人处理，不自动发送。")
    elif globals().get("chat_action") == "SILENT":
        print("🤫 SILENT：本轮不发送。")
    elif globals().get("chat_action") == "EXIT":
        print("🚪 EXIT：交由上层程序处理。")
    else:
        print("没有符合自动发送条件的回复。")
except Exception as e:
    print("❌ V4 安全发送流程失败：", e)
    chat_action = "SEND_FAILED"

print("\n==============================")

# ===== 真人聊天 V3：把大脑决定传给上层程序 =====
import sys

if 'chat_action' not in globals():
    print("⚠️ 没有 chat_action，返回 INVALID")
    sys.exit(40)

if chat_action == "REPLY":
    print("🧠 返回上层：REPLY (0)")
    sys.exit(0)

elif chat_action == "SILENT":
    print("🧠 返回上层：SILENT (10)")
    sys.exit(10)

elif chat_action == "EXIT":
    print("🧠 返回上层：EXIT (20)")
    sys.exit(20)

elif chat_action == "MANUAL":
    print("🧠 返回上层：MANUAL (30)")
    sys.exit(30)

elif chat_action == "DUPLICATE":
    print("🧠 返回上层：DUPLICATE (10)")
    sys.exit(10)

elif chat_action == "DRY_RUN":
    print("🧪 安全预览完成：没有发送任何消息，退出码 50")
    sys.exit(50)

elif chat_action == "SEND_FAILED":
    print("🧠 返回上层：SEND_FAILED (40)")
    sys.exit(40)

else:
    print("🧠 返回上层：INVALID (40)")
    sys.exit(40)
