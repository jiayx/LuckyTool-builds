# LuckyTool-builds

[上游 LuckyTool](https://github.com/luckyzyx/LuckyTool) 的非官方自动构建，提供适用于 **LSPosed** 的 Release APK。

## 下载与安装

从 [Releases](https://github.com/jiayx/LuckyTool-builds/releases) 下载 `LuckyTool-nightly.apk`，安装后在 LSPosed 中启用并选择作用域。Nightly 以预发布形式提供。

安装包使用固定的独立签名，本仓库的后续版本可覆盖升级。从官方版或其他签名版本切换时，通常需要先备份配置、卸载，再安装。

每个 Release 包含：

- `LuckyTool-nightly.apk`：已签名安装包。
- `SHA256SUMS`：附件校验和。
- `build.json`：上游提交、构建版本、工作流和签名证书信息。
- `upstream-source.tar.gz`：对应上游提交的源码快照，包含上游许可证。

## 自动构建

每天北京时间 **23:17** 检查上游 `main`。同一提交已有完整发布时跳过；待发布的提交进入构建流程。

支持在 **Actions → LuckyTool nightly → Run workflow** 手动触发。构建成功但发布失败时，可在 `reuse_build_run` 填入该次运行 ID，复用产物完成签名发布。复用要求：

- 来自本仓库 `main` 分支的成功构建。
- 产物记录的上游提交与当前上游一致。
- 产物仍在 7 天保留期内。

GitHub 定时任务可能延迟；公开仓库连续 60 天没有活动时，可能需要在 Actions 中重新启用。工作流位于默认分支。[GitHub 调度说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 构建配置

- 检出上游完整提交 SHA，使用上游 Gradle Wrapper、JDK 21 和对应 Android SDK。
- 执行 `:app:assembleRelease --build-cache`，使用上游混淆、压缩和作用域配置。
- `gradle/actions/setup-gradle` 缓存 Wrapper、依赖、Java 工具链及 Gradle 任务输出；输入变化的任务重新执行。
- CI 为 `keystore/proguard-custom.txt` 生成固定命名字典（`lt0000`～`lt0fff`）。
- `versionCode` 为 `1000000000 + github.run_number`，通过 `app/version.properties` 传入；`versionName` 使用上游值。
- 发布标签为 `nightly-<完整上游 SHA>`，附件上传完成后公开为预发布。
- Actions 固定完整提交 SHA，旁注对应版本；Dependabot 每周检查更新并创建 PR。

## 签名配置

Gradle 构建使用临时签名。独立发布任务使用长期密钥重新签名，并校验包名、版本号、Release 标志、Xposed 元数据及入口文件。

仓库需要以下 Actions Secrets：

| Secret | 内容 |
| --- | --- |
| `SIGNING_KEYSTORE_BASE64` | 长期 PKCS12/JKS 密钥文件的 Base64 内容 |
| `SIGNING_STORE_PASSWORD` | 密钥库密码 |
| `SIGNING_KEY_PASSWORD` | 私钥密码 |
| `SIGNING_KEY_ALIAS` | 密钥别名 |

长期密钥和密码应单独备份，用于后续覆盖升级。发布权限由工作流内置的 `GITHUB_TOKEN` 提供。

## 本地检查

```sh
python3 -m unittest discover -s tests
actionlint .github/workflows/nightly.yml
```
