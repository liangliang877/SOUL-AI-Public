#!/usr/bin/env python3
"""SOUL V6 新招呼模块：只读识别、草稿和指定对象单次安全发送。
默认只读；仅 --send --username 完全匹配后允许真实发送。
"""
import argparse
import json
import hashlib
import time
from datetime import datetime
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from soul_v7_system_ai import is_system_ai

MODEL = 'deepseek-v4-flash-ga-260731'
API_URL = 'https://ark.cn-beijing.volces.com/api/v3'


def rid(node):
    return node.attrib.get('resource-id', '').split('/')[-1]


def text(node):
    return node.attrib.get('text', '').strip()


def read_page_from_adb():
    with tempfile.TemporaryDirectory(prefix='soul_greeting_') as work:
        remote = f'/sdcard/soul_greeting_scan_{os.getpid()}.xml'
        local = str(Path(work) / 'page.xml')
        for attempt in range(1, 4):
            try:
                result = subprocess.run(['adb', 'shell', 'uiautomator', 'dump', remote],
                                        text=True, capture_output=True, timeout=45)
                if result.returncode == 0:
                    pulled = subprocess.run(['adb', 'pull', remote, local],
                                            text=True, capture_output=True, timeout=30)
                    if pulled.returncode == 0:
                        return ET.parse(local).getroot()
                    info = pulled.stderr
                else:
                    info = result.stderr + ' ' + result.stdout
            except (subprocess.TimeoutExpired, ET.ParseError) as exc:
                info = str(exc)
            print(f'⚠️ 第{attempt}次读取失败：{info[-180:]}')
        raise RuntimeError('连续3次读取失败，安全停止')


def read_list(root):
    lists = [n for n in root.iter('node') if rid(n) == 'rv_new_greeting_list']
    if len(lists) != 1:
        raise ValueError('没有唯一识别到新招呼列表')
    rows = []
    for n in lists[0].iter('node'):
        if rid(n) != 'item_content_root':
            continue
        names = [text(x) for x in n.iter('node') if rid(x) == 'name' and text(x)]
        if len(names) != 1:
            continue
        texts = [text(x) for x in n.iter('node') if text(x) and rid(x) != 'name']
        rows.append({'name': names[0], 'notes': texts[:8]})
    return rows


def read_detail(root):
    if not any(rid(n) == 'et_sendmessage' for n in root.iter('node')):
        raise ValueError('没有发现私聊输入框，拒绝当作招呼详情')
    titles = [text(n) for n in root.iter('node') if rid(n) == 'tv_title' and text(n)]
    names = [t for t in titles if not t.startswith('Ta的') and t not in ('收到的新招呼',)]
    if not names:
        raise ValueError('没有识别到聊天昵称')
    person = names[0]
    signatures = [t.removeprefix('Ta的签名：') for t in titles if t.startswith('Ta的签名：')]
    traits = [t for t in titles if t.startswith(('Ta的星座：', 'Ta的')) and not t.startswith('Ta的签名：')]
    all_texts = [text(n) for n in root.iter('node') if text(n)]
    is_greeting = any(rid(n) == 'tv_say_hello_button' for n in root.iter('node'))
    return {'name': person, 'signature': signatures[0] if signatures else '',
            'traits': traits[:10], 'is_greeting': is_greeting,
            'has_input': True, 'text_samples': all_texts[:20]}


def draft_intro(detail):
    key = os.getenv('ARK_API_KEY') or os.getenv('VOLCENGINE_API_KEY')
    if not key:
        raise RuntimeError('本机未设置 ARK_API_KEY；不会读取或输出密钥')
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError('缺少 openai 包：python3 -m pip install openai') from exc
    client = OpenAI(api_key=key, base_url=API_URL, timeout=30, max_retries=0)
    context = (f"昵称：{detail['name']}\n签名：{detail['signature']}\n"
               f"其他资料：{'；'.join(detail['traits'])}")
    response = client.responses.create(
        model=MODEL,
        instructions=('你是社交软件聊天开场白草稿助手。根据对方公开资料，生成一句自然、简短、有趣的中文开场白；'
                      '只输出一句话，15-35个汉字；不要编造自己经历，不要假设对方已经发来消息；'
                      '不要过度亲密、套话、施压或重复提问。'),
        input=context,
        max_output_tokens=1500,
    )
    answer = (getattr(response, 'output_text', '') or '').strip()
    if not answer:
        raise RuntimeError(f'模型未输出可见草稿，响应状态：{getattr(response, "status", "未知")}')
    return answer



def chat_title(root):
    for parent in root.iter('node'):
        if rid(parent) == 'c_ct_chat_title_info':
            titles = [text(n) for n in parent.iter('node') if rid(n) == 'tv_title' and text(n)]
            if len(titles) == 1:
                return titles[0]
    raise RuntimeError('无法唯一确认顶部聊天标题')


def center(node):
    nums = [int(n) for n in re.findall(r'\d+', node.attrib.get('bounds', ''))]
    if len(nums) != 4:
        raise RuntimeError('控件坐标缺失')
    return (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2


def adb_action(*args):
    result = subprocess.run(['adb', *[str(a) for a in args]], text=True,
                            capture_output=True, timeout=25)
    if result.returncode != 0:
        raise RuntimeError(f'ADB 操作失败：{result.stderr[-200:]}')
    return result


def one_node(root, suffix, *, enabled=False):
    nodes = [n for n in root.iter('node') if rid(n) == suffix and
             (not enabled or n.attrib.get('enabled') == 'true')]
    if len(nodes) != 1:
        raise RuntimeError(f'{suffix} 控件数量={len(nodes)}，拒绝操作')
    return nodes[0]


def count_visible_reply(root, reply):
    return sum(1 for n in root.iter('node')
               if rid(n) == 'content_text' and text(n) == reply)


def do_one_send(username, reply):
    # 专用待核实记录：不与普通聊天模块的去重文件互相覆盖。
    state_dir = Path(__file__).resolve().parent / 'soul_greeting_send_state'
    state_dir.mkdir(exist_ok=True)
    uid = hashlib.sha256(username.encode('utf-8')).hexdigest()[:20]
    pending = state_dir / f'{uid}.pending.json'
    sent = state_dir / f'{uid}.sent.json'
    if pending.exists() or sent.exists():
        raise RuntimeError('该用户已有待核实或成功发送记录；为避免重复发送，安全停止')
    root = read_page_from_adb()
    if is_system_ai(root):
        raise RuntimeError('已确认系统AI会话：禁止发送新招呼')
    if chat_title(root) != username:
        raise RuntimeError('顶部聊天对象与 --username 不一致')
    if not any(rid(n) == 'tv_say_hello_button' for n in root.iter('node')):
        raise RuntimeError('没有发现新招呼页面标记，不在此程序中发送普通聊天消息')
    baseline = count_visible_reply(root, reply)
    node = one_node(root, 'et_sendmessage', enabled=True)
    x, y = center(node)
    if node.attrib.get('clickable') != 'true':
        raise RuntimeError('输入框不可点击')
    adb_action('shell','input','tap',x,y)
    time.sleep(.4)
    adb_action('shell','am','broadcast','-a','ADB_CLEAR_TEXT')
    time.sleep(.2)
    adb_action('shell','am','broadcast','-a','ADB_INPUT_TEXT','--es','msg',reply)
    time.sleep(.6)
    root = read_page_from_adb()
    if chat_title(root) != username:
        raise RuntimeError('输入后聊天对象变化，停止发送')
    if text(one_node(root, 'et_sendmessage')) != reply:
        raise RuntimeError('草稿输入验证未通过；保留当前页面，绝不点击发送')
    buttons = [n for n in root.iter('node') if rid(n) == 'btn_send'
               and text(n) == '发送' and n.attrib.get('enabled') == 'true'
               and n.attrib.get('clickable') == 'true']
    if len(buttons) != 1:
        raise RuntimeError(f'发送按钮数量={len(buttons)}，草稿保留，安全停止')
    sx, sy = center(buttons[0])
    payload = {'username':username,'reply':reply,'time':datetime.now().isoformat(),
               'status':'about_to_tap','baseline':baseline}
    pending.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    # 点击阶段发生异常时也不自动重试，避免重复发送。
    adb_action('shell','input','tap',sx,sy)
    payload['status'] = 'tap_sent_unverified'
    pending.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    time.sleep(1.3)
    verified = False
    for _ in range(3):
        root = read_page_from_adb()
        if chat_title(root) != username:
            raise RuntimeError('点击后页面已变化，发送结果未核实，保留 pending')
        if count_visible_reply(root, reply) > baseline:
            verified = True
            break
        time.sleep(.8)
    if not verified:
        raise RuntimeError('未观察到新增同文消息，保留 pending，禁止自动重发')
    sent.write_text(json.dumps(payload | {'status':'verified'},ensure_ascii=False,indent=2),encoding='utf-8')
    pending.unlink()
    print("🔒 运行状态已记录，个人信息已隐藏")
    print('✅ 单次发送结束，手机保持当前页面；未自动返回或处理下一个用户')


def main():
    p = argparse.ArgumentParser(description='SOUL V6 新招呼：默认只读，--send 时单次发送')
    p.add_argument('--xml', help='分析指定的本地 XML 文件；默认从当前手机读取')
    p.add_argument('--draft', action='store_true', help='生成一条 DeepSeek 草稿（消耗 Token，不发送）')
    p.add_argument('--send', action='store_true', help='向指定用户真实发送一次开场白')
    p.add_argument('--username', help='--send 必需：完整匹配顶部聊天昵称')
    args = p.parse_args()
    if args.send and (args.xml or args.draft or not args.username):
        p.error('--send 必须提供 --username，且不可与 --xml/--draft 混用')
    root = ET.parse(args.xml).getroot() if args.xml else read_page_from_adb()
    if args.send and chat_title(root) != args.username:
        raise RuntimeError('顶部昵称与指定用户名不匹配，停止')
    if any(rid(n) == 'rv_new_greeting_list' for n in root.iter('node')):
        if args.draft:
            raise SystemExit('请进入具体新招呼详情页再使用 --draft')
        rows = read_list(root)
        print(f'✅ 收到的新招呼列表：识别到 {len(rows)} 条可见记录')
        for i, row in enumerate(rows, 1):
            print(f"{i}. {row['name']} | {' / '.join(row['notes'][:3])}")
    else:
        detail = read_detail(root)
        print(f"✅ 当前聊天对象：{detail['name']}")
        print(f"✅ 招呼详情标记：{'存在' if detail['is_greeting'] else '未发现（可能为普通私聊）'}")
        print(f"签名：{detail['signature'] or '未发现'}")
        print(f"其他资料：{'；'.join(detail['traits']) or '未发现'}")
        if args.draft:
            print('🧠 DeepSeek 开场白草稿（未发送）：', draft_intro(detail))
        if args.send:
            if not detail['is_greeting']:
                raise RuntimeError('不是新招呼详情页，停止')
            reply = draft_intro(detail)
            print("🔒 运行状态已记录，个人信息已隐藏")
            do_one_send(args.username, reply)
    if not args.send:
        print('🔒 只读模式：未点击、未填写、未发送。')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, ET.ParseError, FileNotFoundError) as exc:
        raise SystemExit(f'❌ 安全停止：{exc}')
