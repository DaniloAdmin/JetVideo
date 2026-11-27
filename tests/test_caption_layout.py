import pytest

from caption_layout import caption_lines, keep_with_next, wrap_words
from subtitles import CaptionPainter, Cue, SubtitleStyle


def test_caption_lines_prefer_sentence_boundary_and_preserve_words():
    text = 'Он снова что-то сделал и опять завис. Петя открыл рюкзак, а он пуст.'
    assert caption_lines(text, 39) == 'Он снова что-то сделал и опять завис.\nПетя открыл рюкзак, а он пуст.'
    assert caption_lines(text, 30).split() == text.split()


@pytest.mark.parametrize('text, width, expected', [
    ('Петя открыл рюкзак, а он пуст.', 21, 'Петя открыл рюкзак,\nа он пуст.'),
    ('Петро відкрив рюкзак, а він порожній.', 22, 'Петро відкрив рюкзак,\nа він порожній.'),
    ('Перед нами була дорога до лісу.', 23, 'Перед нами була\nдорога до лісу.'),
])
def test_short_links_stay_with_the_next_word(text, width, expected):
    assert caption_lines(text, width) == expected


def test_two_balanced_rows_take_precedence_over_a_single_word_tail():
    rows = caption_lines('Они уже вернулись домой вчера вечером', 26).splitlines()
    assert len(rows) == 2
    assert all(len(row.split()) >= 2 for row in rows)


def test_explicit_line_breaks_keep_global_word_indices():
    text = '— Я повернувся.\n— Йдемо далі.'
    words, rows = wrap_words(text, 100, len)
    assert words == text.split()
    assert rows == [[0, 1, 2], [3, 4, 5]]


@pytest.mark.parametrize('style_width', [63, 72, 84])
def test_preview_and_export_layout_fit_font_measurements(style_width):
    cue = Cue(0, 2000, 'Петя открыл рюкзак, а он пуст.', (0, 300, 800, 1200, 1400, 1600))
    painter = CaptionPainter(SubtitleStyle(width=style_width, highlight=True), (1080, 1920))
    layout = painter.layout(cue)
    box, (words, rows, widths, _font, pad, *_metrics) = layout
    assert words == cue.text.split()
    assert [index for row in rows for index in row] == list(range(len(words)))
    assert len(rows) <= 2
    assert max(widths) + 2 * pad <= painter.limit
    assert not any(keep_with_next(words[row[-1]]) for row in rows[:-1])
    assert painter.paint(layout, 4).getbbox()
    assert box[2] <= 1080
