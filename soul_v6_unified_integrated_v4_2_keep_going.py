#!/usr/bin/env python3
# SOUL V2 integration candidate. Safe by default: use --live to enable actions.
import os
import shutil
from pathlib import Path
import argparse, subprocess, time, re, threading, sys, xml.etree.ElementTree as ET
from PIL import Image

BASE = Path(__file__).resolve().parent
ADB = os.environ.get("ADB_PATH") or shutil.which("adb") or "/opt/homebrew/bin/adb"
AI_READER = BASE / 'soul_ai_reader_v6_14_active.py'
from soul_v7_system_ai import is_system_ai, log_skip
CHAT_WATCH = BASE / 'soul_chat_watch_v5.py'
GREETING_BATCH = BASE / 'soul_v6_greeting_batch_v2.py'
BELL_BATCH = BASE / 'soul_v6_bell_batch_all.py'
OPENING = '哈哈，没想到在Soul还能遇到这么近的人。先问个不太正经的问题：你觉得两个人认识，是靠缘分多一点，还是靠其中一个人脸皮厚一点？'
REMOTE = '/sdcard/soul_v2.xml'
LOCAL = Path('/tmp/soul_v2.xml')
POLL_SECONDS = 30
IDLE_BADGE_CHECK_SECONDS = 5
GUARDIAN_REMOTE = '/sdcard/soul_popup_guardian.xml'
GUARDIAN_LOCAL = Path('/tmp/soul_popup_guardian.xml')
GUARDIAN_INTERVAL = 0.8
# uiautomator dump is a single device-side operation: serialize all dump/pull pairs.
UI_DUMP_LOCK = threading.Lock()
ADB_TIMEOUT = 12

class SoulError(RuntimeError): pass

class PopupGuardian:
    """只读后台监控器：发现弹窗后发出中断信号，绝不点击手机。

    所有触摸/滑动仍由主线程执行，避免两个程序争抢 ADB。
    与主线程共用 UI_DUMP_LOCK，避免同时启动 uiautomator dump。
    """
    def __init__(self, adb, interval=GUARDIAN_INTERVAL):
        self.adb = adb
        self.interval = interval
        self.enabled = threading.Event()
        self.stop_event = threading.Event()
        self.alert = threading.Event()
        self.lock = threading.Lock()
        self.last_popup = None
        self.last_error = None
        self._active_fingerprint = None  # 仅在弹窗从无到有/用户变化时触发一次
        self.thread = threading.Thread(target=self._watch, name='SOUL-PopupGuardian', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.enabled.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def enable(self, value=True):
        if value:
            self.enabled.set()
        else:
            self.enabled.clear()

    def consume_alert(self):
        if not self.alert.is_set():
            return None
        with self.lock:
            item = self.last_popup
            self.alert.clear()
            return item

    def _read_popup(self):
        # dump + pull 必须作为一个原子读取流程；主线程 read_xml 也使用同一把锁。
        with UI_DUMP_LOCK:
            dump = subprocess.run(
                [self.adb, 'shell', 'uiautomator', 'dump', GUARDIAN_REMOTE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=ADB_TIMEOUT, check=False)
            if dump.returncode != 0:
                raise SoulError(f'守护线程 uiautomator dump 失败：{dump.stderr.strip() or dump.stdout.strip()}')
            pull = subprocess.run(
                [self.adb, 'pull', GUARDIAN_REMOTE, str(GUARDIAN_LOCAL)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=ADB_TIMEOUT, check=False)
            if pull.returncode != 0 or not GUARDIAN_LOCAL.exists():
                raise SoulError(f'守护线程 adb pull 失败：{pull.stderr.strip() or pull.stdout.strip()}')
            root = ET.parse(GUARDIAN_LOCAL).getroot()
        found = {}
        for n in root.iter('node'):
            rid = n.attrib.get('resource-id', '').rsplit('/', 1)[-1]
            if rid in ('tv_love_recall', 'tv_love_signature', 'tv_love_chat'):
                found[rid] = n.attrib.get('text', '').strip()
        if (found.get('tv_love_recall') == '同城奇遇'
                and found.get('tv_love_chat') == '立即私聊'
                and found.get('tv_love_signature')):
            return {'type': 'same_city_encounter',
                    'username': found['tv_love_signature'],
                    'fingerprint': ('同城奇遇', found['tv_love_signature'])}
        return None

    def _watch(self):
        while not self.stop_event.is_set():
            if not self.enabled.wait(timeout=0.2):
                continue
            if self.stop_event.is_set():
                break
            try:
                found = self._read_popup()
                if found:
                    fingerprint = found.get('fingerprint')
                    with self.lock:
                        self.last_popup = found
                        self.last_error = None
                        is_new_event = fingerprint != self._active_fingerprint
                        self._active_fingerprint = fingerprint
                    # 同一个弹窗持续显示时只触发一次；消失后再次出现才算新事件。
                    if is_new_event:
                        self.alert.set()
                else:
                    with self.lock:
                        self._active_fingerprint = None
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
            self.stop_event.wait(self.interval)

def run(*args, check=True, capture=False, timeout=ADB_TIMEOUT):
    p = subprocess.run([ADB, *map(str,args)], text=True, capture_output=capture, timeout=timeout)
    if check and p.returncode:
        detail = p.stderr if capture else ''
        raise SoulError(f'ADB命令失败：{args}\n{detail}')
    return p

def read_xml():
    # 与后台守护线程共用同一把锁，防止两个 uiautomator dump 同时运行。
    with UI_DUMP_LOCK:
        dump = run('shell','uiautomator','dump',REMOTE,check=False,capture=True)
        if dump.returncode != 0:
            raise SoulError(f'uiautomator dump 失败：{dump.stderr.strip() or dump.stdout.strip()}')
        pull = run('pull',REMOTE,str(LOCAL),check=False,capture=True)
        if pull.returncode != 0 or not LOCAL.exists():
            raise SoulError(f'XML 拉取失败：{pull.stderr.strip() or pull.stdout.strip()}')
        try:
            return ET.parse(LOCAL).getroot()
        except Exception as e:
            raise SoulError(f'XML解析失败：{e}')

def short_id(node): return node.attrib.get('resource-id','').rsplit('/',1)[-1]
def nodes(root, rid): return [n for n in root.iter('node') if short_id(n)==rid]
def first(root,rid):
    found=nodes(root,rid)
    return found[0] if found else None

def wait_for_expected_chat(expected_username, timeout_seconds=9):
    """Wait for the actual, matching private chat UI before launching AI.

    Never use arbitrary tv_title nodes from profile panels as the chat name.
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while True:
        attempts += 1
        try:
            root = read_xml()
            # Match ONLY title text nested in the real chat title bar.
            titles = []
            for title_area in nodes(root, 'c_ct_chat_title_info'):
                titles.extend(
                    child.attrib.get('text', '').strip()
                    for child in title_area.iter('node')
                    if short_id(child) == 'tv_title'
                    and child.attrib.get('text', '').strip()
                )
            has_input = first(root, 'et_sendmessage') is not None
            if len(titles) == 1 and titles[0] == expected_username and has_input and not popup(root):
                print("🔒 运行状态已记录，个人信息已隐藏")
                return
            # A different confirmed chat is a serious mismatch, not loading delay.
            if titles and titles[0] != expected_username and has_input:
                raise SoulError(f'❌ 私聊对象不匹配：预期 {expected_username}，实际 {titles[0]}；安全停止')
            print(f'⏳ 等待私聊加载：第{attempts}次，标题={titles or "未出现"}，输入框={has_input}')
        except SoulError as exc:
            if '私聊对象不匹配' in str(exc):
                raise
            print(f'⚠️ 私聊读取暂时失败：{exc}')
        if time.monotonic() >= deadline:
            raise SoulError(f'等待 {expected_username} 私聊加载超时；未调用AI、未发送')
        time.sleep(0.7)


def bounds(node):
    nums=list(map(int,re.findall(r'\d+',node.attrib.get('bounds',''))))
    if len(nums)!=4: raise SoulError('控件没有有效bounds')
    return nums

def tap_node(node):
    x1,y1,x2,y2=bounds(node)
    run('shell','input','tap',(x1+x2)//2,(y1+y2)//2)

def text_of(root): return [n.attrib.get('text','').strip() for n in root.iter('node') if n.attrib.get('text','').strip()]
def popup(root):
    # Soul 不同版本/页面层级的弹窗根节点 ID 可能变化。
    # 以本次真实 XML 已确认的三个关键控件为准，不强制依赖旧 root ID。
    title=first(root,'tv_love_recall')
    button=first(root,'tv_love_chat')
    name=first(root,'tv_love_signature')
    return (title is not None and title.attrib.get('text','').strip()=='同城奇遇'
            and button is not None and button.attrib.get('text','').strip()=='立即私聊'
            and name is not None and bool(name.attrib.get('text','').strip()))

def main_list_snapshot(root):
    """提取主聊天列表当前可见内容，用于判断是否已到列表底部。

    只比较列表区域内的可见文本，不依赖固定页数；滑动后内容连续两次不变，
    才认为已经到达底部，避免因为单次滑动未生效而过早结束。
    """
    container = first(root, 'conversation_list')
    if container is None:
        raise SoulError('主聊天列表容器 conversation_list 不存在')
    _, top, _, bottom = bounds(container)
    items = []
    for n in root.iter('node'):
        text = n.attrib.get('text', '').strip()
        if not text:
            continue
        try:
            x1, y1, x2, y2 = bounds(n)
        except SoulError:
            continue
        if y2 <= top or y1 >= bottom:
            continue
        # 忽略底部导航及系统状态等列表外内容。
        items.append((short_id(n), text))
    return tuple(items)


def is_main_chat_page(root):
    """只在 Soul 主聊天列表页面启用弹窗守护。

    依据用户提供的 soul_main.xml：conversation_list 是主聊天列表容器，
    main_tab_msg_text='聊天' 是底部聊天页签；私聊页有 et_sendmessage，
    因此明确排除私聊页面。"""
    has_list = first(root, 'conversation_list') is not None
    has_chat_tab = any(
        n.attrib.get('text', '').strip() == '聊天'
        for n in nodes(root, 'main_tab_msg_text')
    )
    is_private_chat = first(root, 'et_sendmessage') is not None
    return has_list and has_chat_tab and not is_private_chat

def skip_failed_private_chat(expected_username, processed_keys, active_chat, guardian):
    """不重发失败的草稿；安全离开已确认的私聊并继续下一人。"""
    root = read_xml()
    if is_main_chat_page(root):
        print('⏭️ 失败后已在主聊天列表，跳过当前任务')
    else:
        title = first(root, 'tv_title')
        actual = title.attrib.get('text', '').strip() if title is not None else ''
        # 必须是可核实的同一聊天，才允许点击返回。
        if actual != expected_username or first(root, 'et_sendmessage') is None:
            raise SoulError(f'无法核对失败后的私聊身份（期待 {expected_username}，实际 {actual or "未知"}）；保留现场防误操作')
        try:
            back_to_main_once()
        except SoulError as exc:
            # 键盘遮挡返回按钮时，只关闭键盘，不做盲目多次返回。
            root = read_xml()
            inp = first(root, 'et_sendmessage')
            title = first(root, 'tv_title')
            if (inp is None or title is None or
                    title.attrib.get('text', '').strip() != expected_username or
                    inp.attrib.get('focused') != 'true'):
                raise
            run('shell', 'input', 'keyevent', '4')
            time.sleep(0.5)
            back_to_main_once()
        print("🔒 运行状态已记录，个人信息已隐藏")
    current = read_xml()
    if not is_main_chat_page(current):
        raise SoulError('失败后的返回未能确认主消息列表；保留现场')
    processed_keys.add(chat_task_key(active_chat))
    guardian.enable(False)
    check_popup_chain_from_main(live=True)
    guardian.enable(is_main_chat_page(read_xml()))
    return read_xml()


def back_to_main_once():
    """只执行一次经过控件确认的返回，不在这里分派其他任务。"""
    root=read_xml()
    b=first(root,'item_left_back')
    if b is None:
        raise SoulError('当前页面未找到 item_left_back，拒绝盲目返回')
    tap_node(b); time.sleep(1.5)
    root=read_xml()
    if first(root,'et_sendmessage') is not None:
        raise SoulError('返回后仍处于私聊页面')
    return root


def check_popup_chain_from_main(live=False):
    """当前已在主聊天页面：持续检查并处理弹窗，直到页面上不再有弹窗。

    不限制弹窗数量。若处理后仍是同一个弹窗且页面没有变化，视为点击/返回未生效，
    安全停止，避免对同一弹窗无限重复点击。
    """
    previous_fingerprint = None
    while True:
        root = read_xml()
        if not popup(root):
            print('✓ 主聊天页面弹窗检查通过：当前没有待处理弹窗')
            return root

        name = first(root, 'tv_love_signature')
        username = name.attrib.get('text', '').strip() if name is not None else '昵称未读到'
        fingerprint = ('同城奇遇', username)
        print("🔒 运行状态已记录，个人信息已隐藏")
        if not live:
            print('DRY-RUN：识别到弹窗，但不点击；停止后续动作')
            return root

        if fingerprint == previous_fingerprint:
            raise SoulError(
                f'处理后仍重复出现同一个弹窗（{username}），页面可能没有成功跳转；'
                '为避免重复发送，安全停止'
            )

        previous_fingerprint = fingerprint
        # 处理一条弹窗；处理器返回主聊天页后，while 会重新读取最新页面。
        handled = handle_popup(live=True, return_after=True, check_after=False)
        if not handled:
            raise SoulError('检测到弹窗，但弹窗处理器未能确认处理；安全停止')


def return_to_main_and_check_popup(live=False):
    """统一返回入口：回到主聊天页后立即检查弹窗，直到没有弹窗。"""
    back_to_main_once()
    return check_popup_chain_from_main(live=live)

def back_chat(check_popup_after=True, live=False):
    """兼容旧调用名；默认每次返回主聊天后都检查弹窗。"""
    if check_popup_after:
        return return_to_main_and_check_popup(live=live)
    return back_to_main_once()

def send_opening_if_safe(username):
    """同城奇遇专用输入适配：先激活自定义 View，再输入后查找动态发送按钮。"""
    root = read_xml()
    title = first(root, 'tv_title')
    if title is None or title.attrib.get('text', '').strip() != username:
        raise SoulError(f'聊天对象校验失败：期望 {username!r}')
    if OPENING in text_of(root):
        print("🔒 运行状态已记录，个人信息已隐藏")
        return False

    field = first(root, 'et_sendmessage')
    if field is None:
        raise SoulError('未找到 et_sendmessage 输入控件')
    # 真实 XML 已确认 et_sendmessage 是可点击的 android.view.View，
    # 发送按钮 btn_send 则要在输入文字后才出现，不能预先要求它存在。
    current = field.attrib.get('text', '').strip()
    if current not in ('', '发消息', '发消息或按住说话'):
        raise SoulError(f'输入框已有内容（{current!r}），停止以免覆盖')
    if field.attrib.get('clickable') != 'true':
        raise SoulError('et_sendmessage 当前不可点击，拒绝盲目输入')

    tap_node(field)
    time.sleep(0.6)
    focused_root = read_xml()
    focused_field = first(focused_root, 'et_sendmessage')
    if focused_field is None or focused_field.attrib.get('focused') != 'true':
        raise SoulError('点击后输入框未获得焦点，未输入、未发送')

    # ADB_INPUT_TEXT 使用参数传递，不拼接 shell 命令。
    run('shell', 'am', 'broadcast', '-a', 'ADB_INPUT_TEXT', '--es', 'msg', OPENING)
    time.sleep(1)
    root = read_xml()
    field = first(root, 'et_sendmessage')
    button = first(root, 'btn_send')
    if field is None or field.attrib.get('text', '').strip() != OPENING:
        raise SoulError('开场白输入验证失败；未点击发送')
    if button is None or button.attrib.get('clickable') != 'true':
        raise SoulError('输入后未出现可点击的 btn_send；消息保留在输入框，不自动发送')

    tap_node(button)
    time.sleep(2)
    root = read_xml()
    if OPENING not in text_of(root):
        raise SoulError('发送后聊天记录未找到开场白；不自动重发')
    print("🔒 运行状态已记录，个人信息已隐藏")
    return True

def handle_popup(live, return_after=True, check_after=True):
    root=read_xml()
    if not popup(root): return False
    name=first(root,'tv_love_signature')
    username=name.attrib.get('text','').strip() if name is not None else ''
    button=first(root,'tv_love_chat')
    print("🔒 运行状态已记录，个人信息已隐藏")
    if not live:
        print('DRY-RUN：只识别，不点击'); return True
    if not username or button is None:
        raise SoulError('弹窗昵称或立即私聊按钮缺失，拒绝点击')
    tap_node(button); time.sleep(2)
    send_opening_if_safe(username)
    if return_after:
        # 只返回一次；由统一入口负责决定是否继续检查下一条弹窗。
        back_to_main_once()
        if check_after:
            check_popup_chain_from_main(live=True)
    return True

def main_page_tasks(root):
    """从主聊天页 XML 识别任务；不把所有红点混成一个未读计数。

    依据用户本次提供的真实 XML 层级：
    - 新招呼：tv_greeting_title / tv_greeting_more，父级 content_layout 可点击
    - 奇遇铃：c_ct_system_notice_name / latestSystemMessage，父级 swipe_msg_header 可点击
    - 普通聊天：list_itease_layout 行内含 name、tv_from、unread_msg_number；
      可点击的是第 6 层 ViewGroup（行内 clickable=true）。
    """
    tasks = {'greeting': None, 'bell': None, 'chats': []}
    parent = {child: par for par in root.iter() for child in par}

    title = first(root, 'tv_greeting_title')
    greeting_count = first(root, 'tv_greeting_more')
    if title is not None:
        p = title
        while p is not None and short_id(p) != 'content_layout':
            p = parent.get(p)
        if p is not None and p.attrib.get('clickable') == 'true':
            tasks['greeting'] = {
                'label': title.attrib.get('text', '').strip(),
                'count_text': greeting_count.attrib.get('text', '').strip() if greeting_count is not None else '',
                'bounds': p.attrib.get('bounds', '')}

    bell_title = first(root, 'c_ct_system_notice_name')
    bell_message = None
    if bell_title is not None:
        p = bell_title
        while p is not None and short_id(p) != 'swipe_msg_header':
            p = parent.get(p)
        if p is not None and p.attrib.get('clickable') == 'true':
            # Match the preview inside this bell row, not an earlier unrelated system card.
            bell_message = next((n for n in p.iter('node') if short_id(n) == 'latestSystemMessage'), None)
            tasks['bell'] = {
                'label': bell_title.attrib.get('text', '').strip(),
                'message': bell_message.attrib.get('text', '').strip() if bell_message is not None else '',
                'bounds': p.attrib.get('bounds', '')}

    # 只把同时具备昵称、来源和 unread_msg_number 的普通列表行认作未读聊天。
    for row in nodes(root, 'list_itease_layout'):
        row_nodes = list(row.iter('node'))
        unread = next((n for n in row_nodes if short_id(n) == 'unread_msg_number'), None)
        if unread is None:
            continue
        name_node = next((n for n in row_nodes if short_id(n) == 'name' and n.attrib.get('text','').strip()), None)
        source_node = next((n for n in row_nodes if short_id(n) == 'tv_from' and n.attrib.get('text','').strip()), None)
        preview_node = next((n for n in row_nodes if short_id(n) == 'message' and n.attrib.get('text','').strip()), None)
        if name_node is None:
            continue
        # 从行节点向上寻找 XML 中明确标记 clickable=true 的最小祖先。
        clickable = None
        p = row
        while p is not None:
            if p.attrib.get('clickable') == 'true':
                clickable = p
                break
            p = parent.get(p)
        if clickable is None:
            continue
        try:
            x1, y1, x2, y2 = bounds(clickable)
        except SoulError:
            continue
        tasks['chats'].append({
            'username': name_node.attrib.get('text','').strip(),
            'source': source_node.attrib.get('text','').strip() if source_node is not None else '',
            'bounds': clickable.attrib.get('bounds',''),
            'x': (x1+x2)//2, 'y': (y1+y2)//2,
            'unread_text': unread.attrib.get('text','').strip(),
            'preview': preview_node.attrib.get('text','').strip() if preview_node is not None else ''})
    return tasks


def unread_badges():
    """兼容旧调用名，但现在返回 XML 关联到具体用户的未读聊天行，不再扫红色像素。"""
    root = read_xml()
    if not is_main_chat_page(root):
        return []
    return main_page_tasks(root)['chats']

def launch_chat_script(path):
    """Only distinguish a confirmed no-reply-needed from a genuine safety error."""
    command = ['python3', '-u', str(path)] + (['--send'] if Path(path).name in ('soul_ai_reader_v6_13.py', 'soul_ai_reader_v6_14_active.py') else [])
    lines = []
    with subprocess.Popen(command, cwd=BASE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            lines.append(line)
        code = process.wait()
    output = ''.join(lines)
    if (Path(path).name in ('soul_ai_reader_v6_13.py', 'soul_ai_reader_v6_14_active.py') and code == 40
            and '没有发现对方文字消息' in output
            and '没有符合自动发送条件的回复' in output):
        print('⏭️ 对方没有可回复内容：不发送，返回主聊天列表')
        return 22
    return code

def inspect_special_lists(root):
    tasks = main_page_tasks(root)
    g = tasks['greeting']
    b = tasks['bell']
    print('新招呼入口：' + (f"{g['label']}（{g['count_text'] or '数量未读到'}）" if g else '未发现'))
    print('奇遇铃入口：' + (f"{b['label']}（{b['message'] or '有入口提醒'}）" if b else '未发现'))
    for item in tasks['chats']:
        print("🔒 运行状态已记录，个人信息已隐藏")
    return tasks

def build_task_queue(tasks):
    """按优先级生成只读任务队列；不执行尚未验证的入口动作。"""
    queue = []
    for item in tasks.get('chats', []):
        queue.append({'priority': 1, 'kind': 'chat', 'label': item['username'], 'data': item})
    if tasks.get('greeting'):
        queue.append({'priority': 2, 'kind': 'greeting', 'label': tasks['greeting'].get('label') or '收到的新招呼', 'data': tasks['greeting']})
    if tasks.get('bell'):
        queue.append({'priority': 3, 'kind': 'bell', 'label': tasks['bell'].get('label') or '奇遇铃-稍后再聊', 'data': tasks['bell']})
    return sorted(queue, key=lambda x: x['priority'])


def process_special_entry(kind, guardian, max_users=1, all_bell=False):
    """Process one verified special list; child process owns its send safeguards."""
    if kind not in ('greeting', 'bell'):
        raise SoulError('未知特殊任务类型')
    script = GREETING_BATCH if kind == 'greeting' else BELL_BATCH
    if not script.is_file():
        raise SoulError(f'缺少特殊入口处理程序：{script.name}')
    root = read_xml()
    if not is_main_chat_page(root) or popup(root):
        raise SoulError('特殊入口启动前不在无遮挡主消息页面，拒绝点击')
    task = main_page_tasks(root)[kind]
    if not task:
        print(f'ℹ️ {kind} 入口已消失，安全跳过')
        return False
    marker = 'tv_greeting_title' if kind == 'greeting' else 'c_ct_system_notice_name'
    expected_label = task['label']
    if kind == 'greeting' and expected_label != '收到的新招呼':
        raise SoulError(f'新招呼标题发生变化：{expected_label}')
    if kind == 'bell' and '稍后再聊' not in expected_label:
        raise SoulError(f'奇遇铃标题发生变化：{expected_label}')
    matching = [n for n in nodes(root, marker) if n.attrib.get('text','').strip() == expected_label]
    if len(matching) != 1:
        raise SoulError('特殊入口标题不唯一，停止点击')
    # Do not use a stale coordinate after checking for a popup.
    found = []
    parent = {child: par for par in root.iter() for child in par}
    button = matching[0]
    target_id = 'content_layout' if kind == 'greeting' else 'swipe_msg_header'
    while button is not None and short_id(button) != target_id:
        button = parent.get(button)
    if button is None or button.attrib.get('clickable') != 'true':
        raise SoulError('特殊入口不可唯一点击')
    guardian.enable(False)
    print(f'▶ 进入特殊入口：{expected_label}')
    tap_node(button)
    time.sleep(1.4)
    current = read_xml()
    if kind == 'greeting':
        if first(current, 'rv_new_greeting_list') is None or '收到的新招呼' not in text_of(current):
            raise SoulError('点击后未确认进入新招呼列表，保留现场')
        command = [sys.executable, '-u', str(script), '--live', '--max', str(max_users)]
    else:
        if first(current, 'recycler_view') is None or '奇遇铃-稍后再聊' not in text_of(current):
            raise SoulError('点击后未确认进入奇遇铃稍后再聊列表，保留现场')
        command = ([sys.executable, '-u', str(script), '--live', '--all', '--return-main']
                   if all_bell else [sys.executable, '-u', str(script), '--live', '--max', str(max_users), '--return-main'])
    result = subprocess.run(command, cwd=BASE, check=False, timeout=(3600 if all_bell else 240))
    if result.returncode != 0:
        raise SoulError(f'{script.name} 返回错误码 {result.returncode}；保留现场，不重试')
    current = read_xml()
    # Greeting batch may leave a nonempty (already-handled) list open.
    if kind == 'greeting' and not is_main_chat_page(current):
        if first(current, 'rv_new_greeting_list') is None or '收到的新招呼' not in text_of(current):
            raise SoulError('新招呼批处理后页面未知，禁止返回')
        buttons = [n for n in nodes(current, 'ibtn_back') if n.attrib.get('clickable') == 'true' and n.attrib.get('enabled') == 'true']
        if len(buttons) != 1:
            raise SoulError('新招呼列表返回按钮不唯一')
        tap_node(buttons[0])
        time.sleep(1.5)
        current = read_xml()
    if not is_main_chat_page(current):
        raise SoulError('特殊入口处理结束但未确认返回主消息页面')
    print(f'✅ {expected_label} 处理结束，已返回主消息页面')
    guardian.enable(True)
    return True


def navigate_special_entry_without_send(kind, guardian):
    """只导航：从已核实的主列表进入特殊列表、返回；不调用发送模块。"""
    if kind not in ("greeting", "bell"):
        raise SoulError("未知特殊入口类型")
    root = read_xml()
    if popup(root) or not is_main_chat_page(root):
        raise SoulError("导航前未确认无弹窗主消息列表")
    task = main_page_tasks(root)[kind]
    if not task:
        return False
    label = task["label"]
    if kind == "greeting" and label != "收到的新招呼":
        raise SoulError("新招呼标题与预期不一致")
    if kind == "bell" and not ("奇遇铃" in label and "稍后再聊" in label):
        raise SoulError("奇遇铃标题与预期不一致")
    marker = "tv_greeting_title" if kind == "greeting" else "c_ct_system_notice_name"
    wanted = "content_layout" if kind == "greeting" else "swipe_msg_header"
    candidates = [n for n in nodes(root, marker)
                  if n.attrib.get("text", "").strip() == label]
    if len(candidates) != 1:
        raise SoulError("特殊入口标题无法唯一定位")
    parents = {child: par for par in root.iter() for child in par}
    button = candidates[0]
    while button is not None and short_id(button) != wanted:
        button = parents.get(button)
    if (button is None or button.attrib.get("clickable") != "true"
            or button.attrib.get("enabled") != "true"):
        raise SoulError("特殊入口按钮未经可靠确认")
    guardian.enable(False)
    tap_node(button)
    time.sleep(1.5)
    page = read_xml()
    if kind == "greeting":
        valid = (not is_main_chat_page(page)
                 and first(page, "rv_new_greeting_list") is not None
                 and "收到的新招呼" in text_of(page)
                 and first(page, "et_sendmessage") is None)
    else:
        valid = (not is_main_chat_page(page)
                 and first(page, "recycler_view") is not None
                 and "奇遇铃-稍后再聊" in text_of(page)
                 and first(page, "et_sendmessage") is None)
    if not valid:
        raise SoulError("导航后未确认特殊列表；保留现场")
    print(f"✅ 已确认进入 {kind} 列表（未进入任何私聊）")
    backs = [n for n in nodes(page, "ibtn_back")
             if n.attrib.get("clickable") == "true"
             and n.attrib.get("enabled") == "true"]
    if len(backs) != 1:
        raise SoulError("列表返回按钮无法唯一确认")
    tap_node(backs[0])
    time.sleep(1.5)
    if not is_main_chat_page(read_xml()):
        raise SoulError("返回后未确认主消息列表；保留现场")
    print(f"✅ 已从 {kind} 返回主消息列表；未调用AI、未发送")
    return True


def test_special_navigation(guardian, *, kind="bell", max_swipes=25):
    """在主消息列表动态寻找特殊入口并做单次导航测试。"""
    previous = None
    unchanged = 0
    found = False
    guardian.enable(False)
    try:
        for index in range(max_swipes + 1):
            root = read_xml()
            if popup(root) or not is_main_chat_page(root):
                raise SoulError("导航扫描时不是无遮挡主消息列表；停止")
            tasks = main_page_tasks(root)
            print(f"导航扫描 第{index + 1}屏：{kind}={'发现' if tasks[kind] else '无'}")
            if tasks[kind]:
                # 进入前由 navigate_special_entry_without_send 再读取一次 XML。
                found = navigate_special_entry_without_send(kind, guardian)
                break
            snapshot = main_list_snapshot(root)
            unchanged = unchanged + 1 if snapshot == previous else 0
            if unchanged >= 2:
                print(f"ℹ️ 列表已到末尾，未发现 {kind}")
                break
            previous = snapshot
            if index >= max_swipes:
                raise SoulError("导航达到扫描上限，尚未证明列表到达底部")
            run("shell", "input", "swipe", 540, 1850, 540, 650, 450)
            time.sleep(0.5)
    finally:
        current = read_xml()
        if not popup(current) and is_main_chat_page(current):
            return_main_list_to_top_dynamic(guardian, max_swipes=max_swipes+12)
        else:
            print("⚠️ 当前不在无遮挡主聊天页，保留现场不回顶")
        guardian.enable(False)
    print(f"🔒 导航测试结束：{'找到并返回' if found else '未找到入口'}；无AI、无发送")


def chat_task_key(item):
    # 预览或未读数变化时，视为新的聊天任务。
    return (item.get('username', ''), item.get('source', ''),
            item.get('unread_text', ''), item.get('preview', ''))


def reconcile_processed_chat_keys(root, processed_keys):
    # 仅当已处理用户仍在当前可见页、但未读标记消失时，解除去重。
    # 滚动到其他页不会清除其他页的处理记录。
    if not is_main_chat_page(root):
        return
    for row in nodes(root, 'list_itease_layout'):
        row_nodes = list(row.iter('node'))
        name_node = next((n for n in row_nodes if short_id(n) == 'name' and n.attrib.get('text','').strip()), None)
        if name_node is None:
            continue
        username = name_node.attrib.get('text','').strip()
        unread = next((n for n in row_nodes if short_id(n) == 'unread_msg_number'), None)
        if unread is None:
            processed_keys.difference_update(k for k in tuple(processed_keys) if k[0] == username)


def service_guardian_alert(guardian, live=False):
    """主线程响应主聊天页面的弹窗信号；私聊页面不运行守护监控。"""
    alert = guardian.consume_alert()
    if not alert:
        return False
    username = alert.get('username', '未知用户')
    print("🔒 运行状态已记录，个人信息已隐藏")
    guardian.enable(False)  # 主线程接管前暂停只读守护线程
    try:
        if not live:
            print('DRY-RUN：已暂停普通任务；只报告弹窗，不点击、不发送')
            return True
        check_popup_chain_from_main(live=True)
        return True
    finally:
        # 只有确认已经回到主聊天列表，才重新开启弹窗监控。
        try:
            current = read_xml()
            guardian.enable(is_main_chat_page(current))
        except Exception:
            guardian.enable(False)


def guardian_wait(seconds, guardian, live=False):
    """可被弹窗信号打断的等待。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if service_guardian_alert(guardian, live=live):
            return True
        time.sleep(min(0.1, max(0, end-time.monotonic())))
    return service_guardian_alert(guardian, live=live)


def idle_until_next_poll(guardian, live=False, total_seconds=POLL_SECONDS):
    """主聊天页无待办时进入待机；弹窗信号或可见未读会提前唤醒。

    弹窗由后台守护线程约每 0.8 秒只读监测；未读数字在主线程每隔
    IDLE_BADGE_CHECK_SECONDS 秒检查一次。待机不操作私聊、不发送消息。
    到达巡检周期后返回主循环，重新扫描主聊天列表的所有可见分页。
    """
    print(f'当前没有待处理消息，进入待机；每 {IDLE_BADGE_CHECK_SECONDS}s 检查可见未读，'
          f'每 {total_seconds}s 重新巡检列表')
    deadline = time.monotonic() + total_seconds
    initial_root = read_xml()
    initial_tasks = main_page_tasks(initial_root) if is_main_chat_page(initial_root) else {'chats': []}
    initial_fingerprint = tuple((x['username'], x['source'], x['bounds']) for x in initial_tasks['chats'])
    while time.monotonic() < deadline:
        if service_guardian_alert(guardian, live=live):
            print('待机被同城奇遇弹窗打断：优先处理完成后重新巡检')
            return 'popup'
        remaining = deadline - time.monotonic()
        guardian_wait(min(IDLE_BADGE_CHECK_SECONDS, max(0.1, remaining)), guardian, live=live)
        if service_guardian_alert(guardian, live=live):
            print('待机被同城奇遇弹窗打断：优先处理完成后重新巡检')
            return 'popup'
        # LIVE 与 DRY-RUN 都允许只读截图识别未读标记；DRY-RUN 只报告并唤醒巡检，
        # 不点击聊天、不输入、不发送。这样才能在安全模式下验证“待机→新消息→唤醒”。
        current_root = read_xml()
        current_tasks = main_page_tasks(current_root) if is_main_chat_page(current_root) else {'chats': []}
        current_fingerprint = tuple((x['username'], x['source'], x['bounds']) for x in current_tasks['chats'])
        if current_fingerprint != initial_fingerprint:
            mode = 'LIVE' if live else 'DRY-RUN（只报告）'
            print(f'待机期间未读聊天任务发生变化：{mode}，唤醒主调度')
            for item in current_tasks['chats']:
                print("🔒 运行状态已记录，个人信息已隐藏")
            return 'unread'
    print('待机结束：开始下一轮主聊天列表巡检')
    return 'timeout'


def guardian_swipe(guardian, live=False, x1=540, y1=1850, x2=540, y2=650, duration=450):
    """执行一段短滑动；后台并行监测，滑动结束立即检查中断信号。"""
    if service_guardian_alert(guardian, live=live):
        return False
    run('shell', 'input', 'swipe', x1, y1, x2, y2, duration)
    # guardian_wait 返回 True 表示期间已处理/接收到弹窗中断；调用方必须重建页面快照。
    interrupted = guardian_wait(0.15, guardian, live=live)
    return not interrupted and not guardian.alert.is_set()



def return_main_list_to_top_dynamic(guardian, max_swipes=30):
    """动态回到主聊天列表顶部；不使用固定滑动次数。

    连续两次向下回顶滑动后列表快照不变，视为已到顶部。
    max_swipes 只是防止异常页面导致无限循环的安全上限，不代表页数限制。
    """
    root = read_xml()
    if not is_main_chat_page(root):
        raise SoulError('回顶前不在主聊天列表，拒绝滑动')
    previous = main_list_snapshot(root)
    unchanged = 0
    for attempt in range(1, max_swipes + 1):
        if service_guardian_alert(guardian, live=True):
            root = read_xml()
            if not is_main_chat_page(root):
                raise SoulError('弹窗处理后未回到主聊天列表，无法继续回顶')
            previous = main_list_snapshot(root)
            unchanged = 0
            continue
        moved = guardian_swipe(guardian, live=True, y1=650, y2=1850, duration=300)
        if not moved:
            if service_guardian_alert(guardian, live=True):
                root = read_xml()
                if not is_main_chat_page(root):
                    raise SoulError('弹窗处理后未回到主聊天列表')
                previous = main_list_snapshot(root)
                unchanged = 0
                continue
            raise SoulError('回顶滑动未完成且没有弹窗中断信号，安全停止')
        root = read_xml()
        if not is_main_chat_page(root):
            raise SoulError('回顶过程中离开主聊天列表，安全停止')
        current = main_list_snapshot(root)
        if current == previous:
            unchanged += 1
            if unchanged >= 2:
                print(f'✓ 动态回顶完成：连续两次内容不变（尝试 {attempt} 次）')
                return root
        else:
            unchanged = 0
        previous = current
    raise SoulError(f'回顶超过安全上限 {max_swipes} 次仍未确认到顶部；请检查列表滚动状态')

def discover_special_entries(guardian, *, max_swipes=25, dispatch=False,
                             max_users=1, last_special_run=None,
                             kinds=('greeting', 'bell')):
    """Find unpinned greeting/bell entries across the main message list.

    No stale coordinates are retained: process_special_entry() reads fresh XML.
    In scan mode only scroll, with no chat opening and no AI requests.
    """
    found = set()
    previous = None
    unchanged = 0
    guardian.enable(False)  # avoid concurrent UIAutomator dumps while scanning
    try:
        for index in range(max_swipes + 1):
            root = read_xml()
            if popup(root):
                raise SoulError('扫描过程中出现弹窗，停止滑动，等待主程序处理')
            if not is_main_chat_page(root):
                raise SoulError('特殊入口扫描时不在主消息列表，安全停止')
            tasks = main_page_tasks(root)
            for kind in ('greeting', 'bell'):
                if tasks[kind]:
                    found.add(kind)
            print(f'特殊入口扫描 第{index+1}屏：新招呼={"有" if tasks["greeting"] else "无"}，奇遇铃={"有" if tasks["bell"] else "无"}')
            if dispatch:
                for kind in kinds:
                    if not tasks[kind]:
                        continue
                    if last_special_run is not None and time.monotonic() - last_special_run[kind] < 120:
                        print(f'ℹ️ {kind} 最近已尝试处理，本轮跳过')
                        continue
                    if last_special_run is not None:
                        last_special_run[kind] = time.monotonic()
                    # process_special_entry verifies the current page again before tapping.
                    process_special_entry(kind, guardian, max_users)
                    print(f'✅ 已完成本轮 {kind} 特殊入口任务')
                    return found, True
            snapshot = main_list_snapshot(root)
            unchanged = unchanged + 1 if snapshot == previous else 0
            if unchanged >= 2:
                print('✅ 主消息列表到底：连续两次滑动内容不变')
                break
            previous = snapshot
            if index == max_swipes:
                print('⚠️ 达到最大扫描页数，未证明已扫描到底')
                break
            run('shell', 'input', 'swipe', 540, 1850, 540, 650, 450)
            time.sleep(0.5)
        return found, False
    finally:
        # Never tap Back blindly. Scroll upward only from confirmed main list.
        root = read_xml()
        if not popup(root) and is_main_chat_page(root):
            return_main_list_to_top_dynamic(guardian, max_swipes=max_swipes + 10)
        else:
            print('⚠️ 当前页面或弹窗状态不允许回顶，保留现场')
        guardian.enable(is_main_chat_page(root) and not popup(root))


def main():
    ap=argparse.ArgumentParser(description='SOUL V2: popup guardian runs only on the main chat list')
    ap.add_argument('--live',action='store_true',help='允许执行已验证的弹窗/未读聊天动作；默认仅观察')
    ap.add_argument('--once',action='store_true',help='只跑一轮')
    ap.add_argument('--special-live', action='store_true', help='LIVE时额外启用新招呼及奇遇铃真实发送（默认关闭）')
    ap.add_argument('--bell-first', action='store_true', help='优先定位奇遇铃并实际处理；之后继续普通未读消息')
    ap.add_argument('--bell-visible-all', action='store_true', help='当前可见奇遇铃：处理所有未发送用户，逐人返回列表，完成后返回主消息页并结束')
    ap.add_argument('--bell-visible-once', action='store_true', help='奇遇铃入口当前可见时，直接真实处理最多 special-max 位并退出；不滑动扫描')
    ap.add_argument('--special-max', type=int, default=1, help='每次特殊入口最多处理人数（1-5，默认1）')
    ap.add_argument('--scan-special-only', action='store_true', help='仅扫描特殊入口并回顶：允许滑动，但绝不进入聊天或发送')
    ap.add_argument('--special-nav-only', choices=['bell', 'greeting'], help='仅导航指定特殊入口并返回；允许滑动与进入列表，不进入私聊、不发送')
    ap.add_argument('--special-scan-max', type=int, default=25, help='特殊入口扫描最多滑动次数（5-40）')
    ap.add_argument('--popup-once', action='store_true', help='只真实处理当前一个同城奇遇弹窗，返回主聊天后检查是否还有弹窗；不扫描未读、不处理其他任务')
    ap.add_argument('--chat-once', action='store_true', help='当前已经在指定私聊页时，只发送一次开场白并返回主聊天；不要求弹窗仍在页面上')
    ap.add_argument('--username', default='D老师', help='--chat-once 时用于核对聊天标题的昵称，默认 D老师')
    args=ap.parse_args()
    if not 1 <= args.special_max <= 5:
        ap.error("--special-max 必须在1至5之间")
    if args.special_live and not args.live:
        ap.error("--special-live 必须与 --live 一起使用")
    if args.bell_first and not (args.live and args.special_live):
        ap.error('--bell-first 需要同时指定 --live --special-live')
    if args.bell_visible_all and not (args.live and args.special_live):
        ap.error('--bell-visible-all 需要同时指定 --live --special-live')
    if args.bell_visible_all and (args.bell_first or args.bell_visible_once or args.special_nav_only or args.scan_special_only or args.popup_once or args.chat_once):
        ap.error('--bell-visible-all 不可与其他专项模式组合')
    if args.bell_visible_once and not (args.live and args.special_live):
        ap.error('--bell-visible-once 需要同时指定 --live --special-live')
    if args.bell_visible_once and (args.bell_first or args.special_nav_only or args.scan_special_only or args.popup_once or args.chat_once):
        ap.error('--bell-visible-once 不可与其他专项模式组合')
    if not 5 <= args.special_scan_max <= 40:
        ap.error('--special-scan-max 必须在5至40之间')
    if args.scan_special_only and (args.live or args.special_live or args.popup_once or args.chat_once or args.bell_first):
        ap.error('--scan-special-only 不可与任何 LIVE/发送测试模式一起使用')
    if args.special_nav_only and (args.live or args.special_live or args.popup_once or args.chat_once or args.scan_special_only or args.bell_first):
        ap.error('--special-nav-only 不可与 LIVE/其他测试模式组合')
    print('=== SOUL V6：统一调度 V3（默认观察）===')
    run('get-state')
    print('设备连接正常；模式：', 'LIVE' if args.live else 'DRY-RUN')
    guardian=PopupGuardian(ADB).start()
    print(f'弹窗守护已启动；目标轮询间隔：{GUARDIAN_INTERVAL:.1f}s；XML读取采用全局互斥锁')
    dry_run_popup_fingerprint = None
    # 跨主循环保存弹窗期间的未读快照，避免同一批未读标记反复触发提示。
    dry_run_popup_badge_signature = None
    processed_chat_keys = set()
    last_special_run = {"greeting": 0.0, "bell": 0.0}
    last_special_scan = 0.0
    try:
        if args.bell_visible_all or args.bell_visible_once:
            # No full-list scan. Operates only on a unique, visible bell row.
            guardian.enable(False)
            root = read_xml()
            if popup(root) or not is_main_chat_page(root):
                raise SoulError('当前不是无遮挡主消息列表，拒绝执行')
            matches = [n for n in nodes(root, 'c_ct_system_notice_name')
                       if n.attrib.get('text', '').strip() == '奇遇铃-稍后再聊']
            if len(matches) != 1:
                raise SoulError(f'当前屏幕没有唯一的奇遇铃入口（找到{len(matches)}处）；请把入口显示在屏幕中再运行')
            parent = {child: par for par in root.iter() for child in par}
            entry = matches[0]
            while entry is not None and short_id(entry) != 'swipe_msg_header':
                entry = parent.get(entry)
            if entry is None or entry.attrib.get('clickable') != 'true':
                raise SoulError('奇遇铃入口没有可靠可点击父节点；停止')
            print('🔔 当前屏幕已确认奇遇铃入口：直接调用批处理，不全列表扫描')
            process_special_entry('bell', guardian, max_users=args.special_max, all_bell=args.bell_visible_all)
            print('✅ 奇遇铃全部处理完成，已返回主消息页面；本次结束' if args.bell_visible_all
                  else '✅ 奇遇铃本次处理完成，已返回主消息页面；单次模式结束')
            return
        if args.special_nav_only:
            test_special_navigation(guardian, kind=args.special_nav_only, max_swipes=args.special_scan_max)
            return
        if args.scan_special_only:
            found, _ = discover_special_entries(guardian, max_swipes=args.special_scan_max)
            print("🔒 扫描结束，未进入聊天或发送消息；发现：", "、".join(sorted(found)) or "没有发现")
            return
        if args.chat_once:
            if not args.live:
                raise SoulError('--chat-once 必须同时指定 --live；该模式会真实发送消息')
            root = read_xml()
            title_node = first(root, 'tv_title')
            actual_name = title_node.attrib.get('text', '').strip() if title_node is not None else ''
            if first(root, 'et_sendmessage') is None or actual_name != args.username:
                raise SoulError(f'当前页面不是 {args.username} 的私聊页（实际标题：{actual_name or "未识别"}），拒绝发送')
            print(f'单聊天真实处理测试：当前已在 {actual_name} 私聊页面')
            send_opening_if_safe(actual_name)
            back_to_main_once()
            root = read_xml()
            if not is_main_chat_page(root):
                raise SoulError('发送后未能确认返回 Soul 主聊天列表')
            guardian.enable(True)
            check_popup_chain_from_main(live=True)
            print(f'✓ 单聊天真实处理测试结束：{actual_name}')
            return

        if args.popup_once:
            if not args.live:
                raise SoulError('--popup-once 必须同时指定 --live；该模式会真实点击并发送开场白')
            root = read_xml()
            if not is_main_chat_page(root):
                raise SoulError('当前不在 Soul 主聊天列表，拒绝执行单弹窗测试')
            guardian.enable(False)
            if not popup(root):
                print('当前主聊天页面没有同城奇遇弹窗；未执行任何点击或发送')
                return
            name_node = first(root, 'tv_love_signature')
            username = name_node.attrib.get('text', '').strip() if name_node is not None else '未知用户'
            print("🔒 运行状态已记录，个人信息已隐藏")
            handled = handle_popup(live=True, return_after=True, check_after=False)
            if not handled:
                raise SoulError('弹窗处理器未确认处理成功')
            root = read_xml()
            if not is_main_chat_page(root):
                raise SoulError('处理后未返回主聊天列表')
            guardian.enable(True)
            # 每次返回主聊天都立即检查；本测试只检查，不自动处理第二个弹窗。
            root = read_xml()
            if popup(root):
                name_node = first(root, 'tv_love_signature')
                next_name = name_node.attrib.get('text', '').strip() if name_node is not None else '未知用户'
                print(f'⚠️ 已返回主聊天，但又出现新弹窗：{next_name}。单弹窗测试到此停止，未处理第二个弹窗。')
            else:
                print('✓ 已返回主聊天；返回后弹窗检查通过，目前没有新的弹窗')
            print("🔒 运行状态已记录，个人信息已隐藏")
            return

        if args.bell_first:
            print('🔔 优先处理奇遇铃：直接寻找入口并调用已验证的批处理（不是只导航）')
            root = read_xml()
            if not is_main_chat_page(root) or popup(root):
                raise SoulError('奇遇铃优先处理要求无遮挡的SOUL主消息列表')
            found, dispatched = discover_special_entries(
                guardian, max_swipes=args.special_scan_max,
                dispatch=True, max_users=args.special_max,
                last_special_run=last_special_run, kinds=('bell',))
            print('🔔 奇遇铃本次：', '已调用发送处理模块' if dispatched else '未发现可处理入口')
            # 返回主消息列表后，普通未读仍由原有保护机制处理。
        while True:
            # 先识别当前页面：只有主聊天列表才开启弹窗守护。
            root = read_xml()
            on_main = is_main_chat_page(root)
            guardian.enable(on_main)
            if on_main and service_guardian_alert(guardian, live=args.live):
                if args.once and not args.live:
                    break
                # LIVE 模式下弹窗处理后重新读取页面；DRY-RUN 不能因只报告弹窗
                # 就跳过后面的只读未读检测。
                if args.live:
                    continue

            # 重新读取，避免页面刚切换时沿用旧快照。
            root=read_xml()
            on_main = is_main_chat_page(root)
            guardian.enable(on_main)
            if not on_main:
                raise SoulError('当前不在 Soul 主聊天列表；弹窗守护已暂停，拒绝按主页面坐标继续操作')
            if popup(root):
                if not args.live:
                    name_node = first(root, 'tv_love_signature')
                    username = name_node.attrib.get('text', '').strip() if name_node is not None else '未知用户'
                    fingerprint = ('同城奇遇', username)
                    if fingerprint != dry_run_popup_fingerprint:
                        print("🔒 运行状态已记录，个人信息已隐藏")
                        dry_run_popup_fingerprint = fingerprint
                    # 弹窗只在主聊天页面出现。DRY-RUN 不点击弹窗，但仍然
                    # 每隔几秒做一次只读未读检查，验证新消息能否唤醒主调度。
                    # 不因弹窗遮挡就跳过未读检测，也不执行滑动/点击/发送。
                    wait_deadline = time.monotonic() + POLL_SECONDS
                    while time.monotonic() < wait_deadline:
                        service_guardian_alert(guardian, live=False)
                        badges_during_popup = unread_badges()
                        # 用具体聊天对象/来源/未读数/预览组成快照；同一快照只报告一次。
                        badge_signature = tuple(sorted(
                            (x.get('username', ''), x.get('source', ''),
                             x.get('unread_text', ''), x.get('preview', ''))
                            for x in badges_during_popup
                        ))
                        if badge_signature != dry_run_popup_badge_signature:
                            if badge_signature:
                                print(f'DRY-RUN：弹窗期间发现未读任务快照变化（{len(badges_during_popup)}项）；已记录，等待下一轮调度，不重复报告旧标记')
                            else:
                                print('DRY-RUN：弹窗期间未检测到可见未读聊天')
                            dry_run_popup_badge_signature = badge_signature
                        # 若弹窗已消失，尽早回到主循环重新判断页面状态。
                        time.sleep(min(IDLE_BADGE_CHECK_SECONDS,
                                       max(0.1, wait_deadline - time.monotonic())))
                        root = read_xml()
                        if not popup(root):
                            print('DRY-RUN：弹窗已消失，立即重新检查主聊天页面')
                            break
                    continue
                dry_run_popup_fingerprint = None
                dry_run_popup_badge_signature = None
                guardian.enable(False)  # 进入弹窗私聊前停止主页面监控
                check_popup_chain_from_main(live=True)
                # 弹窗处理器已返回主聊天页；确认页面后才恢复监控。
                guardian.enable(is_main_chat_page(read_xml()))
                continue

            dry_run_popup_fingerprint = None
            reconcile_processed_chat_keys(root, processed_chat_keys)
            page_tasks = inspect_special_lists(root)
            all_visible_chats = page_tasks['chats']
            badges = [item for item in all_visible_chats if chat_task_key(item) not in processed_chat_keys]
            page_tasks['chats'] = badges
            task_queue = build_task_queue(page_tasks)
            if all_visible_chats and len(badges) != len(all_visible_chats):
                print(f'任务去重：跳过 {len(all_visible_chats)-len(badges)} 条已处理且未读状态未变化的聊天')
            if service_guardian_alert(guardian, live=args.live):
                continue
            print(f"当前识别到 {len(task_queue)} 项待处理任务（普通聊天 {len(badges)}，新招呼 {1 if page_tasks['greeting'] else 0}，奇遇铃 {1 if page_tasks['bell'] else 0}）")
            if task_queue and not args.live:
                print('DRY-RUN：任务队列（只识别、不点击、不发送）：')
                for item in task_queue:
                    print(f"  P{item['priority']} | {item['kind']} | {item['label']}")
                if page_tasks['greeting'] or page_tasks['bell']:
                    print('注意：观察模式只列任务，不进行发送。')

            # P1 普通聊天先处理；新招呼/奇遇铃处理器尚未接入时，
            # 不让它们抢先阻断已经可用的普通聊天处理流程。
            # 当当前可见普通聊天处理完毕后，再安全停止并报告未接入入口。
            handled_chat_this_cycle = False
            if args.live and args.special_live and not badges:
                available = next((kind for kind in ('greeting', 'bell')
                    if page_tasks[kind] and time.monotonic() - last_special_run[kind] >= 120), None)
                if available:
                    last_special_run[available] = time.monotonic()
                    print(f'▶ 当前屏直接处理 {available}，无需先扫描整张列表')
                    process_special_entry(available, guardian, args.special_max)
                    if args.once:
                        print('✅ 当前特殊入口处理完成；--once 结束')
                        break
                    continue
            if badges and args.live:
                handled_chat_this_cycle = True
                guardian.enable(False)  # 进入私聊后不再按“主聊天页面”规则监控
                active_chat = badges[0]
                tap_x, tap_y = active_chat['x'], active_chat['y']
                print("🔒 运行状态已记录，个人信息已隐藏")
                run('shell','input','tap',tap_x,tap_y)
                wait_for_expected_chat(active_chat['username'])
                current_user_page = read_xml()
                if is_system_ai(current_user_page):
                    log_skip('chat-page-1', active_chat['username'], current_user_page, BASE)
                    code = 23
                else:
                    code=launch_chat_script(AI_READER)
                if code == 10:
                    print('🤫 SILENT：不等待10秒，直接返回主消息列表')
                    back_to_main_once()
                    code = 21
                if code == 0:
                    # 正常发送后才执行10秒监听。
                    if not CHAT_WATCH.exists():
                        raise SoulError(f'缺少聊天监听程序：{CHAT_WATCH.name}')
                    code=launch_chat_script(CHAT_WATCH)
                if code in (20,21):
                    # 20=AI主动结束本轮；21=监听超时且已确认返回主聊天列表。
                    processed_chat_keys.add(chat_task_key(active_chat))
                    print("🔒 运行状态已记录，个人信息已隐藏")
                    guardian.enable(False)
                    if code == 20:
                        back_to_main_once()
                    check_popup_chain_from_main(live=True)
                    guardian.enable(is_main_chat_page(read_xml()))
                elif code == 30:
                    print("🔒 运行状态已记录，个人信息已隐藏")

                    root = read_xml()
                    title = first(root, 'tv_title')

                    if title is None or title.attrib.get('text', '').strip() != active_chat['username']:
                        raise SoulError('人工接管时聊天对象不匹配，安全停止')

                    back_to_main_once()

                    root = read_xml()
                    if not is_main_chat_page(root):
                        raise SoulError('人工接管后未能确认返回消息列表')

                    processed_chat_keys.add(chat_task_key(active_chat))
                    guardian.enable(True)
                    print("✅ 当前用户已跳过，继续扫描其他未读消息")

                elif code == 23:
                    back_to_main_once()
                    root = read_xml()
                    if not is_main_chat_page(root):
                        raise SoulError('系统AI跳过后未回到主聊天列表')
                    processed_chat_keys.add(chat_task_key(active_chat))
                    guardian.enable(True)
                    print('✅ 已跳过系统AI，继续检查本屏其余未读')
                elif code == 22:
                    # 只有 AI 输出明确的“没有对方文字消息 + 无发送条件”才进入此分支。
                    back_to_main_once()
                    root = read_xml()
                    if not is_main_chat_page(root):
                        raise SoulError('无可回复内容后未确认返回主聊天页面')
                    processed_chat_keys.add(chat_task_key(active_chat))
                    guardian.enable(True)
                    print("🔒 运行状态已记录，个人信息已隐藏")
                elif code == 40:
                    print("🔒 运行状态已记录，个人信息已隐藏")
                    skip_failed_private_chat(active_chat['username'], processed_chat_keys, active_chat, guardian)
                elif code not in (0,10):
                    raise SoulError(f'未知状态码 {code}')
            else:
                if not args.live:
                    if badges:
                        print('DRY-RUN：已识别普通聊天任务，但不会点击或发送。')
                    elif not task_queue:
                        print('当前没有识别到可处理任务，准备进入待机。')

            # 有未读刚刚处理过：立即刷新任务队列，不先空扫多屏。
            if handled_chat_this_cycle and args.live:
                if args.once:
                    print('✅ 本次已处理普通聊天；--once 结束')
                    break
                print('↻ 已处理一位普通聊天，直接重新检查主列表')
                continue

            # 固定两屏；第2屏处理完一位后留在第2屏，直到该屏全部处理。
            if args.live and not badges:
                print('📜 第1屏无待回复，翻到第2屏')
            if not args.live:
                print('DRY-RUN：只观察第1屏，不滑动或发送')
            else:
                if service_guardian_alert(guardian, live=True):
                    continue
                print('正在检查主聊天列表第2屏（本轮最后一屏）')
                moved = guardian_swipe(guardian, live=True, duration=450)
                if not moved:
                    if service_guardian_alert(guardian, live=True):
                        continue
                    raise SoulError('第2屏滑动未完成，且没有弹窗中断信号')
                if service_guardian_alert(guardian, live=True):
                    continue
                root = read_xml()
                if not is_main_chat_page(root):
                    raise SoulError('滑动后未确认主聊天页面')
                guardian.enable(True)
                previous_snapshot = main_list_snapshot(root)
                unchanged_swipes = 0
                # 在同一屏持续处理，不对第2屏再次滑动，更不会进入第3屏。
                while True:
                    if service_guardian_alert(guardian, live=True):
                        root = read_xml()
                        if not is_main_chat_page(root):
                            raise SoulError('弹窗处理后未回主聊天页')
                    root = read_xml()
                    if not is_main_chat_page(root):
                        raise SoulError('第2屏处理期间离开主聊天列表')
                    # 后续分页也必须应用同一套去重键；否则已处理用户在第2页以后
                    # 仍带着旧未读标记时，会再次进入并重复调用狗头军师。
                    page_badges = unread_badges()
                    badges = [item for item in page_badges
                              if chat_task_key(item) not in processed_chat_keys]
                    if page_badges and len(badges) != len(page_badges):
                        print(f'第 2 页任务去重：跳过 '
                              f'{len(page_badges)-len(badges)} 条已处理且状态未变化的聊天')
                    if badges:
                        handled_chat_this_cycle = True
                        print(f'第 2 页发现 {len(badges)} 个待处理未读聊天')
                        guardian.enable(False)
                        active_chat = badges[0]
                        print("🔒 运行状态已记录，个人信息已隐藏")
                        run('shell','input','tap',active_chat['x'],active_chat['y'])
                        wait_for_expected_chat(active_chat['username'])
                        current_user_page = read_xml()
                        if is_system_ai(current_user_page):
                            log_skip('chat-page-2', active_chat['username'], current_user_page, BASE)
                            code = 23
                        else:
                            code=launch_chat_script(AI_READER)
                        if code == 10:
                            print('🤫 SILENT：直接返回本屏列表，不等待10秒')
                            back_to_main_once()
                            code = 21
                        if code == 0:
                            if not CHAT_WATCH.exists():
                                raise SoulError(f'缺少聊天监听程序：{CHAT_WATCH.name}')
                            code=launch_chat_script(CHAT_WATCH)
                        if code in (20,21):
                            # 20=AI主动结束本轮；21=监听超时且已确认返回主聊天列表。
                            processed_chat_keys.add(chat_task_key(active_chat))
                            print("🔒 运行状态已记录，个人信息已隐藏")
                            if code == 20:
                                back_to_main_once()
                            check_popup_chain_from_main(live=True)
                            root = read_xml()
                            guardian.enable(is_main_chat_page(root))
                            if not is_main_chat_page(root):
                                raise SoulError('聊天处理后未返回主聊天列表')
                            previous_snapshot = main_list_snapshot(root)
                            unchanged_swipes = 0
                        elif code == 30:
                            print("🔒 运行状态已记录，个人信息已隐藏")

                            root = read_xml()
                            title = first(root, 'tv_title')

                            if title is None or title.attrib.get('text', '').strip() != active_chat['username']:
                                raise SoulError('人工接管时聊天对象不匹配，安全停止')

                            back_to_main_once()

                            root = read_xml()
                            if not is_main_chat_page(root):
                                raise SoulError('人工接管后未能确认返回消息列表')

                            processed_chat_keys.add(chat_task_key(active_chat))
                            guardian.enable(True)

                            previous_snapshot = main_list_snapshot(root)
                            unchanged_swipes = 0

                            print("✅ 当前用户已跳过，继续扫描其他未读消息")

                        elif code == 23:
                            back_to_main_once()
                            root = read_xml()
                            if not is_main_chat_page(root):
                                raise SoulError('第2屏系统AI跳过后未返回列表')
                            processed_chat_keys.add(chat_task_key(active_chat))
                            guardian.enable(True)
                            previous_snapshot = main_list_snapshot(root)
                            unchanged_swipes = 0
                            print('✅ 已跳过系统AI，继续第2屏')
                        elif code == 22:
                            back_to_main_once()
                            root = read_xml()
                            if not is_main_chat_page(root):
                                raise SoulError('无可回复内容后未确认返回主聊天页面')
                            processed_chat_keys.add(chat_task_key(active_chat))
                            guardian.enable(True)
                            previous_snapshot = main_list_snapshot(root)
                            unchanged_swipes = 0
                            print("🔒 运行状态已记录，个人信息已隐藏")
                        elif code == 40:
                            print("🔒 运行状态已记录，个人信息已隐藏")
                            root = skip_failed_private_chat(active_chat['username'], processed_chat_keys, active_chat, guardian)
                            previous_snapshot = main_list_snapshot(root)
                            unchanged_swipes = 0
                        elif code not in (0,10):
                            raise SoulError(f'未知状态码 {code}')
                        continue

                    # 第2屏的普通未读处理完毕后，再处理同屏可见的特殊入口。
                    # 不进行第3屏及更深页扫描，保持双屏边界。
                    if args.live and args.special_live:
                        special_here = inspect_special_lists(root)
                        kind = next((k for k in ('greeting', 'bell')
                                     if special_here[k] and time.monotonic() - last_special_run[k] >= 120), None)
                        if kind:
                            last_special_run[kind] = time.monotonic()
                            print(f'▶ 第2屏普通未读已处理完，处理特殊入口：{kind}')
                            process_special_entry(kind, guardian, max_users=args.special_max)
                            # 特殊子程序可能改变列表位置；退出本屏，回顶后重启两屏检查。
                            print('↩️ 特殊任务结束，准备回顶重检两屏')
                            break
                    print('✅ 第2屏已无待回复消息；回顶部待机')
                    break

            # 页面恢复顶部采用动态内容判断，不再固定滑动三次。
            if args.live:
                root = read_xml()
                if is_main_chat_page(root):
                    root = return_main_list_to_top_dynamic(guardian)
            # 严格限制为两屏，不执行可能扫描3～19屏的特殊入口全列表发现。
            # 主页面当前可见特殊入口仍由上述入口逻辑处理。
            if args.once:
                break
            if not args.live and task_queue:
                print('DRY-RUN：当前仍有待处理任务记录；不标记为空闲。30秒后重新读取主聊天页面。')
                guardian_wait(POLL_SECONDS, guardian, live=False)
            else:
                idle_until_next_poll(guardian, live=args.live, total_seconds=POLL_SECONDS)
    finally:
        guardian.stop()

if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('\n用户停止。')
    except Exception as e: raise SystemExit(f'安全停止：{e}')
