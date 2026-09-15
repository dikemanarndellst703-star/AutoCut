"""Timeline invariants, transparent classification rules and subtitle mapping."""
import math
import re

CATEGORIES = {'know', 'do', 'other', 'unclassified'}
DEFAULT_RULES = {
    'know': ['因为', '为什么', '原理', '作用', '意味着', '帮助我们', '有助于', '神经', '横膈膜', '二氧化碳', '氧气', '也就是说', '所以', '我们需要', '注意事项'],
    'do': ['吸气', '呼气', '屏息', '憋气', '跟着我', '跟我', '一起做', '开始练习', '保持', '放松肩膀', '闭上眼睛', '坐直', '慢慢', '再来一次', '倒数', '数到', '准备好', '接下来做', '感受'],
    'other': ['欢迎来到', '欢迎收看', '下期再见', '下节课再见', '点赞', '关注我们', '关注我的', '订阅', '扫码', '二维码', '购买课程', '感谢观看', '谢谢观看', '今天就到这里'],
}


def classify(text, start, duration, rules=None):
    rules = rules or DEFAULT_RULES
    scores = {key: sum(word in text for word in words) for key, words in rules.items()}
    counting = bool(re.search(r'(?:[一二三四五六七八九十]|\d)[，、,\s]+(?:[一二三四五六七八九十]|\d)', text))
    if counting:
        scores['do'] += 2
    if scores['other'] and (start < min(120, duration * .15) or start > duration * .8 or any(w in text for w in ['扫码', '点赞', '订阅', '购买课程'])):
        return 'other', 'medium', '命中片头、片尾或推广用语；请复核'
    # Explanations about an action are knowledge, not automatically practice.
    if any(w in text for w in ['因为', '为什么', '原理', '有助于', '也就是说']):
        return 'know', 'medium', '包含原因或原理解释'
    if scores['do'] > scores['know']:
        return 'do', 'medium', '包含行动指令或读秒用语'
    if scores['know']:
        return 'know', 'medium', '包含原理、作用或注意事项用语'
    return 'know', 'low', '语义线索不足，暂归为知，需人工确认'


def piece(start, end, text='', category='unclassified', reason='尚未分析', cues=None, confidence='low'):
    return {'start': round(start, 4), 'end': round(end, 4), 'text': text,
            'category': category, 'keep': True, 'reviewed': False,
            'reason': reason, 'confidence': confidence, 'cues': cues or []}


def blank_timeline(duration, step=30):
    return [piece(start, min(start + step, duration)) for start in range(0, math.ceil(duration), step) if start < duration]


def build_timeline(raw, duration, rules=None):
    result, cursor = [], 0.0

    def gap(end):
        nonlocal cursor
        category = 'do' if result and result[-1]['category'] == 'do' else 'unclassified'
        while end - cursor > .00005:
            stop = min(cursor + 30, end)
            result.append(piece(cursor, stop, category=category, reason='未识别到文字，可能包含跟练、音乐或未识别语音；请查看画面'))
            cursor = stop

    for item in sorted(raw, key=lambda s: s['start']):
        start = min(duration, max(cursor, float(item['start'])))
        end = min(duration, float(item['end']))
        text = str(item.get('text', '')).strip()
        if not text or end <= start:
            continue
        if start > cursor:
            gap(start)
        category, confidence, reason = classify(text, start, duration, rules)
        cues = item.get('words') or [{'start': start, 'end': end, 'text': text}]
        cues = [{'start': max(start, float(c['start'])), 'end': min(end, float(c['end'])), 'text': str(c.get('text', c.get('word', '')))} for c in cues if float(c['end']) > start and float(c['start']) < end]
        result.append(piece(start, end, text, category, reason, cues, confidence))
        cursor = end
    gap(duration)
    for segment in result:
        segment['keep'] = segment['category'] != 'other'
    return validate_segments(result, duration)


def validate_segments(segments, duration):
    if not isinstance(segments, list) or not segments or len(segments) > 50000:
        raise ValueError('分段列表为空或过长')
    clean, cursor = [], 0.0
    for segment in segments:
        start, end = float(segment['start']), float(segment['end'])
        if not (math.isfinite(start) and math.isfinite(end)) or abs(start - cursor) > .002 or end <= start or end > duration + .002:
            raise ValueError('时间分段必须从 0 开始连续覆盖原片，不得重叠或越界')
        category = segment.get('category', 'unclassified')
        if category not in CATEGORIES:
            raise ValueError('内容分类无效')
        text = str(segment.get('text', ''))[:20000]
        entry = piece(cursor, end, text, category, str(segment.get('reason', '人工编辑'))[:500], confidence=segment.get('confidence', 'low'))
        entry['keep'] = bool(segment.get('keep', True))
        entry['reviewed'] = bool(segment.get('reviewed', False))
        cues = segment.get('cues', [])
        if not isinstance(cues, list) or len(cues) > 10000:
            raise ValueError('字幕时间戳格式无效')
        for cue in cues:
            a, b = float(cue['start']), float(cue['end'])
            if not math.isfinite(a) or not math.isfinite(b):
                raise ValueError('字幕时间戳无效')
            a, b = max(start, a), min(end, b)
            if b > a:
                entry['cues'].append({'start': a, 'end': b, 'text': str(cue.get('text', ''))[:20000]})
        clean.append(entry)
        cursor = end
    if abs(cursor - duration) > .002:
        raise ValueError('时间分段未覆盖完整视频')
    clean[-1]['end'] = duration
    return clean


def export_ranges(segments, mode='kept'):
    selected = [s for s in segments if s['keep'] and (mode == 'kept' or s['category'] == mode)]
    ranges = []
    for s in selected:
        if ranges and abs(ranges[-1][1] - s['start']) < .002:
            ranges[-1][1] = s['end']
        else:
            ranges.append([s['start'], s['end']])
    return selected, ranges


def timestamp(seconds):
    total = max(0, round(seconds * 1000))
    return f'{total // 3600000:02}:{total // 60000 % 60:02}:{total // 1000 % 60:02},{total % 1000:03}'


def subtitles(segments, remap=False):
    rows, offset = [], 0.0
    for s in segments:
        if s['text']:
            start = offset if remap else s['start']
            end = start + s['end'] - s['start']
            rows.append(f"{len(rows)+1}\n{timestamp(start)} --> {timestamp(end)}\n{s['text']}\n")
        offset += s['end'] - s['start']
    return '\n'.join(rows)
