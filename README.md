# WorkOS Suite

一个本机工作台，整合文件工作台、进程管理、千问录制、WorkOS、手机镜像、想法收集和个人记忆。七个组件的固定版本保存在 `components/`，工具目录列出19个公开项目及其入口。目录中的其他项目仅作参考，不作为已合并的运行引擎。

## 使用

从本仓库 **Releases** 下载 `WorkOS-Suite-1.0.4-windows-x64-complete.zip`，完整解压到任意本机文件夹，双击 **Start-Suite.cmd**。不需要先安装 Python、Node 或 npm。入口为 `http://127.0.0.1:18880/`，快捷方式可指向这个启动器。

包里包含 Python、Node、七个组件、完整文件工作台和进程管理器所需的 Tk / Tcl 与界面依赖、文件解析和交付依赖、ADB、scrcpy，以及记忆助手 Android 安装包。官方千问账号、模型账号、Office 和手机授权需要在新机器配置；个人项目、记忆和登录信息不会随安装包复制。使用 **Check-Environment.cmd** 查看哪些条件已经满足。

手机镜像使用 FFmpeg 的 LGPL 2.1 动态库；[对应源码 ZIP](https://github.com/jg8421/WorkOS-Suite-Public/releases/download/v1.0.4/WorkOS-Suite-1.0.4-corresponding-sources.zip) 与安装包在同一 Release 提供。[完整第三方许可与构建来源](docs/PUBLIC_THIRD_PARTY.md) 随包交付。

包含 PyMuPDF / MuPDF 1.28.2 的原生文件 GUI 组合依 GNU AGPLv3 分发；原有自有源码的 MIT 告知及第三方各自许可证均保留。AGPL 全文随二进制交付，该版本对应源码与构建说明在同一 Release 的源码资产提供。详见[原生界面依赖与许可](docs/NATIVE_GUI_THIRD_PARTY.md)。

默认本机模式下，已有健康 WorkOS 在 18866 运行时，套件会接入其原数据；退出套件不会关闭该服务。否则套件启动自己的独立 WorkOS。数据默认在 `%LOCALAPPDATA%\WorkOS-Suite`。明确指定 `--core-data-dir` 时使用选定的数据目录启动独立研究后台，不借用 18866；不要让两个后台同时使用同一个数据目录。

公网模式须明确配置固定 HTTPS 地址和已在本机初始化的密码账户，并由自己部署的隧道连接本机入口。未配置时继续只允许本机访问。登录后的远程页面使用同一台主机的数据，可研究、整理资料和保存想法；系统剪贴板、原应用/原版窗口、进程控制、手机控制、录音控制与主机配置仍只在本机提供。配置方式和限制见[部署及迁移](docs/DEPLOYMENT.md)。

新安装不会内置任何人的业务或网盘目录。在文件页登记自己的文件夹；若需将生成的交付稿保存到项目目录，在 WorkOS 中明确配置项目根目录。个人数据库、记忆、录音与登录信息单独保存在本机，不进入公开源码或通用安装包。

同一个 OneDrive 可用于共享源资料、交付文件和明确启用的 WorkOS JSON 镜像；镜像只支持备份及空库恢复，尚不支持多台 Suite 同时编辑后的自动合并。SQLite/WAL、登录会话和账号配置保留在本机。Suite 记忆库默认也是独立本机库，开启云收件箱并不自动配置 OneDrive 或手机账号。

文件页支持 Ctrl / Shift 多选、Ctrl+C / X / V、F2 重命名、Delete 移入回收区、Ctrl+Z 撤销删除、Alt+方向键导航和递归内容搜索。复制路径与复制文件分别提供；后者使用 Windows 文件剪贴板，可粘贴到资源管理器。「打开完整文件工作台」保留原版目录标签、收藏、图形预览和编辑功能；进程页也可打开完整原版窗口。

千问页会识别本机已运行的原版自动录音监测并沿用其应用、快捷键和归档设置，避免启动重复监测；退出套件不会关闭借用的原监测。首次安装须配置官方千问并主动启用。状态分别显示监听、暂停、录制及错误；发送快捷键成功与客户端实际开始录制分开呈现。

## 先试一条工作链

1. 在「工作 → WorkOS」新建项目。
2. 在「想法」写下一段判断，按 Enter 保存（Shift+Enter 换行），送到这个项目的笔记。
3. 在「文件」添加项目文件夹，预览 PDF / Excel / 文本，选择一份文件「送到项目」。
4. 回到 WorkOS，选择资料，研究、整理会议、起草 Memo 或建立回报模型；在同一对话中继续修订。
5. 在「记忆」主动保存工作偏好。只有主动开启相应来源，才导入 Codex / DSH 或捕获桌面；这些来源不会自动作为项目证据发送给模型。

[完整使用案例](docs/GUIDE.md) · [组件与边界](docs/COMPONENTS.md) · [部署及迁移](docs/DEPLOYMENT.md)

## 开发与发布

这是公开发行版。`components.lock.json` 记录组件及固定来源版本；`deployment/runtime-lock.json` 固定离线运行时和依赖校验。发行仓库只包含经过审查的软件源码和合成示例，不包含个人历史资料、运行配置或旧安装包。

源码开发：Python 3.11+ / Node 22.15+，安装 `requirements.txt`；完整导出可安装 `components/workos/requirements.txt` 和 openpyxl / python-pptx。运行 `python -B launch.py`。`python -m suite.server --isolated --empty-roots --data-dir <临时目录>` 可独立验收，不接入现有项目或默认资料目录。

每次修改：模块测试 → 真实 HTTP 工作链 → Chrome 桌面和手机宽度验收 → 推送并确认 CI → 清洁 Git 提交构建完整 ZIP → 包内 Python/Node 隔离启动验证 → 发布 ZIP 和 SHA256。测试只使用合成项目和自有子进程；不自动录音、修改历史项目或控制真实手机。

研究部分使用 WorkOS 自有任务、证据、对话和质量检查机制，可接入配置好的 DSH / WorkBuddy / OpenAI 兼容服务；套件层采用固定组件进程管理，并非把所有工具伪装成 DSH。没有可用模型、Excel、千问或设备时会显示具体配置入口，不能凭空保证相应功能已完成。

## 源码归属

七个原组件保留原有 README、许可证与版本记录。Python、Node、scrcpy、ADB 和第三方依赖在发行包中附带许可材料及逐文件 SHA256 清单。套件提供 Web 工作流和独立原版窗口入口；原版文件工作台的设置保存在用户 AppData，关闭套件不会结束有未保存编辑的原版窗口。
