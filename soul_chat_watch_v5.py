import os
import shutil
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
import re

ADB = os.environ.get("ADB_PATH") or shutil.which("adb") or "/opt/homebrew/bin/adb"
AI_READER = str(Path(__file__).resolve().parent / "soul_ai_reader_v6_14_active.py")

REMOTE_XML = "/sdcard/soul_watch.xml"
LOCAL_XML = "/tmp/soul_watch.xml"

WAIT_SECONDS = 10
CHECK_INTERVAL = 1


def parse_bounds(s):
    nums = list(map(int, re.findall(r"\d+", s)))
    if len(nums) != 4:
        return None

    x1, y1, x2, y2 = nums
    return {
        "cx": (x1 + x2) // 2,
        "cy": (y1 + y2) // 2
    }


def read_latest_other_message():

    subprocess.run(
        [ADB, "shell", "uiautomator", "dump", REMOTE_XML],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    subprocess.run(
        [ADB, "pull", REMOTE_XML, LOCAL_XML],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    try:
        root = ET.parse(LOCAL_XML).getroot()
    except Exception:
        return None

    messages = []
    avatars = []

    for node in root.iter("node"):

        rid = node.attrib.get("resource-id", "")
        text = node.attrib.get("text", "").strip()
        bounds = parse_bounds(node.attrib.get("bounds", ""))

        if not bounds:
            continue

        if rid.endswith("/content_text") and text:
            messages.append({
                "text": text,
                "cy": bounds["cy"]
            })

        elif rid.endswith("/avatar"):
            avatars.append({
                "cx": bounds["cx"],
                "cy": bounds["cy"]
            })

    other = []

    for msg in messages:

        if not avatars:
            continue

        avatar = min(
            avatars,
            key=lambda a: abs(a["cy"] - msg["cy"])
        )

        if (
            abs(avatar["cy"] - msg["cy"]) < 180
            and avatar["cx"] < 540
        ):
            other.append(msg)

    if not other:
        return None

    latest = max(other, key=lambda m: m["cy"])

    return latest["text"]


print("\n========== Soul 连续聊天测试 ==========\n")

last_message = read_latest_other_message()

print("当前对方最后一句：")
print(last_message)

print("\n开始等待新回复……")

silent_seconds = 0

while silent_seconds < WAIT_SECONDS:

    time.sleep(CHECK_INTERVAL)

    current = read_latest_other_message()

    if current and current != last_message:

        print("\n🟢 发现新消息：")
        print(current)

        # ===== 短暂收消息，避免对方连续发几句时过早回复 =====
        pending = current

        for _ in range(3):
            time.sleep(0.7)
            newer = read_latest_other_message()

            if newer and newer != pending:
                print("\n➕ 对方又发来内容，继续收消息：")
                print(newer)
                pending = newer
            else:
                break

        current = pending

        print("\n⚡ 消息暂时稳定，立即交给 AI 回复……")

        result = subprocess.run([
            "python3",
            "-u",
            AI_READER,
            "--send"
        ])

        code = result.returncode

        if code == 0:
            # REPLY：已经正常回复，继续监听
            print("\n✅ REPLY：已完成回复，继续监听")
            last_message = current
            silent_seconds = 0
            print("重新开始10秒等待……")

        elif code == 10:
            # SILENT：看到了，但选择不回复
            print("\n🤫 SILENT：本轮不回复，继续监听")
            last_message = current
            silent_seconds = 0
            print("重新开始10秒等待……")

        elif code == 20:
            # EXIT：不要在这里返回，让上层身体程序处理
            print("\n🚪 EXIT：把退出指令交给上层程序")
            raise SystemExit(20)

        elif code == 30:
            # MANUAL：停在当前聊天，交给本人
            print("\n🙋 MANUAL：需要本人处理")
            raise SystemExit(30)

        else:
            # INVALID 或其他异常
            print(f"\n⚠️ AI返回异常状态：{code}")
            raise SystemExit(40)

    else:
        silent_seconds += CHECK_INTERVAL
        print(
            f"等待中：{silent_seconds}/{WAIT_SECONDS} 秒",
            end="\r",
            flush=True
        )


print("\n\n连续10秒没有发现对方新回复。")
print("准备返回聊天列表……")

# 智能返回：根据当前 Activity 判断是否真的回到消息列表
def get_current_activity():
    result = subprocess.run(
        [ADB, "shell", "dumpsys", "window"],
        capture_output=True,
        text=True
    )

    for line in result.stdout.splitlines():
        if "mCurrentFocus" in line:
            return line

    return ""


returned_to_main = False
for attempt in range(3):
    current = get_current_activity()

    if "MainActivity" in current:
        print("✅ 已确认返回消息列表")
        returned_to_main = True
        break

    if "ConversationActivity" in current:
        print(f"↩️ 仍在聊天页面，第 {attempt + 1} 次尝试 XML 返回")
        # 通过 Soul 聊天页真实返回控件返回，禁止盲目系统返回
        subprocess.run(
            [ADB, "shell", "uiautomator", "dump", REMOTE_XML],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        subprocess.run(
            [ADB, "pull", REMOTE_XML, LOCAL_XML],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        try:
            page = ET.parse(LOCAL_XML).getroot()
        except Exception:
            raise SystemExit(40)
        back = next((n for n in page.iter("node")
                     if n.attrib.get("resource-id", "").endswith("/item_left_back")), None)
        if back is None:
            print("⚠️ 未找到 item_left_back，停止，不执行系统返回")
            raise SystemExit(40)
        nums = list(map(int, re.findall(r"\d+", back.attrib.get("bounds", ""))))
        if len(nums) != 4:
            raise SystemExit(40)
        x, y = (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2
        subprocess.run([ADB, "shell", "input", "tap", str(x), str(y)], check=True)
        time.sleep(1)
        continue

    print("⚠️ 当前页面无法识别，停止继续返回")
    print(current)
    raise SystemExit(40)

else:
    print("⚠️ 连续返回3次仍未到消息列表")
    raise SystemExit(40)

if not returned_to_main:
    print("❌ 未能确认返回主聊天列表，禁止报告本轮完成")
    raise SystemExit(40)

print("\n========== 连续聊天测试结束 ==========")
print("🧭 返回上层：WATCH_DONE (21)；已确认回到主聊天列表")
raise SystemExit(21)
