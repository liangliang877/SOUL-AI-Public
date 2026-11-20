# -*- coding: utf-8 -*-
"""只识别有明确 AI 免责声明的 SOUL 系统 AI 会话，不按昵称猜测。"""
import json
from datetime import datetime
from pathlib import Path

AI_NOTICE_MARKERS = (
    '对话由AI生成，非Soul立场',
    '对话由 AI 生成，非 Soul 立场',
    '对话由AI生成，非 SOUL 立场',
)

def is_system_ai(root):
    texts = [n.attrib.get('text','') for n in root.iter('node')]
    texts += [n.attrib.get('content-desc','') for n in root.iter('node')]
    # 消除空格、全半角标点变体；避免直接把“智能甄选”当系统 AI 的唯一依据。
    compact = ''.join(texts).replace(' ', '').replace('\u3000', '').lower()
    return '对话由ai生成' in compact and ('非soul立场' in compact or '非soul' in compact)

def log_skip(stage, name, root, folder):
    path=Path(folder)/'soul_v7_ai_skip_log.jsonl'
    data={'time':datetime.now().isoformat(timespec='seconds'),'stage':stage,'name':name,
          'reason':'页面有明确AI生成免责声明','session_only':True}
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(data,ensure_ascii=False)+'\n')
    # 不以昵称做永久黑名单，避免新账号、同名真实用户被误跳过。
    print(f'🤖 系统AI用户：{name}；跳过发送，返回列表',flush=True)
