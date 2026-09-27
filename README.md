# LuckyTool-builds

[上游 LuckyTool](https://github.com/luckyzyx/LuckyTool) 的非官方自动构建，提供可在 **LSPosed** 中启用的 Release APK。保留上游业务代码、Xposed 入口及作用域；不包含 Zygisk 移植。

每天北京时间 **23:17** 检查上游 `main`。该提交已有完整发布时直接退出；失败或未发布的提交会在下次运行重试。支持 Actions → LuckyTool nightly → Run workflow 手动触发。

从本仓库 Releases 下载 `LuckyTool-nightly.apk`，安装后在 LSPosed 中启用并选择作用域。Nightly 属于预发布，编译通过不代表所有功能经过实机验证。

## 签名与安装

本仓库使用固定的独立签名，后续版本可覆盖升级。官方版或其他签名版本通常需要先备份配置、卸载，再安装本仓库版本。不要每次生成新密钥，丢失密钥会失去覆盖升级能力。

长期签名密钥只提供给独立发布 runner，上游 Gradle 构建使用一次性临时密钥。发布时重新签名并验证包名、版本号、不可调试标志、Xposed 元数据和 `assets/xposed_init`。

首次部署需设置仓库 Actions Secrets：

| Secret | 内容 |
| --- | --- |
| `SIGNING_KEYSTORE_BASE64` | 长期 PKCS12/JKS 密钥文件的 Base64 内容 |
| `SIGNING_STORE_PASSWORD` | 密钥库密码 |
| `SIGNING_KEY_PASSWORD` | 私钥密码 |
| `SIGNING_KEY_ALIAS` | 密钥别名 |

密钥和密码需要单独备份，不提交到 Git。工作流用内置 `GITHUB_TOKEN` 发布，无需个人访问令牌。

## 构建与发布

- 使用 `gradle/actions/setup-gradle` 缓存 Wrapper、依赖、Java 工具链和 Gradle 缓存；`--build-cache` 开启可缓存任务输出的复用。无需与 setup-java 的 Gradle 缓存重复配置。输入变化的任务仍会重新执行，签名密钥不缓存。
- 每次锁定上游完整提交 SHA，使用上游 Gradle Wrapper、JDK 21 和配置要求的 Android SDK。
- 上游引用未提交的 `keystore/proguard-custom.txt`，CI 生成固定命名字典（`lt0000`～`lt0fff`），保留上游全部混淆规则。
- 执行 `:app:assembleRelease`，保留上游混淆、压缩、包名与功能代码。
- versionCode 为 `1000000000 + github.run_number`，通过上游 `version.properties` 输入；APK versionName 保留上游值。迁移工作流或仓库时须继续原有版本序列。
- 发布标签为 `nightly-<完整上游 SHA>`，避免重复发布。先上传完整 draft，再公开为 prerelease；中断后可重试。
- 附件包括 APK、SHA256SUMS、构建记录 `build.json`、对应上游源码快照。源码快照保留上游许可证；构建记录说明版本号调整。
- 最新下载请打开 Releases 列表；GitHub 的 `/releases/latest` 不指向 prerelease。
- Actions 使用 2026-09-27 查询到的最新正式版本，固定完整提交 SHA；Dependabot 每周检查更新并提 PR。

GitHub 的定时运行可能延迟；公开仓库 60 天没有活动时，定时工作流可能被暂停，需要在 Actions 中重新启用。工作流必须位于默认分支。[GitHub 调度说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

本地验证：`python3 -m unittest discover -s tests`；工作流可使用 actionlint 校验。不要将构建产物、临时上游检出或密钥提交到仓库。
