"""`yamluna.error`: the exception hierarchy, char-offset marks, and snippets.

The mark tests are written against ruamel's own `StringMark` wherever the rendering is
meant to match, so a drift in the snippet or in `str(error)` shows up here rather than in
a user's traceback.

Run:

```bash
PYTHONPATH=python .venv/bin/pytest tests/test_errors.py
```
"""

import warnings

import pytest
from ruamel.yaml.error import MarkedYAMLError as RuamelMarkedYAMLError
from ruamel.yaml.error import StringMark as RuamelStringMark
from yamluna.error import (
    ComposerError,
    ConstructorError,
    DuplicateKeyError,
    DuplicateKeyFutureWarning,
    EmitterError,
    Mark,
    MarkedYAMLError,
    ParserError,
    RepresenterError,
    ScannerError,
    StringMark,
    YAMLError,
    YAMLStreamError,
    YAMLWarning,
    make_error,
)

ASCII = 'first: 1\nsecond: two\nthird: 3\n'
# 'é' is 2 bytes, '☕' 3 and '😀' 4, so byte-offset arithmetic goes wrong from line 0 on.
UNICODE = 'café: ☕\nemoji: 😀 tail\nlast: ok\n'


def test_hierarchy():
    """Every error class sits where ruamel puts its namesake."""
    for cls in (
        ScannerError,
        ParserError,
        ComposerError,
        ConstructorError,
        RepresenterError,
        EmitterError,
        DuplicateKeyError,
    ):
        assert issubclass(cls, MarkedYAMLError)
        assert issubclass(cls, YAMLError)
    # ruamel puts YAMLStreamError beside YAMLError rather than under it.
    assert not issubclass(YAMLStreamError, YAMLError)
    assert issubclass(DuplicateKeyFutureWarning, Warning)
    assert issubclass(YAMLWarning, Warning)
    assert StringMark is Mark


def test_snippet_ascii():
    """The caret lands under the character the line and column name."""
    # Line 1, column 8 is the `w` of "two".
    m = Mark('doc.yaml', None, 1, 8, ASCII)
    assert m.index == ASCII.index('two')
    assert m.get_snippet() == '    second: two\n            ^ (line: 2)'


def test_str_reports_1_based_line_and_column():
    m = Mark('doc.yaml', None, 1, 8, ASCII)
    assert str(m).startswith('  in "doc.yaml", line 2, column 9:\n')
    assert str(m).endswith(m.get_snippet())


@pytest.mark.parametrize('text', [ASCII, UNICODE])
@pytest.mark.parametrize('line,column', [(0, 0), (0, 3), (1, 0), (1, 6), (2, 4)])
def test_matches_ruamel_mark(text, line, column):
    ours = Mark('n', None, line, column, text)
    theirs = RuamelStringMark('n', ours.index, line, column, text, ours.index)
    assert ours.get_snippet() == theirs.get_snippet()
    assert str(ours) == str(theirs)


def test_snippet_multibyte_is_char_indexed():
    """A line holding multi-byte characters still points at the character asked for."""
    # Column 7 on line 1 is the emoji itself.
    m = Mark('u.yaml', None, 1, 7, UNICODE)
    assert UNICODE[m.pointer] == '😀'
    first, caret = m.get_snippet().split('\n')
    assert first == '    emoji: 😀 tail'
    assert caret == ' ' * 11 + '^ (line: 2)'
    # The same position expressed in bytes points somewhere else entirely.
    assert len(UNICODE[: m.index].encode()) != m.index


def test_explicit_index_must_be_char_offset():
    """An index handed in has to be a char offset, which is what the Rust side sends."""
    # A byte offset slices mid-character, so the snippet and the caret both go wrong.
    char_index = UNICODE.index('😀')
    byte_index = len(UNICODE[:char_index].encode())
    assert byte_index > char_index
    good = Mark('u.yaml', char_index, 1, 7, UNICODE)
    assert good.get_snippet().splitlines()[0] == '    emoji: 😀 tail'
    # A byte offset points at a different character, and the caret lands off target.
    bad = Mark('u.yaml', byte_index, 1, 7, UNICODE)
    assert UNICODE[bad.pointer] != '😀'
    assert bad.get_snippet() != good.get_snippet()


def test_first_line():
    """Line 0, column 0 is index 0, and the snippet is the first line."""
    m = Mark('u.yaml', None, 0, 0, UNICODE)
    assert m.index == 0
    assert m.get_snippet() == '    café: ☕\n    ^ (line: 1)'


def test_last_line():
    """A mark on the last line works, and so does one character past the last."""
    text = 'a: 1\nb: 2'  # no trailing newline
    m = Mark('u.yaml', None, 1, 3, text)
    assert m.index == len(text) - 1
    assert m.get_snippet() == '    b: 2\n       ^ (line: 2)'
    eof = Mark('u.yaml', None, 1, 4, text)  # one past the last character
    assert eof.index == len(text)
    assert eof.get_snippet() == '    b: 2\n        ^ (line: 2)'


def test_position_past_end_of_line_is_clamped():
    m = Mark('u.yaml', None, 0, 99, ASCII)
    assert m.index == ASCII.index('\n')
    assert m.get_snippet() == '    first: 1\n            ^ (line: 1)'


def test_position_past_end_of_buffer_is_clamped():
    m = Mark('u.yaml', 10_000, 400, 0, ASCII)
    assert m.pointer == len(ASCII)
    assert m.get_snippet() is not None


def test_empty_document():
    m = Mark('empty.yaml', None, 0, 0, '')
    assert (m.index, m.pointer) == (0, 0)
    assert m.get_snippet() == '    \n    ^ (line: 1)'
    assert str(m) == '  in "empty.yaml", line 1, column 1:\n    \n    ^ (line: 1)'


def test_no_buffer_has_no_snippet():
    m = Mark('f.yaml', 12, 3, 4)
    assert m.get_snippet() is None
    assert str(m) == '  in "f.yaml", line 4, column 5'


def test_crlf_line_offsets():
    text = 'a: 1\r\nb: 2\r\nc: 3\r\n'
    m = Mark('crlf', None, 1, 3, text)
    assert text[m.index] == '2'


def test_long_line_is_elided_like_ruamel():
    text = 'key: ' + 'x' * 200
    m = Mark('long', None, 0, 120, text)
    ours = m.get_snippet()
    assert ours.startswith('     ... ')
    assert ours.splitlines()[0].endswith(' ... ')
    theirs = RuamelStringMark('long', m.index, 0, 120, text, m.index)
    assert ours == theirs.get_snippet()


@pytest.mark.parametrize(
    'kind,cls',
    [
        ('scanner', ScannerError),
        ('parser', ParserError),
        ('composer', ComposerError),
        ('constructor', ConstructorError),
        ('representer', RepresenterError),
        ('emitter', EmitterError),
        ('duplicate_key', DuplicateKeyError),
        ('DuplicateKeyError', DuplicateKeyError),
        ('ScannerError', ScannerError),
        ('something-new', MarkedYAMLError),
    ],
)
def test_make_error_classifies_by_kind(kind, cls):
    err = make_error(kind, 'boom', 1, 8, ASCII.index('two'), ASCII, 'doc.yaml')
    assert type(err) is cls
    assert isinstance(err, YAMLError)


def test_make_error_message():
    err = make_error(
        'scanner', 'found unexpected end of stream', 1, 7, None, UNICODE, 'u.yaml'
    )
    assert str(err) == (
        'found unexpected end of stream\n'
        '  in "u.yaml", line 2, column 8:\n'
        '    emoji: 😀 tail\n'
        '           ^ (line: 2)'
    )
    assert err.problem == 'found unexpected end of stream'
    assert err.problem_mark.index == UNICODE.index('😀')
    assert err.context is None and err.context_mark is None


def test_make_error_without_source():
    err = make_error('parser', 'nope', 0, 0)
    assert str(err) == 'nope\n  in "<unicode string>", line 1, column 1'


def test_make_error_note():
    err = make_error('duplicate_key', 'duplicate key "a"', 2, 0, None, ASCII, 'd.yaml',
                     '\n        see the docs\n        ')
    assert str(err).endswith('\nsee the docs\n')


def test_marked_error_str_matches_ruamel():
    ctx = Mark('d.yaml', None, 0, 0, ASCII)
    prb = Mark('d.yaml', None, 2, 5, ASCII)
    kwargs = dict(
        context='while parsing a block mapping',
        context_mark=ctx,
        problem='expected <block end>',
        problem_mark=prb,
        note='  a note\n',
    )
    ours = MarkedYAMLError(**kwargs)
    theirs = RuamelMarkedYAMLError(
        kwargs['context'],
        RuamelStringMark('d.yaml', ctx.index, 0, 0, ASCII, ctx.index),
        kwargs['problem'],
        RuamelStringMark('d.yaml', prb.index, 2, 5, ASCII, prb.index),
        kwargs['note'],
    )
    assert str(ours) == str(theirs)


def test_context_mark_suppressed_when_same_position():
    m = Mark('d.yaml', None, 0, 0, ASCII)
    err = MarkedYAMLError('ctx', m, 'problem', Mark('d.yaml', None, 0, 0, ASCII))
    assert str(err).count('in "d.yaml"') == 1


def test_marked_error_ignores_warn():
    err = MarkedYAMLError(None, None, 'p', None, None, 'this warn text is dropped')
    assert err.warn is None
    assert str(err) == 'p'


def test_duplicate_key_future_warning_is_warnable():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        warnings.warn(DuplicateKeyFutureWarning(None, None, 'dup key "a"', None), stacklevel=2)
    assert len(caught) == 1
    assert str(caught[0].message) == 'dup key "a"'


def test_errors_pickle_round_trip():
    import pickle

    err = make_error('scanner', 'boom', 1, 0, None, ASCII, 'd.yaml')
    back = pickle.loads(pickle.dumps(err))
    assert type(back) is ScannerError
    assert str(back) == str(err)
