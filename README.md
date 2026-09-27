# LuckyTool-builds

[上游 LuckyTool](https://github.com/luckyzyx/LuckyTool) 的非官方自动构建，适用于 **LSPosed**。

## 下载与安装

**[前往 Releases 下载安装包](https://github.com/jiayx/LuckyTool-builds/releases)**

下载 `LuckyTool-nightly.apk`，安装后在 LSPosed 中启用并选择作用域。

若安装时提示签名不兼容，请先备份配置，卸载已有版本后再安装。

## 自动构建

每天北京时间 **23:17** 检查上游 `main`。有新提交时构建 Release APK 并发布到 GitHub Releases；已发布的提交直接跳过。

Tag 格式为 `nightly-YYYY-MM-DD-<8 位提交号>`，例如 `nightly-2026-09-27-37dd9e9d`。日期采用北京时间。Nightly 以预发布形式提供。

可在 **Actions → LuckyTool nightly → Run workflow** 手动触发。构建成功但发布失败时，在 `reuse_build_run` 填入该次运行 ID，可复用 7 天内的构建产物重试发布；产物的上游提交须与当前上游一致。

## 维护配置

仓库需要以下 Actions Secrets：

| Secret | 内容 |
| --- | --- |
| `SIGNING_KEYSTORE_BASE64` | PKCS12/JKS 签名密钥文件的 Base64 内容 |
| `SIGNING_STORE_PASSWORD` | 密钥库密码 |
| `SIGNING_KEY_PASSWORD` | 私钥密码 |
| `SIGNING_KEY_ALIAS` | 密钥别名 |

密钥和密码应单独备份。Actions 固定提交版本，Dependabot 每周检查更新。

GitHub 定时任务可能延迟；公开仓库连续 60 天没有活动时，可能需要在 Actions 中重新启用。[GitHub 调度说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

本地检查：

```sh
python3 -m unittest discover -s tests
actionlint .github/workflows/nightly.yml
```
