from subtitle_spelling import find_issues, suggestions


def test_russian_spelling_leaves_names_abbreviations_numbers_and_yo_alone():
    text = 'Петя, пажалуста, уточни у Лены. Ещё всё верно: ёлка, 2026, ВГИК.'
    issue, = find_issues(text, 'ru')
    assert text[issue.start:issue.end] == issue.word == 'пажалуста'
    assert 'пожалуйста' in suggestions(issue, 'ru')


def test_proposes_known_lowercase_form_for_accidental_internal_capitals():
    issue, = find_issues('она оказалАсь дома', 'ru')
    assert issue.word == 'оказалАсь'
    assert issue.correction == 'оказалась'
    assert suggestions(issue, 'ru') == ('оказалась',)


def test_ukrainian_spelling_preserves_valid_apostrophes_and_letters():
    text = "Петро скоєв помилку. П'ять, п’ять, пʼять. Ґанок, їжа, її."
    issue, = find_issues(text, 'uk')
    assert text[issue.start:issue.end] == issue.word == 'скоєв'
    assert 'скоїв' in suggestions(issue, 'uk')
