_OPENERS = '«‹“„([{'
_CLOSERS = '»›”’")]}'
_LINK_WORDS = frozenset((
    'а', 'и', 'но', 'или', 'либо', 'в', 'во', 'на', 'к', 'ко', 'у', 'с', 'со',
    'из', 'до', 'за', 'от', 'по', 'под', 'над', 'для', 'без', 'при', 'о', 'об',
    'обо', 'про', 'не', 'ни', 'что', 'как', 'чтобы',
    'і', 'й', 'та', 'але', 'чи', 'або', 'з', 'зі', 'із', 'від', 'під',
    'перед', 'між', 'що', 'як', 'щоб', 'ні',
))


def keep_with_next(word):
    return word.lstrip(_OPENERS + '"').casefold() in _LINK_WORDS


def _break_cost(words, end):
    if end == len(words):
        return 0
    previous, following = words[end - 1], words[end]
    if (keep_with_next(previous) or previous in ('-', '–', '—') or previous[-1] in _OPENERS
            or following[0] in _CLOSERS + ',.;:!?%…'):
        return 2
    if previous.rstrip(_CLOSERS).endswith(('.', '!', '?', '…')):
        return -.32
    if previous.rstrip(_CLOSERS).endswith((',', ';', ':')):
        return -.12
    return 0


def _wrap_line(words, limit, measure):
    count = len(words)
    if measure(' '.join(words)) <= limit:
        return [(0, count)]
    scores = [(count + 1, float('inf'))] * count + [(0, 0)]
    next_rows = [0] * count
    for start in range(count - 1, -1, -1):
        for end in range(start + 1, count + 1):
            width = measure(' '.join(words[start:end]))
            if width > limit and end > start + 1:
                break
            remaining = max(0, limit - width) / limit
            orphan = .35 if end == start + 1 and count > 1 else 0
            lines, cost = scores[end]
            candidate = (lines + 1, cost + remaining ** 2 + orphan + _break_cost(words, end))
            if candidate < scores[start]:
                scores[start] = candidate
                next_rows[start] = end
    rows = []
    start = 0
    while start < count:
        end = next_rows[start]
        rows.append((start, end))
        start = end
    return rows


def wrap_words(text, max_width, measure):
    words, rows = [], []
    for line in text.splitlines():
        group = line.split()
        if not group:
            continue
        offset = len(words)
        words.extend(group)
        rows.extend(list(range(offset + start, offset + end))
                    for start, end in _wrap_line(group, max(1, max_width), measure))
    return words, rows


def caption_lines(text, max_width=42):
    words, rows = wrap_words(text, max_width, len)
    return '\n'.join(' '.join(words[index] for index in row) for row in rows)
