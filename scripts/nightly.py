"""Small, dependency-free helpers for the upstream build/release workflow."""
import base64
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


def published(release):
    return bool(release and not release['draft'] and
                EXPECTED_ASSETS <= {a['name'] for a in release['assets']})


def version_code(run_number):
    number = int(run_number)
    if not 1 <= number < 1_000_000_000:
        raise ValueError('Invalid workflow run number')
    return 1_000_000_000 + number


def plan():
    head = api(f'repos/{UPSTREAM}/commits/main')
    if not head or not re.fullmatch('[0-9a-f]{40}', head['sha']):
        raise RuntimeError('Cannot resolve upstream main')
    sha = head['sha']
    tag = 'nightly-' + sha
    release = api(f"repos/{os.environ['GITHUB_REPOSITORY']}/releases/tags/{tag}")
    build = not published(release)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'sha={sha}\ntag={tag}\nbuild={str(build).lower()}\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(f'Upstream: [{sha}](https://github.com/{UPSTREAM}/commit/{sha})\n\n')
        summary.write('New or incomplete release: build required.\n' if build else 'Already published; skipped.\n')


def prepare():
    source = Path('upstream')
    if run('git', '-C', str(source), 'rev-parse', 'HEAD') != os.environ['UPSTREAM_SHA']:
        raise RuntimeError('Unexpected source revision')
    # Upstream requires a keystore even for configuration. This disposable build key
    # is not the distribution key; the latter is only available on the release runner.
    keys = source / 'keystore'
    keys.mkdir(exist_ok=True)
    key = (keys / 'build.p12').resolve()
    run('keytool', '-genkeypair', '-noprompt', '-keystore', str(key), '-storetype', 'PKCS12',
        '-storepass', 'temporary-build', '-keypass', 'temporary-build', '-alias', 'build',
        '-keyalg', 'RSA', '-keysize', '2048', '-validity', '2', '-dname', 'CN=Temporary Build')
    (keys / 'keystore.properties').write_text(
        f'storeFile={key}\nstorePassword=temporary-build\nkeyAlias=build\nkeyPassword=temporary-build\n')
    # getVersionCode() increments once during Gradle configuration.
    code = version_code(os.environ['GITHUB_RUN_NUMBER'])
    (source / 'app/version.properties').write_text(f'versionCode={code - 1}\n')
    config = (source / 'build.gradle.kts').read_text()
    sdk = re.search(r'extra\["compileSdkVersion"\]\s*=\s*(\d+)', config)
    if not sdk:
        raise RuntimeError('Upstream SDK configuration changed; update the workflow')
    available = run('sdkmanager', '--list')
    packages = {line.split('|')[0].strip() for line in available.splitlines() if '|' in line}
    # Recent SDKs use an explicit minor version, e.g. android-37.0.
    platform = next((p for p in (f'platforms;android-{sdk[1]}', f'platforms;android-{sdk[1]}.0') if p in packages), None)
    if platform is None:
        raise RuntimeError(f'Official SDK platform {sdk[1]} is not available')
    run('sdkmanager', platform)


def collect():
    source = Path('upstream')
    apks = list((source / 'app/build/outputs/apk/release').glob('*.apk'))
    if len(apks) != 1:
        raise RuntimeError(f'Expected one release APK, found {len(apks)}')
    artifact = Path('artifact')
    artifact.mkdir()
    (artifact / 'LuckyTool-nightly.apk').write_bytes(apks[0].read_bytes())
    # Archive committed upstream sources, not generated keys or local build output.
    run('git', '-C', str(source), 'archive', '--format=tar.gz',
        '--output=' + str((artifact / 'upstream-source.tar.gz').resolve()), 'HEAD')
    info = dict(upstream=UPSTREAM, commit=os.environ['UPSTREAM_SHA'],
                versionCode=version_code(os.environ['GITHUB_RUN_NUMBER']),
                workflow_commit=os.environ['GITHUB_SHA'],
                run=f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
                build_type='release', version_override='app/version.properties: versionCode - 1 before Gradle')
    (artifact / 'build.json').write_text(json.dumps(info, indent=2) + '\n')


def sign():
    artifact = Path('artifact')
    apk = artifact / 'LuckyTool-nightly.apk'
    info = json.loads((artifact / 'build.json').read_text())
    if info['commit'] != os.environ['UPSTREAM_SHA'] or info['versionCode'] != version_code(os.environ['GITHUB_RUN_NUMBER']):
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
    if not re.search(r'"xposedmodule"[^\n]*\n\s*A: android:value[^\n]*0xffffffff', manifest):
        raise RuntimeError('Missing enabled Xposed module metadata')
    with zipfile.ZipFile(apk) as archive:
        if not archive.read('assets/xposed_init').strip():
            raise RuntimeError('Missing Xposed entry point')
    key = Path(os.environ['RUNNER_TEMP']) / 'nightly-signing.p12'
    try:
        key.write_bytes(base64.b64decode(os.environ['SIGNING_KEYSTORE_BASE64'], validate=True))
        key.chmod(0o600)
        signed = artifact / 'signed.apk'
        signer = str(latest / 'apksigner')
        run(signer, 'sign', '--ks', str(key), '--ks-key-alias', os.environ['SIGNING_KEY_ALIAS'],
            '--ks-pass', 'env:SIGNING_STORE_PASSWORD', '--key-pass', 'env:SIGNING_KEY_PASSWORD',
            '--out', str(signed), str(apk))
        certificate = run(signer, 'verify', '--verbose', '--print-certs', str(signed))
        digest = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-f]+)', certificate)
        if not digest:
            raise RuntimeError('Signed APK verification failed')
        signed.replace(apk)
        (artifact / 'signed.apk.idsig').unlink(missing_ok=True)
        info['certificate_sha256'] = digest[1]
        (artifact / 'build.json').write_text(json.dumps(info, indent=2) + '\n')
    finally:
        key.unlink(missing_ok=True)
    lines = [f'{hashlib.sha256((artifact / name).read_bytes()).hexdigest()}  {name}'
             for name in sorted(EXPECTED_ASSETS - {'SHA256SUMS'})]
    (artifact / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')


def publish():
    repo = os.environ['GITHUB_REPOSITORY']
    sha = os.environ['UPSTREAM_SHA']
    tag = 'nightly-' + sha
    release = api(f'repos/{repo}/releases/tags/{tag}')
    if published(release):
        return
    if release and not release['draft']:
        raise RuntimeError('Existing published release is incomplete; inspect it before retrying')
    info = json.loads(Path('artifact/build.json').read_text())
    notes = Path(os.environ['RUNNER_TEMP']) / 'release-notes.md'
    notes.write_text(f'''非官方 LuckyTool 自动构建，适用于 LSPosed。使用上游原版功能与作用域。

- 上游提交：[ {sha[:12]} ](https://github.com/{UPSTREAM}/commit/{sha})
- 构建类型：Release；versionCode：{info['versionCode']}
- 构建记录：{info['run']}
- 签名证书 SHA-256：`{info['certificate_sha256']}`

下载 `LuckyTool-nightly.apk` 安装，在 LSPosed 中启用并按原版要求选择作用域。
与官方签名不同，通常不能直接覆盖官方版；本仓库后续构建保持同一签名。
编译成功不代表所有功能已在设备上验证。源码快照随附件提供；仅覆盖构建版本号，未修改业务代码。
''')
    if not release:
        run('gh', 'release', 'create', tag, '--repo', repo, '--target', os.environ['GITHUB_SHA'],
            '--draft', '--prerelease', '--title', 'LuckyTool nightly · ' + sha[:12], '--notes-file', str(notes))
    run('gh', 'release', 'upload', tag, '--repo', repo, '--clobber',
        *(str(Path('artifact') / name) for name in sorted(EXPECTED_ASSETS)))
    run('gh', 'release', 'edit', tag, '--repo', repo, '--draft=false', '--prerelease', '--notes-file', str(notes))


if __name__ == '__main__':
    {'plan': plan, 'prepare': prepare, 'collect': collect, 'sign': sign, 'publish': publish}[sys.argv[1]]()
