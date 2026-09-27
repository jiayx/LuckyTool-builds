import importlib.util
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
