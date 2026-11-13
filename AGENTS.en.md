# AGENTS.en.md — Installation manual for AI assistants

[中文](AGENTS.md) | **English**

> This file is written for an **AI assistant**. The user will send you this repo's URL and ask you to help install it.
> Follow the steps given here, and **do not mix in generic tutorials from the web** — this project deliberately avoids
> the whole "install a driver / enable test signing" approach, and doing that will actually break things.

## 0. What this is

A small Windows tool that turns a Google TV / Chromecast Bluetooth remote into a "push-to-talk + hotkey" device.
Purely user mode: **no drivers, nothing written to system directories, no resident administrator**. ⚠ But **key mapping** (D-pad/OK/volume) needs **one** elevation: Frida has to inject into the Bluetooth HID driver host and register a temporary service; after one "One-click optimization" a highest-privilege scheduled task covers it and it never prompts again. **Voice needs no administrator at all** — never describe both features with a single blanket "no admin needed" claim.
The program starts a web console on `127.0.0.1:8787` (localhost only).

**Confirm three things with the user first** (missing any one of them means it cannot be installed):

1. Is the system **Windows 10/11 x64**?
2. Is the remote a **Google TV / Chromecast Bluetooth remote** (other remotes use different report formats)?
3. Which package will be used?
   - **`VibeMote_Setup.exe` (installer, ~203MB of data, easiest on a clean machine ← recommended)**:
     bundles the Python runtime (`runtime\`) **and** the official virtual-audio-cable files, so the target
     machine needs **no Python and no network**
   - `vibe-mote-offline-v1.zip` (~61MB, bundles Python) → unzip and run, but you install the cable yourself
   - `vibe-mote-v1.zip` (~900KB) → requires **Python 3.10+**; have the user install it from
     [python.org](https://www.python.org/downloads/), **be sure to check `Add python.exe to PATH`**,
     and do not use the Microsoft Store version

## 1. Installation

**Installer `VibeMote_Setup.exe` (recommended)**: double-click → click Next through the wizard → finish → **reboot once**.

| What the wizard does | What you must tell the user |
|---|---|
| Installs to `C:\Program Files\VibeMote 遥控器` (**one UAC prompt**, because writing to Program Files and installing an audio driver both need admin) | Click "Yes" on the UAC prompt — it is not the app grabbing privileges for no reason |
| Step 3 **launches VB-CABLE's own installer** (the bundle carries the 6 official Pack45 files, not one byte changed) | In that window **click Install Driver** and accept its licence; **you must reboot afterwards** (VB-Audio's requirement) — without the reboot voice will seem to have "no sound" |
| Creates Desktop / Start-menu shortcuts + an uninstall entry | The uninstall entry points at the bundled `runtime\pythonw.exe`, so no Python install is needed |

After installation, **on first launch go to Settings and click "Scan for remote"** — the package
**contains no Bluetooth address**; `config.json` ships with `remote_addr` empty.

> Why shipping only `.venv` does not work: a venv is just a shell. Its `pyvenv.cfg` points at the
> `C:\Python312` of the machine that created it, so on a clean machine it exits with code 103
> (see pits 22/23 in `01_踩坑与经验_总纲.md`). The wizard installs `runtime\`
> (official embeddable 3.12.9) instead, and `sys.prefix` is that directory itself.

**Offline bundle**: unzip → double-click `启动遥控器.vbs` → done (no other steps; install the cable from the UI checklist).

**Light package** (when the target machine has Python):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
# Optional: the D-pad/OK/volume need frida (~130MB) — ask the user first
.venv\Scripts\python.exe -m pip install -r requirements-keys.txt
```

If dependency installation hangs or times out (common in China), switch sources in this order and stop at the first success:

```powershell
$mirrors = @(
  "https://mirrors.cloud.tencent.com/pypi/simple",
  "https://mirrors.ustc.edu.cn/pypi/simple",
  "https://mirrors.aliyun.com/pypi/simple",
  "https://mirrors.huaweicloud.com/pypi/simple",
  "https://pypi.org/simple"
)
foreach ($m in $mirrors) {
  .venv\Scripts\python.exe -m pip install -r requirements.txt -i $m --timeout 30 --retries 2
  if ($LASTEXITCODE -eq 0) { break }
}
```

> Note: TUNA returns an empty index (`from versions: none`), so it is last;
> NetEase 163's PyPI mirror has stopped syncing, so **do not use it**.
> The "Install all" button in the UI does the above automatically and is usually less trouble than typing it by hand.

> ⚠ **Key mapping is optional — ask before installing it.** It needs frida, and frida injects a module into the system's
> Bluetooth HID driver host (one elevation, may be flagged/quarantined by AV, a few games with kernel anti-cheat may
> refuse to start). **If the user only wants voice, do not install it.**
> Two offline packages exist: `-voice` (~14MB, **no frida**) and the full one (~61MB, frida included).
> If they took `-voice` and later want key mapping, there are two routes:
> ① **online**: click "Settings → Diagnostics → Install key-mapping dependencies" (Chinese mirrors, ~47MB download);
> ② **offline**: have them drop `frida-*-win_amd64.whl` into the program folder's `wheels\` and click the same button
> (bundled wheels are preferred). Either way the first attach needs **one** administrator approval (or run "One-click
> optimization" first).

## 2. Verify the installation

```powershell
# 2.1 Launch (same as double-clicking 启动遥控器.vbs; --silent means don't open a browser)
#     Installer build: use the bundled runtime; offline bundle / dev folder: runtime first, .venv as fallback
.\runtime\pythonw.exe app.py --silent
# 2.2 Wait 5 seconds, then ask the API for status
Invoke-RestMethod http://127.0.0.1:8787/api/state | ConvertTo-Json -Depth 5
```

> To prove it truly does not depend on the system Python, run this in the install directory:
> `.\runtime\python.exe -c "import sys;print(sys.prefix, sys.base_prefix)"` —
> both should be the `runtime` folder inside the install directory, and `sys.path` must contain
> **no** `C:\Python*` entries.

Reading `/api/state`:

| Field | Expected | What to do if it is wrong |
|---|---|---|
| `running` | `true` | Call `/api/action {"action":"start"}`, or have the user click "Start" in the UI |
| `data_dir.writable` | `true` | **The program folder is not writable** (installed into `C:\Program Files` without an ACL grant) → saving settings always fails and voice never produces text. Have the user **re-run the installer as administrator** (since v1.3.1 the wizard runs `icacls` itself) |
| `deps.ok` | `true` | Dependencies are incomplete; check `deps.missing` |
| `deps.keys_ok` | `true` only if frida is installed | Not having frida installed is normal |
| `remote_addr` | an address | Empty = never scanned/saved → have the user go "Settings → Scan for remotes → click their remote → Save settings" |
| `voice.connected` | `true` | The remote is asleep → **have the user press any button on the remote** |
| `keys.ready` | `true` only if frida is installed | See above |

"Settings → **Environment check**" in the UI (or `POST /api/action {"action":"check"}`) lists everything item by item and is the least trouble.

Logs are also written to `client.log` in the program directory. If nothing happens after a double-click, **read that file first** —
on startup failure the launcher shows a prompt and opens it automatically.

## 3. Three things to tell the user after installation

0. **Scan for the remote and save it** (the `config.json` in the installer is a template with an empty `remote_addr`):
   "Settings → Scan for remotes" → click their remote → "Save settings".
   **Without this step voice never produces text**, while the buttons work fine — users report it as
   "voice doesn't work, everything else is OK".
   If saving reports "cannot write the config to the program folder", that is a permissions problem — see the first rows of §4.
1. **Launch**: double-click `启动遥控器.vbs` (no console window); the browser opens the console automatically
2. **Set the microphone in the input method editor to `CABLE Output (VB-Audio Virtual Cable)`** (otherwise voice input produces no text).
   ⚠ **Change it only there**: leave your real microphone as the system default recording device — if the default is the
   virtual cable, WeChat voice messages / Voice Recorder pick up that line too, and the "mic passthrough" feeds the cable's
   own output back into it (the input method editor then hears the voice plus a ~115 ms delayed copy, which clearly hurts accuracy)
3. **Qianwen IME**: the program handles this **automatically** on first launch (turns on "allow injection" and restarts the Qianwen process once),
   so the user needs to configure nothing. If the voice button still does nothing, have them click that button once on the "Voice" page.

To stop UAC from appearing: have the user click "Settings → **One-click optimization**" (the **last** UAC prompt appears,
after which autostart uses a highest-privilege scheduled task and no prompt appears at boot).

## 4. Troubleshooting

```
The first step is always: POST /api/action {"action":"check"} and see which layer it says is broken
```

| Log symptom | Real cause | Handling |
|---|---|---|
| `voice.note` = `还没填遥控器蓝牙地址（到设置页扫描一次）` ("no remote Bluetooth address yet") while the buttons all work | ① the installer's template has an empty `remote_addr` and the user never scanned; ② they scanned but **saving failed** (program folder not writable, see the next row) | Check `data_dir.writable` first: `false` → handle the next row; `true` → have the user go "Settings → Scan for remotes → click their remote → Save settings", then confirm `remote_addr` in `/api/state` is filled |
| Saving settings reports `配置写不进程序目录` ("cannot write the config to the program folder"), or `data_dir.writable=false`, or there is **no `client.log` in the program folder at all** | Installed into `C:\Program Files`, whose ACL gives `BUILTIN\Users` only RX (v1.3.0 granted nothing) → `config.json` cannot be written → the remote address is never stored → voice never produces text; `client.log` is swallowed silently too | Have the user **re-run the v1.3.1 installer as administrator** (the wizard runs `icacls /grant *S-1-5-32-545:(OI)(CI)M`). Manual first aid: run the same command in an administrator PowerShell. Since v1.3.1 the log falls back to `%LOCALAPPDATA%\VibeMote\client.log`, which states the reason |
| Constant `connection timeout` | Remote is asleep (it does not advertise while idle; it only wakes for a moment after a key press) | Press any button on the remote — the program **retries immediately whenever it sees a remote report**, and backs off to at most a 45-second retry interval while it keeps failing |
| `missing ATVV characteristic` + only 4 services enumerated | Another client is holding the Bluetooth channel | Close other clients (including older versions), then click "Restart" |
| `attach failed: PermissionDenied` | Frida's one-time elevation request was denied | Have the user click "One-click optimization", or retry once |
| Pressing the voice button produces no "start speaking" in the log | Bluetooth is not connected | Check `voice.connected` |
| `voice.connected` stays false and the log keeps printing `connection timeout`, **but the remote's buttons still work** | A BLE device accepts only one connection and Windows' HID stack holds that single link, so the remote stops advertising (`BleakScanner` cannot see it, while other BLE devices scan fine) | Have the user click "Settings → Diagnostics → **Reset Bluetooth radio**", or take the remote's battery out for 10 seconds. **Do not** touch pairing/drivers |
| User clicked "Reset Bluetooth radio", the UI said done, but `elevate_setup.log` holds **only one line** `=== 重置蓝牙电台 ===` | v1.3.1 and older: the elevated helper was killed by pipe + gbk (`✓`/`✗` raises `UnicodeEncodeError`), and the caller ignored the return code → silent failure | Upgrade to **v1.3.2** (helper forces UTF-8 stdout, `log()` wrapped in try/except, `run_reset_bt()` checks `rc`). First aid: from an elevated PowerShell, `cd` into the install dir and run `runtime\python.exe elevate_setup.py --reset-bt` |
| Reset log shows `需要重新启动系统才能完成配置操作!` / `禁用失败（继续）: 常规故障` | That Bluetooth adapter (e.g. MediaTek USB) cannot be restarted at runtime and `Disable-PnpDevice` is refused by the driver | **Ask the user to reboot the PC** (or pull the remote's battery for 10 s). There is no other software switch — stop retrying `pnputil`/`bthserv` |
| "start speaking" appears but the input method editor does nothing | The IME rejects program-injected keystrokes | Qianwen: enable "allow injection"; Doubao: unsupported, switch to another IME |
| frida is installed but `keys.ready` stays false / the log says frida is missing | **Antivirus silently quarantined frida** (it is ~47MB of the offline bundle; `frida-agent.dll` / `frida-helper-*.exe` are in several vendors' signature sets) | Have the user whitelist the program folder, then click "Install key-mapping dependencies". **Do not** tell them to disable Defender |
| `Attach failed: TransportError: the connection is closed` over and over (dozens to hundreds of times) while voice works perfectly | **Antivirus / EDR is blocking Frida's injection** — **by far the most common case on a corporate PC** (a real log had all 145 failures as exactly this error) | Have the user ask **IT to whitelist the program folder** (we never change system settings, disable Defender, or install drivers). **Never** suggest disabling antivirus, and never try to bypass corporate policy. Until it is allowed: **voice works, key mapping does not**. Ask them to click "Settings → Diagnostics → Key-mapping self-check" so the evidence lands in the log |
| The log keeps printing `GATT Protocol Error: Insufficient Authentication` (or repeated `missing ATVV characteristic`) | The remote's link was never properly **paired/encrypted** (a BLE peripheral insists on an encrypted link) — unrelated to antivirus or drivers | Have the user **Remove device** under Settings → Bluetooth & devices, hold 【**Back + Home**】 to pair again, then click Restart. **Do not** touch anything beyond the pairing record |
| No sound right after boot | Audio service / sound card became ready late | **Normal, wait 5–10 seconds** (the program retries every 5 seconds) |
| Installing the virtual audio cable reports `Unknown Error 1072` | The service is stuck in the "marked for deletion" state (an earlier leftover cleanup deleted the service without stopping the driver first). Its "run as administrator" hint is **misleading** | **Reboot once**, then install again; if it still fails, right-click `vbcable\VBCABLE_Setup_x64.exe` → "Run as administrator" → click Install Driver |
| Nothing happens after a double-click | Startup failed | Read `client.log` (the launcher also shows a prompt and opens it) |

**Do not do these**: do not suggest installing drivers, do not modify the IME configuration in the registry, and do not make the program
run as a resident administrator — this project does not need it and doing so causes problems.

## 5. Cleanup / uninstall (when the user asks)

"Settings → Cleanup / Uninstall" in the UI is a **per-item checklist** (nothing is checked by default); the command line works too:

```powershell
.\runtime\python.exe uninstall.py --items=venv,pycache      # delete only these two
.\runtime\python.exe uninstall.py --items=all               # delete everything (including config)
.\runtime\python.exe uninstall.py --dry-run                 # list only, change nothing
# In old packages / a dev folder without runtime\, replace .\runtime\python.exe with .venv\Scripts\python.exe
```

Available ids: `task, run, kill, frida_helper, frida_svc, venv, pycache, pythontxt, logs, config, qwen, cable`.
`task` / `frida_svc` / `cable` require administrator rights.

> **`cable` (uninstall VB-CABLE) is different from every other id** — tell the user before they tick it:
> it is a **system-wide audio driver** that OBS / voice changers may also be using; there is **no silent
> uninstall** (the program only launches the official uninstaller, and the user must click "Remove Driver"
> in its window); and VB-Audio requires a **reboot** afterwards. It is unticked by default and not part of
> the recommended set, and the program deliberately does not verify the result (it would report a false failure).

**Completely uninstall the whole program**: (1) run the "recommended cleanup" above once (disable autostart + remove Frida services + delete dependencies),
(2) then delete the entire program directory. The program is a purely portable deployment, so nothing else is left on the system.
The bundled `runtime\` is part of the program itself and is not in the cleanup list.

## 6. Environment

Windows 10/11 x64 · **the installer / offline bundle bundles Python 3.12.9** (only the light package
needs Python 3.10+ on the system) · voice requires the VB-CABLE virtual audio cable
(its own installer needs administrator rights once; unrelated to this program).

**How to install VB-CABLE**:
- **Installer `VibeMote_Setup.exe`**: step 3 of the wizard **launches VB-CABLE's own installer for you**;
- **Offline bundle / light package**: have the user click the **one-click install** button on the
  home-page checklist row (or on the "Settings → Virtual audio cable" card).

Two things to tell them up front:
- one UAC prompt appears (that is the audio driver installer's own requirement, unrelated to this
  program), and they still have to click **"Install Driver" in its own window** — the official
  installer **offers no silent-install switch** (no `-i` / `-s` / `/S`), so this step cannot be automated;
- the official readme states that **a reboot is required** for the installation to complete.

The program **starts this step for the user automatically on first launch** (it only auto-tries once —
if they decline the UAC it will not ask again). If the dependencies are not installed yet (first run of
the light package), that step does not trigger; use "Install all" in the UI instead (it includes the
virtual audio cable).
