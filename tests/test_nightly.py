import importlib.util
import io
import json
import hashlib
import zipfile
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
    def test_compile_sdk_supports_current_and_legacy_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'build.gradle.kts').write_text('extra["compileSdkVersion"] = 36\n')
            self.assertEqual(nightly.compile_sdk(source), '36')
            properties = source / 'gradle.properties'
            properties.write_text('# project.android.compileSdk=35\nproject.android.compileSdk=37\n')
            self.assertEqual(nightly.compile_sdk(source), '37')
            (source / 'build.gradle.kts').write_text('extra["jdkVersion"] = 21\n')
            self.assertEqual(nightly.compile_sdk(source), '37')
            properties.write_text('project.android.compileSdk=unknown\n')
            with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
                nightly.compile_sdk(source)
            properties.write_text('# project.android.compileSdk=37\n')
            with self.assertRaisesRegex(RuntimeError, 'Cannot determine compile SDK'):
                nightly.compile_sdk(source)

    def test_xposed_archive_formats_and_incomplete_modern_metadata(self):
        def check(files, manifest=''):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            with zipfile.ZipFile(buffer) as archive:
                return nightly.validate_xposed(archive, manifest)
        files = {'META-INF/xposed/java_init.list': '# entry\nexample.Module\n',
                 'META-INF/xposed/module.prop': 'minApiVersion=101\ntargetApiVersion=102\n'}
        self.assertEqual(check(files)['minApiVersion'], 101)
        for bad in ('', 'minApiVersion=102\ntargetApiVersion=101\n'):
            with self.assertRaises(RuntimeError):
                check(dict(files, **{'META-INF/xposed/module.prop': bad}))
        with self.assertRaises(RuntimeError):
            check({'META-INF/xposed/java_init.list': 'example.Module'})
        with self.assertRaises(RuntimeError):
            check(dict(files, **{'META-INF/xposed/java_init.list': '# comment only'}))
        manifest = 'A: name="xposedmodule"\n A: android:value=true\n'
        self.assertEqual(check({'assets/xposed_init': 'example.Module'}, manifest)['format'], 'legacy')
        with self.assertRaises(RuntimeError):
            check({}, manifest)

    def test_only_complete_published_release_skips_build(self):
        release = {'draft': False, 'assets': [{'name': n} for n in nightly.EXPECTED_ASSETS]}
        self.assertTrue(nightly.published(release))
        release['draft'] = True
        self.assertFalse(nightly.published(release))
        release['draft'] = False
        release['assets'].pop()
        self.assertFalse(nightly.published(release))
        self.assertFalse(nightly.published(None))

    def test_hashed_release_assets_and_legacy_compatibility(self):
        sha = 'a' * 40
        self.assertEqual(nightly.apk_name(sha), 'LuckyTool-nightly-aaaaaaaa.apk')
        with self.assertRaises(ValueError):
            nightly.apk_name('../bad')
        release = {'draft': False, 'assets': [{'name': n} for n in nightly.expected_assets(sha)]}
        self.assertTrue(nightly.published(release, sha))
        self.assertFalse(nightly.published(release, 'b' * 40))
        release['assets'] = [a for a in release['assets'] if a['name'] != 'SHA256SUMS']
        self.assertFalse(nightly.published(release, sha))
        release['assets'] = [{'name': n} for n in nightly.EXPECTED_ASSETS]
        self.assertTrue(nightly.published(release, sha))

    def test_sign_renames_apk_and_checksums_use_published_name(self):
        sha = 'a' * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'artifact'
            artifact.mkdir()
            with zipfile.ZipFile(artifact / 'LuckyTool-nightly.apk', 'w') as archive:
                archive.writestr('META-INF/xposed/java_init.list', 'example.Module')
                archive.writestr('META-INF/xposed/module.prop', 'minApiVersion=102\ntargetApiVersion=102')
            (artifact / 'build.json').write_text(json.dumps({'commit': sha, 'versionCode': 1000000001}))
            (artifact / 'upstream-source.tar.gz').write_bytes(b'source')
            (root / 'build-tools/37.0.0').mkdir(parents=True)
            env = {'UPSTREAM_SHA': sha, 'BUILD_RUN_NUMBER': '1', 'ANDROID_HOME': str(root),
                   'RUNNER_TEMP': str(root), 'SIGNING_KEYSTORE_BASE64': 'a2V5', 'SIGNING_KEY_ALIAS': 'test'}
            def command(*args):
                if 'badging' in args:
                    return "package: name='com.luckyzyx.luckytool' versionCode='1000000001'"
                if 'sign' in args:
                    Path(args[args.index('--out') + 1]).write_bytes(b'signed APK')
                return ''
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, env), patch.object(nightly, 'run', side_effect=command), \
                     patch.object(nightly.subprocess, 'check_output', return_value=b'certificate'):
                    nightly.sign()
            finally:
                os.chdir(previous)
            self.assertFalse((artifact / 'LuckyTool-nightly.apk').exists())
            self.assertEqual((artifact / nightly.apk_name(sha)).read_bytes(), b'signed APK')
            info = json.loads((artifact / 'build.json').read_text())
            self.assertEqual(info['apk'], nightly.apk_name(sha))
            lines = (artifact / 'SHA256SUMS').read_text().splitlines()
            self.assertEqual(len(lines), 3)
            for line in lines:
                digest, name = line.split('  ')
                self.assertEqual(digest, hashlib.sha256((artifact / name).read_bytes()).hexdigest())

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
