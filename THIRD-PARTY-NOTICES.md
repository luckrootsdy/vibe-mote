# 第三方组件声明

本项目的**自有代码**采用 MIT 许可（见 [`LICENSE`](LICENSE)）。
下列第三方组件由本项目**打包分发**（主要在 `vibe-mote-offline-v1.zip` 里），
它们各自的许可**独立于**本项目，且优先于本项目条款适用于这些组件本身。

| 组件 | 版本 | 许可 | 用途 |
|---|---|---|---|
| Python 运行时 | 3.12.9 | PSF License Agreement | 解释器（见下方「运行时」一节：目前由目标机自带，未随包） |
| [frida](https://frida.re/) | 17.24.3 | wxWindows Library Licence 3.1（LGPL-2.1 加例外） | 读取蓝牙 HID 报文，实现按键映射 |
| [bleak](https://github.com/hbldh/bleak) | 3.0.2 | MIT | 蓝牙 GATT：语音键状态 + 麦克风音频 |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) | 0.5.6 | MIT | 音频输入输出（PortAudio 绑定） |
| [cffi](https://cffi.readthedocs.io/) | 2.1.1 | MIT | sounddevice 的依赖 |
| [pycparser](https://github.com/eliben/pycparser) | 3.12 | BSD-3-Clause | cffi 的依赖 |
| [winrt-runtime](https://pypi.org/project/winrt-runtime/) | 4.1.0 | MIT | bleak 在 Windows 上的依赖 |
| [typing_extensions](https://pypi.org/project/typing-extensions/) | 4.16.0 | PSF-2.0 | 类型标注兼容层 |
| [VB-CABLE](https://vb-audio.com/Cable/)（安装器 + 驱动） | Driver Pack 45 | VB-Audio 自有许可（donationware，见下） | 虚拟声卡：把遥控器麦克风送进输入法 |

## 许可原文在哪

- 安装包：上述 Python 组件的各自 `LICENSE` / `METADATA` 随包分发，可在
  `program_files\.venv\Lib\site-packages\<组件>-<版本>.dist-info\` 下找到。
- **Python 解释器本体（PSF 许可原文）目前不随包**：`.venv` 只是一个"壳"，
  `pyvenv.cfg` 的 `home` 指向目标机上的 Python 安装目录，标准库和 `python3XX.dll`
  都在那边。**目标机没有 Python 3.12 时程序起不来**（详见 `01` 坑 22）。
  若要做成真正自包含，需把解释器（建议官方 embeddable 包）连同 PSF 许可原文
  `LICENSE.txt` 一起放进包里 —— 届时本节要同步更新。
- 轻包：依赖由 `pip` 从 PyPI 安装，许可文件在对应包目录里。
- **VB-CABLE**：许可原文是官方原文件 `vbcable\readme.txt`，
  也在 VB-Audio 官网 <https://vb-audio.com/Cable/> 与其安装器的许可页上。
  （**注意：`vbcable\` 目前并不在安装包里**，见下方「VB-CABLE 的特别说明」。）

## frida 的特别说明

frida 采用 wxWindows Library Licence 3.1（LGPL-2.1 附「静态链接例外」）。
本项目以**独立文件**形式分发它（未静态链接进自有程序），
使用者可以自行替换 `.venv\Lib\site-packages\frida\` 下的文件。

## VB-CABLE 的特别说明

**VB-CABLE**（VB-Audio Virtual Cable）是本项目语音功能所需的系统级虚拟声卡，
作者是 **Vincent Burel（VB-Audio Software）**，版权与许可归其所有，
本项目**不主张任何权利**。

- **现状（2026-11-12 实测，与本文早期版本不一致，特此更正）**：当前安装包
  `vibe-mote-main_Win64_Setup\program_files\` 里**没有** `vbcable\` 目录，
  状态接口报 `cable.files_ok = false`、缺 `vbMmeCable64_win10.inf` 与
  `vbaudio_cable64_win10.sys`。本机语音能用，是因为**之前手工装过** VB-CABLE
  （`installed: true / registered: true`）；**换一台干净电脑装完，语音会完全没反应，
  而按键正常**，最容易被误判成"软件坏了"。
- **计划做法（与下面许可条款一致）**：把官网原包里的 6 个原文件原样放进
  `program_files\vbcable\`（`VBCABLE_Setup_x64.exe`、`VBCABLE_ControlPanel.exe`、
  `vbMmeCable64_win10.inf`、`vbaudio_cable64_win10.sys`、`vbaudio_cable64_win10.cat`、
  `readme.txt`），**一个字节都不改**，由安装向导调用**它自己的 GUI**，用户自己点
  「Install Driver」并同意它的许可。仓库 **git 里不含**这些文件。
  在那之前，安装向导/界面只给官网下载指引（<https://vb-audio.com/Cable/>）。
- **官方许可（`vbcable\readme.txt` 原文要点）**：
  - VB-CABLE 是 **donationware**：允许**原样（AS IS）复制与分发**整个包，
    但**不允许在未经作者同意的情况下把 VB-CABLE 包集成进另一个软件的安装流程**；
    分发时应当注明来源 <https://www.vb-cable.com>，并说明它是 donationware。
  - 本项目只做两件事：把**原版文件原样**放进包里，以及**替你点一下它自己的安装器**
    （用的是它自己的 GUI，用户仍要自己点「Install Driver」并同意它的许可）。
    我们**没有**修改、重打包或静默安装它。若作者认为这仍属"集成"，
    请联系我们，我们会把 `vbcable\` 从包里去掉 —— 界面上的「安装虚拟声卡」
    会自动改为去官网下载（程序本身一直是这个兜底逻辑）。
  - 用着顺手的话，建议去 <https://vb-cable.com/> 给作者捐一份。
- 安装需要一次管理员权限，那是**它自己的安装器**要求的，与本项目无关。
- **官方要求：装完必须重启一次系统才算装完**（官方 readme 原文
  "To finalize installation, you must reboot your computer."）。
  卸载同理 —— 本程序的清理清单里那一项也只是把它的卸载器拉起来，
  用户仍需点它窗口里的「Remove Driver」，然后重启。
- 如果你要**再分发**本项目的 zip，请先自行确认 VB-Audio 的条款；
  不合规时请从包里删掉 `vbcable\` 目录。
