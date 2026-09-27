import importlib.util
from datetime import datetime, timezone
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('nightly', Path(__file__).parents[1] / 'scripts/nightly.py')
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)


class Contracts(unittest.TestCase):
    def test_only_complete_published_release_skips_build(self):
        release = {'draft': False, 'assets': [{'name': n} for n in nightly.EXPECTED_ASSETS]}
        self.assertTrue(nightly.published(release))
        release['draft'] = True
        self.assertFalse(nightly.published(release))
        release['draft'] = False
        release['assets'].pop()
        self.assertFalse(nightly.published(release))
        self.assertFalse(nightly.published(None))

    def test_tag_uses_shanghai_date_and_eight_digit_sha(self):
        sha = 'a' * 40
        now = datetime(2026, 9, 27, 16, 5, tzinfo=timezone.utc)
        self.assertEqual(nightly.release_tag(sha, now), 'nightly-2026-09-28-aaaaaaaa')
        with self.assertRaises(ValueError):
            nightly.release_tag('invalid', now)

    def test_complete_commit_on_an_earlier_date_skips_build(self):
        sha = 'a' * 40
        release = {'tag_name': 'nightly-2026-09-01-aaaaaaaa', 'draft': False,
                   'body': f'[commit](https://github.com/{nightly.UPSTREAM}/commit/{sha})',
                   'assets': [{'name': n} for n in nightly.EXPECTED_ASSETS]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            env = {'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(Path(directory) / 'summary'),
                   'GITHUB_REPOSITORY': 'owner/builds', 'GITHUB_RUN_ID': '100',
                   'GITHUB_RUN_NUMBER': '10', 'REUSE_RUN': ''}
            with patch.dict(os.environ, env), patch.object(nightly, 'api', side_effect=[{'sha': sha}, [release]]):
                nightly.plan()
            self.assertIn('build=false', output.read_text())
            self.assertIn('tag=nightly-2026-09-01-aaaaaaaa', output.read_text())

    def test_short_sha_collision_does_not_skip_another_commit(self):
        sha = 'a' * 40
        other = 'a' * 8 + 'b' * 32
        release = {'tag_name': 'nightly-2026-09-27-aaaaaaaa', 'draft': False,
                   'body': f'[commit](https://github.com/{nightly.UPSTREAM}/commit/{other})',
                   'assets': [{'name': n} for n in nightly.EXPECTED_ASSETS]}
        with patch.object(nightly, 'api', return_value=[release]):
            self.assertIsNone(nightly.release_for_commit('owner/builds', sha))

    def test_release_lookup_paginates_and_retains_unfinished_draft(self):
        sha = 'a' * 40
        draft = {'tag_name': 'nightly-2026-09-01-aaaaaaaa', 'draft': True,
                 'body': f'[commit](https://github.com/{nightly.UPSTREAM}/commit/{sha})', 'assets': []}
        with patch.object(nightly, 'api', side_effect=[[{'body': ''}] * 100, [draft]]) as api:
            self.assertEqual(nightly.release_for_commit('owner/builds', sha), draft)
            self.assertIn('page=2', api.call_args.args[0])

    def test_xposed_metadata_accepts_aapt2_boolean_but_not_false(self):
        text = 'A: http://schemas.android.com/apk/res/android:name(0x01010003)="xposedmodule" (Raw: "xposedmodule")\n  A: http://schemas.android.com/apk/res/android:value(0x01010024)=true\n'
        self.assertTrue(nightly.has_xposed_metadata(text))
        self.assertFalse(nightly.has_xposed_metadata(text.replace('=true', '=false')))
        self.assertFalse(nightly.has_xposed_metadata(text.replace('xposedmodule', 'other')))

    def test_versions_are_monotonic_and_bounded(self):
        self.assertLess(nightly.version_code('1'), nightly.version_code('2'))
        self.assertLess(nightly.version_code('999999999'), 2_100_000_000)
        for value in ['0', '-1', '1000000000', 'bad']:
            with self.assertRaises(ValueError):
                nightly.version_code(value)

    def test_api_errors_do_not_become_false_no_change(self):
        import urllib.error
        with patch.dict('os.environ', GH_TOKEN='test'):
            with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('url', 403, 'denied', {}, None)):
                with self.assertRaises(urllib.error.HTTPError):
                    nightly.api('repos/example/test')
            with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('url', 404, 'missing', {}, None)):
                self.assertIsNone(nightly.api('repos/example/test'))


if __name__ == '__main__':
    unittest.main()
