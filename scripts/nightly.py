"""Build, sign, and publish upstream LuckyTool releases."""
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

UPSTREAM = 'luckyzyx/LuckyTool'
EXPECTED_ASSETS = {'LuckyTool-nightly.apk', 'SHA256SUMS', 'build.json', 'upstream-source.tar.gz'}


def api(path):
    request = urllib.request.Request('https://api.github.com/' + path, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def apk_name(sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Invalid upstream SHA')
    return f'LuckyTool-nightly-{sha[:8]}.apk'


def expected_assets(sha):
    return (EXPECTED_ASSETS - {'LuckyTool-nightly.apk'}) | {apk_name(sha)}


def published(release, sha=None):
    if not release or release['draft']:
        return False
    names = {a['name'] for a in release['assets']}
    # Keep recognizing historical releases with the original APK name.
    return EXPECTED_ASSETS <= names or (sha is not None and expected_assets(sha) <= names)


def version_code(run_number):
    number = int(run_number)
    if not 1 <= number < 1_000_000_000:
        raise ValueError('Invalid workflow run number')
    return 1_000_000_000 + number


def release_tag(sha, now=None):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Invalid upstream SHA')
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    date = now.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
    return f'nightly-{date}-{sha[:8]}'


def release_for_commit(repo, sha):
    link = f'https://github.com/{UPSTREAM}/commit/{sha}'
    candidate = None
    page = 1
    while True:
        releases = api(f'repos/{repo}/releases?per_page=100&page={page}')
        if releases is None:
            raise RuntimeError('Cannot list repository releases')
        for release in releases:
            if f']({link})' not in (release.get('body') or ''):
                continue
            if published(release, sha):
                return release
            candidate = candidate or release
        if len(releases) < 100:
            return candidate
        page += 1


def plan():
    head = api(f'repos/{UPSTREAM}/commits/main')
    if not head or not re.fullmatch('[0-9a-f]{40}', head['sha']):
        raise RuntimeError('Cannot resolve upstream main')
    sha = head['sha']
    release = release_for_commit(os.environ['GITHUB_REPOSITORY'], sha)
    tag = release['tag_name'] if release else release_tag(sha)
    build = not published(release, sha)
    artifact_run = os.environ['GITHUB_RUN_ID']
    build_number = os.environ['GITHUB_RUN_NUMBER']
    reuse = os.environ.get('REUSE_RUN', '').strip()
    if reuse and build:
        if not reuse.isdigit():
            raise ValueError('Build run ID must be numeric')
        repo = os.environ['GITHUB_REPOSITORY']
        previous = api(f'repos/{repo}/actions/runs/{reuse}')
        if not previous or previous['head_repository']['full_name'] != repo or previous['head_branch'] != 'main' or previous['path'] != '.github/workflows/nightly.yml':
            raise RuntimeError('Only this repository main-branch nightly artifacts may be reused')
        jobs = api(f'repos/{repo}/actions/runs/{reuse}/jobs')
        if not any(j['name'] == 'build' and j['conclusion'] == 'success' for j in jobs['jobs']):
            raise RuntimeError('Selected run has no successful build')
        artifacts = api(f'repos/{repo}/actions/runs/{reuse}/artifacts')
        if not any(a['name'] == 'upstream-release' and not a['expired'] for a in artifacts['artifacts']):
            raise RuntimeError('Selected build artifact is unavailable')
        artifact_run = reuse
        build_number = str(previous['run_number'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'sha={sha}\ntag={tag}\nbuild={str(build).lower()}\n')
        output.write(f'reuse={str(bool(reuse and build)).lower()}\nartifact_run={artifact_run}\nbuild_number={build_number}\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(f'Upstream: [{sha}](https://github.com/{UPSTREAM}/commit/{sha})\n\n')
        summary.write('New or incomplete release: build required.\n' if build else 'Already published; skipped.\n')


def compile_sdk(source):
    """Read current Gropify properties, with support for older upstream builds."""
    properties = source / 'gradle.properties'
    if properties.exists():
        match = re.search(r'^\s*project\.android\.compileSdk\s*[=:]\s*([^\r\n]+)',
                          properties.read_text(), re.MULTILINE)
        if match:
            value = match[1].strip()
            if not re.fullmatch(r'[1-9]\d*', value):
                raise RuntimeError(f'Unsupported project.android.compileSdk: {value!r}')
            return value
    config = (source / 'build.gradle.kts').read_text()
    match = re.search(r'^\s*extra\["compileSdkVersion"\]\s*=\s*(\d+)\s*(?://[^\n]*)?$',
                      config, re.MULTILINE)
    if match:
        return match[1]
    raise RuntimeError('Cannot determine compile SDK: expected project.android.compileSdk '
                       'in gradle.properties or extra["compileSdkVersion"] in build.gradle.kts')


def prepare():
    source = Path('upstream')
    if run('git', '-C', str(source), 'rev-parse', 'HEAD') != os.environ['UPSTREAM_SHA']:
        raise RuntimeError('Unexpected source revision')
    # Gradle uses a temporary key; the release runner holds the distribution key.
    keys = source / 'keystore'
    keys.mkdir(exist_ok=True)
    # Generate the naming dictionary referenced by upstream proguard-rules.pro.
    (keys / 'proguard-custom.txt').write_text(''.join(f'lt{i:04x}\n' for i in range(4096)))
    key = (keys / 'build.p12').resolve()
    run('keytool', '-genkeypair', '-noprompt', '-keystore', str(key), '-storetype', 'PKCS12',
        '-storepass', 'temporary-build', '-keypass', 'temporary-build', '-alias', 'build',
        '-keyalg', 'RSA', '-keysize', '2048', '-validity', '2', '-dname', 'CN=Temporary Build')
    (keys / 'keystore.properties').write_text(
        f'storeFile={key}\nstorePassword=temporary-build\nkeyAlias=build\nkeyPassword=temporary-build\n')
    # getVersionCode() increments once during Gradle configuration.
    code = version_code(os.environ['GITHUB_RUN_NUMBER'])
    (source / 'app/version.properties').write_text(f'versionCode={code - 1}\n')
    sdk = compile_sdk(source)
    available = run('sdkmanager', '--list')
    packages = {line.split('|')[0].strip() for line in available.splitlines() if '|' in line}
    # Resolve SDK package names with an optional explicit minor version.
    platform = next((p for p in (f'platforms;android-{sdk}', f'platforms;android-{sdk}.0') if p in packages), None)
    if platform is None:
        raise RuntimeError(f'Official SDK platform {sdk} is not available')
    run('sdkmanager', platform)


def collect():
    source = Path('upstream')
    apks = list((source / 'app/build/outputs/apk/release').glob('*.apk'))
    if len(apks) != 1:
        raise RuntimeError(f'Expected one release APK, found {len(apks)}')
    artifact = Path('artifact')
    artifact.mkdir()
    (artifact / 'LuckyTool-nightly.apk').write_bytes(apks[0].read_bytes())
    # Archive the exact upstream commit recorded in the build metadata.
    run('git', '-C', str(source), 'archive', '--format=tar.gz',
        '--output=' + str((artifact / 'upstream-source.tar.gz').resolve()), 'HEAD')
    info = dict(upstream=UPSTREAM, commit=os.environ['UPSTREAM_SHA'],
                versionCode=version_code(os.environ['GITHUB_RUN_NUMBER']),
                workflow_commit=os.environ['GITHUB_SHA'],
                run=f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
                build_type='release', obfuscation_dictionary='lt0000 through lt0fff', version_override='app/version.properties: versionCode - 1 before Gradle')
    (artifact / 'build.json').write_text(json.dumps(info, indent=2) + '\n')


def has_xposed_metadata(manifest):
    return bool(re.search(r'"xposedmodule"[^\n]*\n\s*A: (?:android:|http://schemas.android.com/apk/res/android:)value[^\n]*=(?:true|\(type 0x12\)0xffffffff)\s*(?:\n|$)', manifest))


def validate_xposed(archive, manifest):
    modern_entry = 'META-INF/xposed/java_init.list'
    if modern_entry in archive.namelist():
        entries = [line.strip() for line in archive.read(modern_entry).decode().splitlines()
                   if line.strip() and not line.lstrip().startswith('#')]
        if not entries or not all(re.fullmatch(r'[\w$]+(?:\.[\w$]+)+', entry) for entry in entries):
            raise RuntimeError('Missing or invalid modern Xposed entry point')
        try:
            config = archive.read('META-INF/xposed/module.prop').decode()
        except KeyError as error:
            raise RuntimeError('Missing modern Xposed module.prop') from error
        versions = {}
        for key in ('minApiVersion', 'targetApiVersion'):
            match = re.search(r'^\s*' + key + r'\s*[=:]\s*(\d+)\s*$', config, re.MULTILINE)
            if not match or int(match[1]) <= 0:
                raise RuntimeError(f'Missing or invalid modern Xposed {key}')
            versions[key] = int(match[1])
        if versions['targetApiVersion'] < versions['minApiVersion']:
            raise RuntimeError('Xposed target API is below minimum API')
        return dict(format='libxposed', **versions)
    if not has_xposed_metadata(manifest):
        raise RuntimeError('Missing enabled Xposed module metadata')
    if 'assets/xposed_init' not in archive.namelist() or not archive.read('assets/xposed_init').strip():
        raise RuntimeError('Missing Xposed entry point')
    return dict(format='legacy')


def sign():
    artifact = Path('artifact')
    apk = artifact / 'LuckyTool-nightly.apk'
    info = json.loads((artifact / 'build.json').read_text())
    if info['commit'] != os.environ['UPSTREAM_SHA'] or info['versionCode'] != version_code(os.environ['BUILD_RUN_NUMBER']):
        raise RuntimeError('Artifact provenance mismatch')
    tools = Path(os.environ['ANDROID_HOME']) / 'build-tools'
    versions = [p for p in tools.iterdir() if re.fullmatch(r'\d+\.\d+\.\d+', p.name)]
    latest = max(versions, key=lambda p: tuple(map(int, p.name.split('.'))))
    aapt = str(latest / 'aapt2')
    badging = run(aapt, 'dump', 'badging', str(apk))
    if "package: name='com.luckyzyx.luckytool'" not in badging or f"versionCode='{info['versionCode']}'" not in badging:
        raise RuntimeError('Unexpected APK identity or version')
    if 'application-debuggable' in badging:
        raise RuntimeError('Refusing debuggable APK')
    manifest = run(aapt, 'dump', 'xmltree', str(apk), '--file', 'AndroidManifest.xml')
    with zipfile.ZipFile(apk) as archive:
        info['xposed'] = validate_xposed(archive, manifest)
    key = Path(os.environ['RUNNER_TEMP']) / 'nightly-signing.p12'
    try:
        key.write_bytes(base64.b64decode(os.environ['SIGNING_KEYSTORE_BASE64'], validate=True))
        key.chmod(0o600)
        signed = artifact / 'signed.apk'
        signer = str(latest / 'apksigner')
        run(signer, 'sign', '--ks', str(key), '--ks-key-alias', os.environ['SIGNING_KEY_ALIAS'],
            '--ks-pass', 'env:SIGNING_STORE_PASSWORD', '--key-pass', 'env:SIGNING_KEY_PASSWORD',
            '--out', str(signed), str(apk))
        run(signer, 'verify', '--verbose', str(signed))
        certificate = subprocess.check_output(['keytool', '-exportcert', '-keystore', str(key),
            '-alias', os.environ['SIGNING_KEY_ALIAS'], '-storepass:env', 'SIGNING_STORE_PASSWORD'])
        digest = hashlib.sha256(certificate).hexdigest()
        info['apk'] = apk_name(info['commit'])
        signed.replace(artifact / info['apk'])
        apk.unlink()
        (artifact / 'signed.apk.idsig').unlink(missing_ok=True)
        info['certificate_sha256'] = digest
        (artifact / 'build.json').write_text(json.dumps(info, indent=2) + '\n')
    finally:
        key.unlink(missing_ok=True)
    lines = [f'{hashlib.sha256((artifact / name).read_bytes()).hexdigest()}  {name}'
             for name in sorted(expected_assets(info['commit']) - {'SHA256SUMS'})]
    (artifact / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')


def publish():
    repo = os.environ['GITHUB_REPOSITORY']
    sha = os.environ['UPSTREAM_SHA']
    tag = os.environ['RELEASE_TAG']
    if not re.fullmatch(r'nightly-\d{4}-\d{2}-\d{2}-' + sha[:8], tag):
        raise RuntimeError('Release tag does not match upstream commit')
    release = release_for_commit(repo, sha)
    if published(release, sha):
        return
    if release and release['tag_name'] != tag:
        raise RuntimeError('Release changed after planning; retry the workflow')
    if release and not release['draft']:
        raise RuntimeError('Existing published release is incomplete; inspect it before retrying')
    notes = Path(os.environ['RUNNER_TEMP']) / 'release-notes.md'
    module = json.loads(Path('artifact/build.json').read_text()).get('xposed', {})
    compatibility = (f"需要支持 libxposed API {module['minApiVersion']} 或更高版本的框架。"
                     if module.get('format') == 'libxposed' else '适用于传统 Xposed API 框架。')
    notes.write_text(f'''LuckyTool 非官方 Nightly。

{compatibility}

上游版本：[{sha[:8]}](https://github.com/{UPSTREAM}/commit/{sha})

下载下方附件 `{apk_name(sha)}`，安装后在 LSPosed 中启用并选择作用域。
''')
    if not release:
        run('gh', 'release', 'create', tag, '--repo', repo, '--target', os.environ['GITHUB_SHA'],
            '--draft', '--prerelease', '--title', f'LuckyTool nightly · {tag[8:18]} · {sha[:8]}', '--notes-file', str(notes))
    run('gh', 'release', 'upload', tag, '--repo', repo, '--clobber',
        *(str(Path('artifact') / name) for name in sorted(expected_assets(sha))))
    run('gh', 'release', 'edit', tag, '--repo', repo, '--draft=false', '--prerelease', '--notes-file', str(notes))


if __name__ == '__main__':
    {'plan': plan, 'prepare': prepare, 'collect': collect, 'sign': sign, 'publish': publish}[sys.argv[1]]()
