# Things Windows 非官方客户端

这是一个独立的 Windows 桌面客户端，通过公开社区协议读取和写入 Things
Cloud，使 Windows 与 macOS、iPhone、iPad 上的 Things 共享任务数据。

> **非官方项目**：本项目与 Cultured Code 没有关联，也不受其支持或背书。
> Things 是 Cultured Code 的商标。使用非公开 Cloud 协议存在兼容性和账号
> 风险，请先备份重要数据。

## 当前能力

- 原生桌面窗口（pywebview + Microsoft Edge WebView2）。
- 从 Things Cloud 拉取远端修改，Windows 修改也会提交回同一 history。
- 新建、编辑任务和项目，包括标题、备注、截止日期、标签、项目/区域归属。
- 收件箱、今天、日历、随时、某天、日志簿、垃圾桶和项目视图。
- 日历月份视图按日程日期和截止日期标记任务，点击日期可查看当天任务。
- 日历中可直接在选中日期新建任务；详情面板可修改任意日程日期。
- 按 `Ctrl+T` 可随时打开新建任务窗口，并自动放入“今天”。
- 完成、恢复、取消、移入垃圾桶、从垃圾桶恢复以及排序。
- 本地磁盘缓存、持久化离线写入队列、自动重试和冲突处理界面。
- 本机凭证使用 Windows DPAPI 保存；可选本地 PIN 二次验证。
- 附件保存在 Windows 本机，备注中仅同步附件标记文本。

## 尚未验证

真实 Things Cloud 账号写入和 iPhone 往返同步没有在本次交付中验证。当前
验证范围是本地 HTTP API、持久化队列、冲突策略和模拟 Things Cloud 的
双向提交测试。

因此，以下内容属于兼容性设计而非已经证明的互操作承诺：

- 本客户端按社区 Go SDK 的 Task7 规则生成完整创建报文。
- 客户端使用社区 SDK 已验证的 `ThingsMac/32209501` 兼容身份与 schema。
- 成功后同一 history 中的 macOS/iOS 客户端应能看到 Windows 修改。

尚未用真实账号验证的场景包括已有账号登录、Apple 账号安全提示、两步验证、
不同 Things/iOS 版本、重复任务和 Cloud 限流。建议用测试账号或可恢复备份
先行验证。

## 运行要求

- Windows 10/11 x64。
- Python 3.10 或更高版本（源码运行）。
- Microsoft Edge WebView2 Runtime（Windows 10/11 通常已安装）。
- 已启用的 Things Cloud 账号。

安装并启动：

```powershell
python -m pip install -r requirements-dev.txt
python main_window.py
```

也可以双击 `启动.bat`。若 `dist` 中存在 v3 打包文件，它会优先启动 EXE；
否则从 `main_window.py` 启动。

仅用浏览器调试后端：

```powershell
python app.py
```

后端默认只监听 `127.0.0.1:55234`。

## 构建 EXE

双击 `build_onefile.bat`，或执行：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name ThingsWindows_v3.0.3 `
  --add-data "index.html;." `
  --add-data "vue.global.prod.js;." `
  --add-data "tailwindcss.js;." `
  --collect-all webview `
  main_window.py
```

产物位于 `dist\ThingsWindows_v3.0.3.exe`。`build/`、`dist/`、日志和运行
数据不会提交到 Git。

## 测试

```powershell
python -m py_compile app.py things_cloud.py sync_engine.py main_window.py
python -m pytest -q
```

测试使用内存中的模拟 Things Cloud，不会访问真实账号。覆盖创建、编辑、
项目/区域移动、日程、截止日期、标签、完成/取消、垃圾桶、离线队列重启、
远端修改合并、同字段冲突、冲突重试/放弃和 API 静态资源。

## 同步行为与限制

- 写操作先进入磁盘 outbox，Cloud 成功后才从队列移除。
- 提交使用 `ancestor-index`；发生 409 时先拉取远端改动，再判断是否冲突。
- 本机与远端同时修改同一字段时不会静默覆盖；界面提供“重试本机”或
  “放弃本机”选择。
- 不同字段的并发修改会尽量合并，保留另一端未冲突内容。
- 为避免损坏 history，重复任务的修改目前会被拒绝。
- 永久删除未实现；当前只支持可恢复的垃圾桶。
- 附件本体不上传到 Things Cloud，只有备注中的附件标记会同步。
- 备注按 Things 纯文本对象写入，并使用 CRC32 校验。

详见 [架构与协议说明](docs/ARCHITECTURE.md)。

## 数据位置

应用级数据位于 `%APPDATA%\ThingsWindows\`，每个 Cloud history 的缓存位于
`%APPDATA%\ThingsWindows\accounts\<history-hash>\`：

- `history_items.json`：本地 Cloud 状态缓存。
- `known_head.json`：已确认的远端 head。
- `outbox.json`：等待提交的写操作。
- `conflicts.json`：持久化冲突记录。
- `sync_state.json`：最近同步状态。
- `credentials.dat`：DPAPI 加密的账号凭证，位于应用级目录。
- `pins.json`：界面置顶项，位于应用级目录。
- `attachments\`：本机附件，位于应用级目录。

日志写入 EXE/源码目录下的 `things_debug.log`，该文件已被 Git 忽略。

## 参考与许可

同步协议参考：

- [arthursoares/things-cloud-sdk](https://github.com/arthursoares/things-cloud-sdk)
- [evanpurkhiser/things3-cloud](https://github.com/evanpurkhiser/things3-cloud)

本项目保留社区源码的 MIT 许可与版权声明，见 [LICENSE](LICENSE)。Vue 和
Tailwind 浏览器构建文件也分别保留其 MIT 许可头。
