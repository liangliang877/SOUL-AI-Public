#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOUL V6.5.10 匹配聊天出错跳过并继续：匹配后直接使用「匹配下一个」按钮，批次完成后进入现有聊天机器人。
默认 --dry-run；--live 才会点击/发送。聊天界面已有内容只跳过问候，不覆盖。
无法确认页面、发送状态或回到入口时停止，不盲点。
"""
import argparse
import uuid
import subprocess
import sys
import time
from pathlib import Path
from soul_v7_system_ai import is_system_ai, log_skip
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import soul_v6_match_entry as entry
import soul_v6_match_success_send_fast as sender

CHAT = BASE / 'soul_v6_unified_integrated_v4_2_keep_going.py'
MESSAGE = '你说，咱俩匹配成功，是缘分还是系统偷偷安排的？😂'


def page():
    return ET.fromstring(entry.get_page())


def nodes(root, sid):
    return [n for n in root.iter('node') if n.get('resource-id','').endswith('/'+sid)]


def is_matching(root):
    return len(nodes(root,'matchingContainer')) == 1 and any(n.get('text','') == '灵魂匹配' for n in nodes(root,'titlebar_text_tv')) and not nodes(root,'et_sendmessage')


def is_chat(root):
    """匹配私聊：发送后顶部返回区会从 item_left_back 切换为匹配下一个专用控件。
    不再把「顶部控件变化」误判为离开匹配私聊。
    """
    if len(nodes(root,'et_sendmessage')) != 1:
        return False
    # 匹配度卡片仅存在于匹配私聊；顶部按钮可动态切换，不依赖昵称。
    match_features=('llyt_match_degree','iv_rematch_back','tv_rematch_text',
                    'planet_rematching_container','item_left_back')
    return any(nodes(root,feature) for feature in match_features) or any(
        n.get('text','').strip() == '匹配下一个' for n in root.iter('node'))


def chat_name(root):
    # 只使用真正可核验的昵称。昵称尚未渲染时允许跳过问候，
    # 而不是把整批匹配当成错误；绝不凭猜测给用户发送消息。
    if not is_chat(root):
        return None
    try:
        value=sender.title(root)
        return None if sender.transient_title(value) else value
    except RuntimeError:
        return None


def stable_match_chat(timeout=7):
    """对匹配结果页重取 UI，容忍动画和短暂的控件缺失。"""
    end=time.monotonic()+timeout
    last=None
    while time.monotonic()<end:
        last=page()
        if is_popup(last):
            raise RuntimeError('出现同城奇遇弹窗，保留现场')
        if is_chat(last):
            # 匹配私聊结构已确认即可继续；不等待会被状态提示覆盖的昵称。
            return last
        elif not is_matching(last):
            # 其他页面状态交给调用方安全判断。
            pass
        time.sleep(0.6)
    return last


def safe_rematch(root, timeout=8):
    """发送后等待顶部「匹配下一个」完整渲染；仅返回经验证的按钮。"""
    end=time.monotonic()+timeout
    last_error=''
    while time.monotonic()<end:
        if is_popup(root):
            raise RuntimeError('检测到弹窗，不点击匹配下一个')
        if len(nodes(root,'et_sendmessage')) == 1:
            try:
                return rematch_button(root)
            except RuntimeError as exc:
                last_error=str(exc)
        else:
            last_error='尚未确认匹配私聊输入框'
        time.sleep(0.6)
        root=page()
    raise RuntimeError(f'等待匹配下一个按钮超时：{last_error}；保留现场，避免误点击')


def is_main(root):
    return (len(nodes(root,'main_tab_msg_text')) == 1 and
            nodes(root,'main_tab_msg_text')[0].get('text','').strip() == '聊天' and
            not nodes(root,'et_sendmessage'))


def is_popup(root):
    return bool(nodes(root,'tv_love_recall') and nodes(root,'tv_love_chat'))


def return_from_chat_fast():
    """不核对发送后的昵称：Back一次，若只是收起键盘则再Back一次。"""
    entry.adb('shell','input','keyevent','4')
    time.sleep(0.5)
    root=page()
    if is_chat(root):
        # 第一回退只关闭输入法，再退一次离开聊天。
        entry.adb('shell','input','keyevent','4')
        time.sleep(0.5)
        root=page()
    return root


def return_to_home_from_match(root):
    # 匹配等待页顶部返回控件可能是图形按钮。不能认出就不操作，避免付费按钮。
    if not is_matching(root):
        return root
    buttons=[n for n in nodes(root,'iv_back')+nodes(root,'item_left_back')
             if n.get('clickable')=='true' and n.get('enabled')=='true']
    if len(buttons)!=1:
        raise RuntimeError('返回后仍在匹配等待页，尚未确认该页安全返回控件；停止，不点击任何购买入口')
    entry.adb('shell','input','tap',*entry.center(buttons[0].get('bounds')))
    time.sleep(1)
    return page()


def wait_result(timeout):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        root=page()
        if is_popup(root):
            raise RuntimeError('匹配期间遇到同城奇遇弹窗；保留现场交由主聊天程序处理')
        if is_chat(root):
            return root
        if not is_matching(root):
            raise RuntimeError('匹配期间出现未知页面，不执行点击')
        time.sleep(1)
    raise RuntimeError('匹配等待超时；保留页面供人工查看')


def enter_main(root):
    if is_popup(root):
        raise RuntimeError('发现同城奇遇弹窗；保留现场，不覆盖弹窗')
    if is_chat(root):
        raise RuntimeError('仍在私聊，拒绝自动切页面')
    if not is_main(root):
        raise RuntimeError('未确认底部聊天页签，拒绝盲目跳转')
    parent={child:p for p in root.iter() for child in p}
    tab=nodes(root,'main_tab_msg_text')[0]
    current=tab
    while current is not None and current.get('clickable')!='true':
        current=parent.get(current)
    if current is None:
        # SOUL 当前版本底部「聊天」文字本身及其父节点可能都不标记 clickable。
        # 已经确认主页面存在唯一聊天页签时，点击其可见中心即可。
        current=tab
        print('ℹ️ 聊天页签未标记 clickable，改用已识别的文字区域点击',flush=True)
    entry.adb('shell','input','tap',*entry.center(current.get('bounds')))
    time.sleep(1)
    root=page()
    if not nodes(root,'conversation_list') or nodes(root,'et_sendmessage'):
        raise RuntimeError('未确认已返回主消息列表')
    print('✅ 匹配阶段结束，已进入主消息列表',flush=True)


def rematch_button(root):
    """优先唯一「匹配下一个」文字/资源 ID，必要时定位其父容器。

    发送后顶部区域会替换原昵称控件，不能再要求 item_left_back 存在。
    只在可确认的匹配私聊中点击顶部区域；不使用跨设备固定坐标。
    """
    if not is_chat(root):
        raise RuntimeError('当前未确认匹配私聊，不能点击匹配下一个')
    items=[n for n in nodes(root,'tv_rematch_text') if n.get('enabled')=='true'
           and n.get('text','').strip() == '匹配下一个']
    if not items:
        items=[n for n in root.iter('node')
               if n.get('text','').strip() == '匹配下一个'
               and n.get('enabled')=='true']
    if len(items)!=1:
        raise RuntimeError(f'未唯一确认「匹配下一个」文字（{len(items)}个）')
    parent={child:p for p in root.iter() for child in p}
    target=items[0]
    current=target
    for _ in range(4):
        if current.get('clickable')=='true' and current.get('enabled')=='true':
            target=current
            break
        current=parent.get(current)
        if current is None:
            break
    # 点击真实可见文本或其已验证父控件中心；不是任意硬编码坐标。
    entry.center(target.get('bounds'))
    return target

def go_back_from_match_chat():
    """结束批次：用当前匹配聊天页的专用返回键，绝不碰付款按钮。"""
    root = page()
    if len(nodes(root, 'et_sendmessage')) != 1:
        raise RuntimeError('批次结束时不是匹配聊天页，无法安全返回')
    buttons = [n for n in nodes(root, 'iv_rematch_back')
               if n.get('clickable') == 'true' and n.get('enabled') == 'true']
    if len(buttons) != 1:
        raise RuntimeError('找不到唯一的匹配聊天返回按钮，保留现场')
    entry.adb('shell', 'input', 'tap', *entry.center(buttons[0].get('bounds')))
    time.sleep(0.7)
    return page()


def soul_entry(root):
    """只认可真实「灵魂匹配」卡片，绝不误进语音/萌面匹配。"""
    try:
        return entry.inspect(ET.tostring(root, encoding='unicode'))
    except (entry.MatchError, ValueError, ET.ParseError):
        return None


def recover_to_soul_home(max_back=4):
    """缺少「匹配下一个」时，通过 Android 返回回到有唯一灵魂匹配卡片的首页。

    不依赖「匹配下一个」出现，也不执行屏幕固定坐标点击。
    如同城奇遇弹窗、无法识别界面，保留现场；禁止无目的多次返回。
    """
    for attempt in range(max_back + 1):
        root=page()
        if is_popup(root):
            raise RuntimeError('恢复时出现同城奇遇弹窗；不能穿透弹窗乱点，保留现场')
        found=soul_entry(root)
        if found is not None:
            print(f'✅ 已返回SOUL首页，灵魂匹配剩余 {found[0]} 次',flush=True)
            return root
        if attempt == max_back:
            break
        if not (is_chat(root) or is_matching(root)):
            # 输入法弹出可能造成聊天页控件短暂不可见，先等待一次渲染。
            time.sleep(0.8)
            root=page()
            if soul_entry(root) is not None:
                continue
            if not (is_chat(root) or is_matching(root)):
                raise RuntimeError('当前页面不是已验证的私聊或灵魂匹配等待页，停止恢复以免退出APP')
        print(f'↩️ 未显示「匹配下一个」，从已识别页面返回（{attempt+1}/{max_back}）',flush=True)
        entry.adb('shell','input','keyevent','4')
        time.sleep(0.9)
    raise RuntimeError('已尝试返回，但仍未看到唯一的灵魂匹配入口；保留现场')


def select_next_match(root, number, planned):
    """有明确按钮则点击；没有则返回首页，重新点击唯一灵魂匹配卡片。"""
    if is_popup(root):
        raise RuntimeError('匹配前发现弹窗；保留现场')
    if is_chat(root):
        # 【V7.5 核心修复】发送后顶部更换控件，仍属于匹配私聊。
        # 给顶部按钮充分渲染时间，优先点击而不是错误地直接寻找首页入口。
        print('🔎 当前仍在匹配私聊；等待顶部「匹配下一个」',flush=True)
        try:
            button=safe_rematch(root,timeout=8)
        except RuntimeError:
            print('ℹ️ 当前私聊没有可确认的「匹配下一个」；改为返回灵魂匹配首页',flush=True)
            root=recover_to_soul_home()
        else:
            print(f'\n匹配 {number}/{planned}：点击「匹配下一个」',flush=True)
            entry.adb('shell','input','tap',*entry.center(button.get('bounds')))
            time.sleep(1.0)
            return page()
    if is_matching(root):
        root=recover_to_soul_home()
    found=soul_entry(root)
    if found is None:
        raise RuntimeError('未找到已验证的灵魂匹配入口；拒绝点击其他匹配方式')
    remaining,xy,_=found
    if remaining<=0:
        print('⚠️ 灵魂匹配今日次数耗尽，结束匹配批次',flush=True)
        return None
    print(f'\n匹配 {number}/{planned}：从SOUL首页重新进入灵魂匹配（剩余 {remaining} 次）',flush=True)
    entry.adb('shell','input','tap',*xy)
    time.sleep(1.2)
    root=page()
    if not (is_matching(root) or is_chat(root)):
        for _ in range(5):
            time.sleep(0.6)
            root=page()
            if is_matching(root) or is_chat(root):break
        else:
            raise RuntimeError('点击已验证的灵魂匹配入口后，页面不是匹配等待或私聊；停止以免误操作')
    return root


def run(args):
    for file in (BASE/'soul_v6_match_entry.py',BASE/'soul_v6_match_success_send_fast.py',CHAT):
        if not file.is_file():
            raise RuntimeError('缺少程序文件：'+str(file))
        compile(file.read_text(encoding='utf-8-sig'),str(file),'exec')
    print('SOUL V7.5：发送后优先识别顶部「匹配下一个」；失败才返回匹配首页',flush=True)
    if not args.live:
        print('✅ 文件语法检查完成；未操作手机')
        return 0
    devs=[x for x in entry.adb('devices').splitlines()[1:] if x.endswith('\tdevice')]
    if len(devs)!=1:
        raise RuntimeError(f'必须只连接一台 ADB 手机，当前{len(devs)}台')
    root=page()
    if is_popup(root):
        raise RuntimeError('检测到同城奇遇弹窗，保留现场')
    if args.chat_only:
        if not is_main(root):
            raise RuntimeError('--chat-only 必须在底部有「聊天」的SOUL主页运行')
        enter_main(root)
        return subprocess.call([sys.executable,'-u',str(CHAT),'--live', *(['--special-live','--special-max','5'] if args.special_live else [])],cwd=BASE)
    initial=soul_entry(root)
    if initial:
        planned=min(args.max,initial[0])
    elif is_chat(root) or is_matching(root):
        planned=args.max
    else:
        raise RuntimeError('启动时需在SOUL首页、匹配等待页或匹配私聊页')
    print(f'本批最多 {planned} 次；失败不重发同一人，优先返回匹配入口',flush=True)
    skipped=0
    completed=0
    session_uuid=uuid.uuid4().hex
    # 每轮只尝试一个匹配对象，失败仍算本批一次，避免重复进入无限循环。
    for i in range(planned):
        root=page()
        # 启动时若已经在匹配私聊页，第一轮处理该私聊，不重复开启匹配。
        if i==0 and is_chat(root):
            print('↪ 当前已处于匹配私聊，本轮只处理当前对象',flush=True)
        elif i==0 and is_matching(root):
            print('↪ 当前已处于匹配等待页，等待匹配成功',flush=True)
        else:
            root=select_next_match(root,i+1,planned)
            if root is None:break
        if is_matching(root):
            root=wait_result(args.timeout)
        elif not is_chat(root):
            # UI切换动画短暂过渡，只等待有界时间。
            for _ in range(12):
                time.sleep(0.5)
                root=page()
                if is_matching(root) or is_chat(root):break
            if is_matching(root):root=wait_result(args.timeout)
            elif not is_chat(root):
                raise RuntimeError('进入匹配后未确认等待页或私聊页，不执行其他点击')
        root=stable_match_chat()
        try:
            name=chat_name(root)
            if not is_chat(root):
                raise RuntimeError('匹配私聊结构尚未确认，跳过本轮发送')
            print(f'✅ 匹配成功，已确认私聊页面；顶部昵称：{name or "动态文案/暂未显示"}',flush=True)
            if is_system_ai(root):
                log_skip('match', name or '未知昵称', root, BASE)
                print('↪ 系统AI匹配：本轮不发送；继续下一轮',flush=True)
            elif nodes(root,'content_text') or nodes(root,'voice_bubble'):
                print('↪ 已有可见消息，不再重复发送开场白',flush=True)
            else:
                result=subprocess.run([sys.executable,'-u',str(BASE/'soul_v6_match_success_send_fast.py'),
                    '--name',name or '', '--session-key',f'{session_uuid}:{i+1}', '--message',MESSAGE,'--live','--fast-after-click'],cwd=BASE)
                if result.returncode:
                    raise RuntimeError(f'本轮问候失败/未确认（退出码 {result.returncode}）')
            print('✅ 本轮问候流程结束',flush=True)
        except Exception as exc:
            skipped+=1
            print(f'⚠️ 本轮无法发送，放弃本轮、不重复发送：{exc}',flush=True)
            print('↩️ 下轮优先寻找「匹配下一个」；若不存在则返回SOUL首页重新匹配',flush=True)
        completed+=1
    print(f'📌 本批处理 {completed} 位，跳过 {skipped} 位；不自动补发失败问候',flush=True)
    # 无论最后一位是否成功发送，都通过已识别页面返回首页。
    root=recover_to_soul_home()
    if args.chat:
        if not is_main(root):
            raise RuntimeError('已返回匹配入口，但主页聊天标签未显示；保留现场')
        enter_main(root)
        print('💬 已进入主聊天列表，启动主动聊天、双屏、新招呼和奇遇铃处理',flush=True)
        return subprocess.call([sys.executable,'-u',str(CHAT),'--live', *(['--special-live','--special-max','5'] if args.special_live else [])],cwd=BASE)
    print('✅ 匹配批次结束')
    return 0

if __name__=='__main__':
    p=argparse.ArgumentParser(description='SOUL V6.5 连续灵魂匹配＋问候＋聊天交接；默认只读')
    p.add_argument('--live',action='store_true')
    p.add_argument('--max',type=int,default=10)
    p.add_argument('--timeout',type=int,default=90)
    p.add_argument('--chat',action='store_true',help='批次结束启动主聊天顶部待机')
    p.add_argument('--special-live',action='store_true',help='匹配后启用新招呼/奇遇铃批处理')
    p.add_argument('--chat-only',action='store_true',help='不匹配，直接点击主页底部聊天并启动现有机器人')
    a=p.parse_args()
    if not 1<=a.max<=10:p.error('--max 要在1-10之间')
    if not 15<=a.timeout<=300:p.error('--timeout 要在15-300之间')
    try:sys.exit(run(a))
    except KeyboardInterrupt:print('\n用户已停止');sys.exit(130)
    except Exception as e:print('❌ 安全停止：'+str(e),file=sys.stderr);sys.exit(40)
