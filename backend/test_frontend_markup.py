"""Static guards for browser parser repairs that can break all UI handlers."""
from html.parser import HTMLParser
from pathlib import Path


def test_pages_have_no_nested_forms_or_duplicate_ids():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.form_open = False
            self.ids = set()

        def handle_starttag(self, tag, attrs):
            if tag == 'form':
                assert not self.form_open, 'Nested form gets removed by the browser'
                self.form_open = True
            identifier = dict(attrs).get('id')
            if identifier:
                assert identifier not in self.ids, f'Duplicate id: {identifier}'
                self.ids.add(identifier)

        def handle_endtag(self, tag):
            if tag == 'form':
                self.form_open = False

    for path in (Path(__file__).resolve().parents[1] / 'frontend').glob('*.html'):
        try:
            Page().feed(path.read_text(encoding='utf-8'))
        except AssertionError as error:
            raise AssertionError(f'{path.name}: {error}') from error


def test_cabinet_theme_never_changes_source_matched_arm():
    frontend = Path(__file__).resolve().parents[1] / 'frontend'
    for name in ('student', 'dds', 'map'):
        assert '/assets/cabinet.css' not in (frontend / f'{name}.html').read_text(encoding='utf-8')
    for name in ('login', 'portal', 'scenarios', 'instructor', 'generation',
                 'assessment', 'materials', 'tickets', 'operations', 'audit'):
        assert (frontend / f'{name}.html').read_text(encoding='utf-8').count('/assets/cabinet.css') == 1
