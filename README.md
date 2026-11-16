# 科研收集 · Research Collector

手机收集科研工具、配色和教程，Windows 接收整理并写入 Obsidian。包含 Android 原生 Java 客户端、HarmonyOS ArkTS 客户端和 Python 接收端。

## 功能

- 分享文字、链接、图片和视频；剪贴板预览后确认入队。
- 手机、平板可通过「上传 PDF / Word」选择论文文件；支持 `.pdf`、`.docx`、`.doc`，保留原文件并沿用离线队列及分块校验。PDF、DOCX 提取正文和页码／段落／表格定位；扫描 PDF 明确提示需 OCR，旧 DOC 提示转换为 DOCX。
- 分类及低、中、高重要程度；离线持久队列、局域网 HTTPS 同步。
- 电脑保留原文和附件，尝试补齐公开页面，缺失内容标为待补。
- 可选 OCR、视频转录、绘图类型与参数记录；来源卡和专题索引。
- 绘图与配色帖子逐张调用本地视觉模型，分别记录可见图形、布局、数据映射、样式、复现建议和不确定项。详情显示已分析／已保存图片数；OCR 完成不等于视觉分析完成，下载不到的原图和视频不计入图片完成率。
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

逐图分析需要 Ollama 的 `qwen3-vl:4b-instruct`（`ollama pull qwen3-vl:4b-instruct`），可用环境变量 `RESEARCH_COLLECTOR_VISION_MODEL` 指定兼容视觉模型。默认连接本机 11434；使用项目自带 Ollama 时连接 11437，需在同一模型目录安装模型。模型未安装或调用失败时保持「待分析」，不会把 OCR 标成读图完成。旧记录也会补做视觉分析；成功结果按素材校验值缓存，失败约一小时后重试，可从手机「重新处理」提前重试。模型观察与复现建议需人工复核，不能视为作者明确提供的步骤或参数。

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

当前版本：Android / HarmonyOS 1.5.0。两端编译通过，Android 安装包签名验证通过；已覆盖安装至 nova，验证上传按钮可打开系统文档选择器。30 项自动测试通过（包含文档传输、校验、入库与提取）。鸿蒙／平板真机及手机文件选择到入库的完整操作链尚待验收。

nova 上验证点击原帖直接唤起小红书。测试机小红书未登录，登录后是否落到具体作品仍待验证；鸿蒙原生跳转待实机验证。

## 发布边界

仓库只含源码、构建脚本及说明，不包含采集内容、知识库、账号登录状态、配对凭证、证书、私钥、模型或第三方安装包。

原生跳转接口参考：[Android Intent](https://developer.android.com/guide/components/intents-filters)、[HarmonyOS App Linking](https://developer.huawei.com/consumer/en/doc/harmonyos-guides/app-linking-startupapp/)、[小红书深链](https://pages.xiaohongshu.com/activity/deeplink)。
