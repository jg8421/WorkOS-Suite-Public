# 极速进程管家 v1.4.0

轻量 Windows 进程与功耗监控工具。单文件 Python（标准库 + tkinter），双击即用。

## 功能

- 分组查看进程，未响应置顶，可直接结束或重启
- CPU、内存、GPU、NPU、磁盘、网络、温度总览
- **Intel RAPL 实时功耗**：CPU Package、CPU 核心、DRAM（**不需要管理员权限**）
- **温度 / 散热页**：真实硬件温度按区域读取 + ACPI 热区独立参考 + 散热限制与降频原因
- 平均值、峰值、约 3 分钟滚动曲线，以及一条会自动适应的功耗参考线

## 真实读数与区域分类（v1.4.0）

- 温度与功耗按 CPU 封装、CPU 核心、GPU、主板 / 机身、存储及其他区域分开，明细保留来源与传感器身份。
- **CPU 温度只取 CPU 硬件传感器**，不再把 GPU / SSD 最高温或 ACPI 热区冒充 CPU 温度；ACPI 热区仅作未验证参考，随负载变化也不能证明它是 CPU。
- **CPU 封装功耗不是整机功耗**；核心功耗是封装的一部分，不能叠加。CPU 平台功率（RAPL PSYS）另设卡片，代表硬件平台域，不当成插座整机输入。整机功耗仅接受明确的系统总功率传感器，不把 CPU、GPU、DRAM 相加作为整机实测。
- 不支持的指标显示未提供；无效或断连读数清空，不钳位成 0、不保留旧值冒充实时。
- 真实温度需要支持本机处理器的 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) / OpenHardwareMonitor 运行并提供 WMI；硬件访问通常需要正常管理员权限。新处理器若稳定版未支持，应使用官方支持该处理器的版本，而不是估算温度。
- 机身没有统一的“整机温度”，应查看主板、机身或各部件传感器；没有系统功率计的机器不能凭软件获得真实插座整机功耗。

## 温度 / 散热（历史 v1.3.0）

顶部标签页「温度 / 散热」，命令行加 `--temp` 可直接打开。

- **卡片与明细**：CPU 硬件温度及类型、CPU 封装功耗、降频信息；GPU 及主板 / 机身温度按区域列入明细，整机功率卡片在功耗页。ACPI 热区独立列出。
- **ACPI 热区**：仅参考，不论读数是否静态，都不能据此认定为真实 CPU 温度。
- **真实 CPU 温度**：自动探测 `root\LibreHardwareMonitor` 与 `root\OpenHardwareMonitor`，按硬件身份选择 CPU 封装温度，缺少封装传感器时显示 CPU 核心最高值。
- 为什么需要这一步：部分笔记本固件只暴露**一个** ACPI 热区，而且读数**恒定**——CPU 封装功耗从 13 W 拉到 62 W，它始终是 27.9 °C。这不是程序读错，是硬件没提供；本程序会明确标注，而不是伪造一个会动的数字。

## 自动识别本机 CPU（v1.1.0 起，v1.2.0 继续加强）

不再写死某一个型号，换任何一台电脑都能用：

1. 先读注册表 `HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\ProcessorNameString`，失败再用 WMI `Win32_Processor` 兜底；
2. 内置对照表覆盖 Panther Lake / Lunar Lake / Arrow Lake / Meteor Lake / Raptor Lake / Alder Lake / Core Ultra-U / Ryzen 移动版（Ryzen AI Max、Ryzen AI 300、HS/H/U），以及桌面平台（Raptor Lake-K、Alder Lake-K、Ryzen 7000X / 5000X 等）的基础功耗（PL1）与最大睿频功耗（PL2 / MTP）；
3. 表里没有的型号按核心后缀（HX / H / P / U / V）估算；
4. 曲线参考线取**「查表值」与「本机实测峰值」中的较大者**：实测超过官方值时参考线自动上移，并标注为「实测峰值」；
5. 想手工指定？在脚本目录放一个 `power_limit.txt`，内容 `25 80`（基础 / 最大，单位 W），或只写一个数表示最大功耗。

功耗页头部会显示识别到的 CPU 型号，右下角显示功耗上限的依据来源。

## 运行

```bat
:: 依赖（只需一次）
pip install pywin32

:: 启动
::   双击 启动极速进程管家.cmd
::   或
pythonw process_manager.py
::   或直接打开功耗页
pythonw process_manager.py --power
::   或直接打开温度 / 散热页
pythonw process_manager.py --temp
```

- Python 3.10+（需要自带 tkinter 的官方发行版）；
- `pywin32` 用于读取 RAPL / WMI 功耗传感器，缺了不影响进程管理功能。
- 经典 LHM / OHM：可在安装目录的 `hardware_monitor_path.txt` 写硬件监控程序绝对路径；启动时无 WMI 提供程序则正常请求管理员启动一次，不改变进程管家快捷方式。
- 新版 LHM 若不再提供 WMI，可通过本地官方库传感器桥接读取。安装目录放 `hardware_sensor_bridge.json`，内容为 `{"script_path":"桥接脚本绝对路径.ps1","snapshot_path":"传感器快照绝对路径.json"}`；桥接程序正常请求管理员权限，取消不重试。快照必须带真实采样时间，超过 15 秒不再使用。此配置不进入 Git。桥接脚本 `sensor-bridge.ps1` 放到官方 LHM 二进制目录（与 `LibreHardwareMonitorLib.dll` 同目录），默认每秒生成 `sensor-snapshot.json`；正常管理员运行，不绕过执行策略。创建同目录 `sensor-bridge.stop` 可正常停止，重启前移除该停止标记。桥接不改系统限频、功率限制或快捷方式。

## 功耗读数说明

- 数据源：Windows 性能计数器 `Win32_PerfFormattedData_PowerMeterCounter_EnergyMeter`（Intel RAPL）；
- 传感器名按 `Package / PKG`、`PP0`、`DRAM` 关键字自动匹配，不同平台命名不一样也能适配；
- 非 Intel 平台或计数器 / 驱动未启用时，功耗页会给出明确提示，其余功能不受影响；
- 参考线只是「官方规格 + 实测峰值」的提示，不会对系统做任何限频或修改。

## 温度读数说明（v1.3.0）

- CPU / GPU 温度来自 LibreHardwareMonitor / OpenHardwareMonitor 的 WMI 硬件传感器，按所属硬件分类；`Win32_PerfFormattedData_Counters_ThermalZoneInformation` 仅列为 ACPI 热区参考；
- 传感器采样后台运行，结果经主线程队列更新界面，约每 3 秒一轮（另需硬件监控提供程序运行）；
- 不猜测、不外推，读不到就显示「未提供」，读取失败或过期不再保留旧实时读数。

## 回归测试

```bat
python -m unittest test_sensors -v
python -m py_compile process_manager.py
```

## 使用提示

- 建议从开始菜单 / 桌面快捷方式启动：从普通终端启动的 GUI 会继承终端的 DPI 感知级别，在 150% 缩放的屏幕上字会偏小。
- 把快捷方式固定到任务栏时，请从**开始菜单右键固定**；从运行中的窗口右键固定，Windows 只会记下 `pythonw.exe` 而丢掉脚本参数，点了会没反应。

## 更新日志

- **v1.4.0**
  - 修复 GPU / SSD 温度被当作 CPU 温度，硬件归属分类后分区展示温度与功耗；
  - ACPI 热区永远只作参考，不再因随负载变化自动判定为真实 CPU 温度；
  - 整机功耗单列，无系统功率传感器时明确未提供，不以部件加总冒充；
  - 后台结果经队列回主线程更新，传感器无效 / 断连时清除旧值；
  - 增加离线传感器回归测试。
- **v1.3.0**
  - 新增「温度 / 散热」标签页（`--temp`）：温度读数 / 可信度 / 封装功耗 / 降频原因四张卡片 + 传感器明细表（含 `% Passive Limit`、`Throttle Reasons`）；
  - **静态假传感器识别**：温度不随负载变化时标注为「静态 · 疑似假传感器」，顶栏显示 `(静态·不可信)`，不再把假值当真温度；
  - 自动探测 LibreHardwareMonitor / OpenHardwareMonitor，装了就读硬件真值并优先采用；
  - 修复：温度源从 4 次 `Get-Counter`（约 4.2 秒）改为一次 `Win32_PerfFormattedData_Counters_ThermalZoneInformation`（约 13 毫秒），避免采样命令超过 12 秒超时、判定卡在「判定中…」；
  - 修复：温度读数显示 `0.0 °C` 的二次换算 bug。
- **v1.2.0**
  - 机型适配继续加强：对照表补上桌面平台（Raptor Lake-K / Alder Lake-K / Ryzen 7000X / 5000X 等）；
  - 某个 RAPL 传感器（核心 / DRAM）读不到时，对应卡片显示 `—`，不再给出误导性的 `0.00 W`。
- **v1.1.0**
  - CPU 型号与功耗上限自动识别，去掉写死的 `258V / 37W`；
  - RAPL 传感器名动态匹配，兼容不同平台命名；
  - 参考线支持「实测峰值」自适应，并显示依据来源；
  - 新增 `--power` 启动参数，直接进入功耗页；
  - 窗口 / 任务栏图标使用自带 `极速进程管家.ico`。
- v1.0.0：首个版本。
