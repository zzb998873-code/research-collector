# 科研收集 · Research Collector

手机收集科研工具、配色和教程，Windows 接收整理并写入 Obsidian。包含 Android 原生 Java 客户端、HarmonyOS ArkTS 客户端和 Python 接收端。

## 功能

- 分享文字、链接、图片和视频；剪贴板预览后确认入队。
- 分类及低、中、高重要程度；离线持久队列、局域网 HTTPS 同步。
- 电脑保留原文和附件，尝试补齐公开页面，缺失内容标为待补。
- 可选 OCR、视频转录、绘图类型与参数记录；来源卡和专题索引。
- 处理记录、详情、补充原记录、重新处理；暖色漫画风原生界面。
- 打开原帖优先原生 App，不自动跳浏览器。Android 对小红书/抖音指定应用包，标准作品链接可走深链；HarmonyOS 使用仅 App Linking 模式。无法唤起时提示复制链接或手动选择网页。

平台内的自定义分享菜单不一定列出本 App，可以复制链接。没有实现点赞和收藏的自动监听。登录、平台验证、作品下架及 App 链接支持情况可能影响获取或跳转。

## Windows

推荐 Python 3.12，使用独立虚拟环境：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r windows/requirements.txt
.venv/Scripts/python windows/collector.py --state .runtime setup --vault 'D:/MyVault' --host '192.168.1.10'
.venv/Scripts/python windows/collector.py --state .runtime serve
```

将主机地址改为电脑的局域网地址。手机和电脑连接同一局域网，手机导入 `.runtime/配对.json`。该文件含连接凭证，请勿上传或共享。接收端写入 `raw/inbox/`、`raw/assets/`、`wiki/sources/` 等位置；先用测试知识库验证目录约定。

本地识别依赖见 `windows/requirements-models.txt`。模型及浏览器登录状态均保存在本机。Windows 自动启动安装脚本见 `windows/安装接收端.ps1`；自定义知识库建议先按上述命令完成 setup，再检查脚本配置后使用。

## Android 构建

安装 Android SDK Platform 35、Build Tools 35.0.0 及 JDK。SDK 可放在 `.runtime/android`，或传入路径：

```powershell
./windows/build-android.ps1 -SdkPath 'D:/Android/Sdk' -JavaHome 'D:/Java/jdk'
```

APK 输出到 `release/`。本机构建会生成独立调试签名；升级既有安装必须使用原签名。

## HarmonyOS 构建

安装 DevEco Studio 与对应 HarmonyOS SDK。首次构建：

```powershell
Copy-Item harmony/build-profile.example.json5 harmony/build-profile.json5
```

用 DevEco Studio 打开 `harmony/`，在签名设置中为自己的设备生成签名，然后执行：

```powershell
./windows/build-harmony.ps1 -StudioPath 'D:/DevEco Studio'
```

签名配置只在本机保存，不能提交。个人设备调试 HAP 不作为通用安装包发布。

## 验证

```powershell
.venv/Scripts/python -m unittest discover -s windows -p 'test_*.py'
```

当前版本：Android 1.4.3；HarmonyOS 1.4.4。两端编译通过；HarmonyOS 1.4.4 已安装到 Mate 90，并检查了圆角立体按钮的真机显示。

nova 上验证点击原帖直接唤起小红书。测试机小红书未登录，登录后是否落到具体作品仍待验证；鸿蒙原生跳转待实机验证。

## 发布边界

仓库只含源码、构建脚本及说明，不包含采集内容、知识库、账号登录状态、配对凭证、证书、私钥、模型或第三方安装包。

原生跳转接口参考：[Android Intent](https://developer.android.com/guide/components/intents-filters)、[HarmonyOS App Linking](https://developer.huawei.com/consumer/en/doc/harmonyos-guides/app-linking-startupapp/)、[小红书深链](https://pages.xiaohongshu.com/activity/deeplink)。
