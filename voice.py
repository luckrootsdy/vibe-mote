# -*- coding: utf-8 -*-
"""语音通道：遥控器语音键 + 遥控器麦克风 → 虚拟声卡。

两条链路都在 ATVV 私有服务（ab5e0001）上，和普通按键走的 HID 完全不同：

    ab5e0002  控制特征（写）   —— 连上后发一次 GET_CAPS 让遥控器进录音态
    ab5e0003  音频特征（通知） —— 16kHz IMA ADPCM，128 字节一帧，无帧头
    ab5e0004  状态特征（通知） —— b[0]==0x04 按住语音键 / 0x00 松开

音频处理全部用纯 Python（不依赖 numpy）：128B ADPCM → 256 个 16k 样本 →
去直流 → 线性插值升到 48k → 增益 / AGC → 软限幅 → 虚拟声卡。
16k 解码 + 48k 输出一共才 6.4 万样本/秒，纯 Python 绰绰有余。

★ 铁律：遥控器的 BLE 通道同一时间只能有一个客户端。第二个客户端拿到的
  服务表只剩 4 个基础服务（1800/1801/180a/180f），ATVV 会整个消失 ——
  日志表现就是「缺少 ATVV 特征」。所以每次开新会话前必须先把上一条关干净。
"""
from __future__ import annotations

import asyncio
import ctypes
import math
import threading
import time

import i18n
import keys

ADDR_DEFAULT = ""

ATVV_CTL = "ab5e0002"
ATVV_AUDIO = "ab5e0003"
ATVV_STATUS = "ab5e0004"

SR_IN, SR_OUT = 16000, 48000

# 语音键手势的三个时间参数（毫秒）
VOICE_LONG_MS = 200       # 按住超过这么久 → 判「长按」= 按住说话
VOICE_DOUBLE_MS = 200     # 第一下松开后这么快之内再按一下 → 判「双击」
VOICE_TAP_DELAY_MS = 200  # 短按松开后等这么久确认不是双击，才发「单击」


def probe_mic(seconds=1.0):
    """读一秒**系统默认输入设备**，报出峰值 —— 给"声音输入不进去"这类问题定性。

    为什么要它：用户报"不管电脑麦克风阵列还是遥控器，电平都是 0"时，第一件要分清的是
    **系统这边本来就没声音**（隐私设置/设备静音/驱动卡死/硬件静音键）还是**我们没读到**。
    这里用和普通应用完全一样的方式（WASAPI 默认输入端点）读一秒，把原始峰值写进日志 ——
    连 0 都是 0 的话，就是系统侧的事，跟本程序无关。

    ⚠ 不用 sd.rec / numpy：本项目刻意不依赖 numpy（声音处理是纯 Python），
    所以走 RawInputStream + int16 字节，峰值用标准库 array 算。
    """
    import sounddevice as sd
    from array import array
    try:
        dev = sd.default.device[0]
    except Exception:
        dev = None
    name = i18n.L("（没有默认输入设备）", "(no default input device)")
    try:
        if dev is not None and int(dev) >= 0:
            name = str(sd.query_devices(int(dev))["name"])
    except Exception:
        pass
    frames = max(1, int(16000 * seconds))
    try:
        with sd.RawInputStream(device=dev, samplerate=16000, channels=1,
                               dtype="int16", blocksize=1600) as st:
            raw = bytes(st.read(frames)[0])
        a = array("h")
        a.frombytes(raw[:len(raw) - (len(raw) % 2)])
        peak = max((abs(v) for v in a), default=0)
    except Exception as e:
        return i18n.L(f"麦克风自检失败：{type(e).__name__}: {e}",
                      f"Microphone self-check failed: {type(e).__name__}: {e}")
    lvl = 100.0 * peak / 32768.0
    if lvl < 0.5:
        verdict = i18n.L(
            "→ 读数几乎是 0，说明**系统这边就没有声音进来**（跟本程序无关）。按顺序查："
            "① 设置 → 隐私和安全性 → 麦克风（允许访问 + 允许桌面应用）；"
            "② 该设备的「级别」有没有被拉到 0 或被静音；"
            "③ 笔记本的**麦克风静音键/指示灯**（硬件静音会让所有输入都是 0）；"
            "④ 重启一次电脑（音频驱动卡死时只有重启能救）；"
            "⑤ 关掉可能独占麦克风的其他程序（会议/变声/OBS）。",
            "→ Essentially 0, which means **no audio is reaching Windows at all** (nothing to do "
            "with this program). Check in order: (1) Settings → Privacy & security → Microphone "
            "(allow access + allow desktop apps); (2) that device's Level slider / mute; (3) a laptop's "
            "microphone-mute key or LED (hardware mute zeroes every input); (4) reboot (a stuck audio "
            "driver only recovers on reboot); (5) close programs that may hold the mic exclusively "
            "(meetings / voice changers / OBS).")
    else:
        verdict = i18n.L("→ 电平正常（对着麦克风说话时应有明显读数）。",
                         "→ Level looks normal (talking into the mic should read clearly).")
    return i18n.L(
        f"麦克风自检：默认输入设备「{name}」，{seconds:.0f} 秒内峰值 {lvl:.1f}%"
        f"（原始 {peak}/32768）。{verdict}"
        + _cable_naming_hint(),
        f"Microphone self-check: default input “{name}”, peak over {seconds:.0f}s is {lvl:.1f}% "
        f"(raw {peak}/32768). {verdict}"
        + _cable_naming_hint(en=True))


def _dev_name(idx):
    """设备索引 → 名字（find_cable 返回的是索引；日志给用户看要用名字）。"""
    try:
        import sounddevice as sd
        return str(sd.query_devices(int(idx))["name"])
    except Exception:
        return ""


def _cable_naming_hint(en=False):
    """把 VB-CABLE 那对"反直觉"的名字说清楚 —— 每个用户都会问一次。"""
    try:
        cable_in, cable_out = find_cable()
    except Exception:
        cable_in = cable_out = None
    n_in = _dev_name(cable_in) or "CABLE Input"
    n_out = _dev_name(cable_out) or "CABLE Output"
    if not (cable_in is not None or cable_out is not None):
        return ""
    if en:
        return (f" Note: VB-CABLE names its ends from the cable's point of view — "
                f"「{n_in}」 shows up under **Playback** (programs write into it) "
                f"and 「{n_out}」 shows up under **Recording** (programs read from it). "
                f"So the IME must pick the Output end: this program writes into the Input end.")
    return (f" 注：VB-CABLE 的名字是「从线缆角度」起的 —— "
            f"「{n_in}」出现在**播放设备**里（程序往里写），"
            f"「{n_out}」出现在**录制设备**里（程序从它读）。"
            f"所以输入法要选的是 **Output** 那一个：本程序往 Input 那一端写。")


UNSTICK_COOLDOWN = 600.0        # 自动解卡最短间隔（秒）：别把用户蓝牙反复关开


async def unstick_bluetooth():
    """把蓝牙电台关开一次 —— 拆掉"被系统 HID 攥住"的那条唯一 BLE 连接。

    为什么用 Windows 的 Radio API（而不是 PnP）：
      ① 它跟「设置 → 蓝牙」那个开关是**同一个机制**，实测**不需要管理员**；
      ② PnP 那条路在真机上根本走不通（`Disable-PnpDevice` 对某些 Intel 电台直接报
         "不支持" HRESULT 0x8004100c，`pnputil /restart-device` 又要求重启系统）；
      ③ 它能**读回状态** —— 所以可以确认"确实开回来了"，绝不会把电台丢在关闭状态
         （这正是用户最怕的事："我的蓝牙设备全没了"）。

    返回 (结果, 说明)：restarted / turned-on / unavailable / failed
    """
    try:
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState
    except Exception as e:
        return "unavailable", i18n.L(f"没有 Radio API（{e!r}）",
                                     f"the Radio API is unavailable ({e!r})")
    radio = None
    try:
        for r in await Radio.get_radios_async():
            if r.kind == RadioKind.BLUETOOTH:
                radio = r
                break
    except Exception as e:
        return "unavailable", i18n.L(f"读蓝牙电台失败：{type(e).__name__}: {e}",
                                     f"could not read the Bluetooth radio: "
                                     f"{type(e).__name__}: {e}")
    if radio is None:
        return "unavailable", i18n.L("系统里没有蓝牙电台", "no Bluetooth radio on this system")
    if radio.state != RadioState.ON:
        # 本来就关着：只打开，不再去关它（少一个动作少一份风险）
        for _ in range(3):
            try:
                await radio.set_state_async(RadioState.ON)
            except Exception:
                pass
            await asyncio.sleep(1.5)
            if radio.state == RadioState.ON:
                return "turned-on", i18n.L("电台原本是关的，已打开", "it was off; turned it on")
        return "failed", i18n.L(f"打不开蓝牙（当前状态 {radio.state}）",
                                f"could not turn Bluetooth on (state {radio.state})")
    try:
        await radio.set_state_async(RadioState.OFF)
    except Exception as e:
        return "failed", i18n.L(f"关闭蓝牙失败：{type(e).__name__}: {e}",
                                f"could not turn Bluetooth off: {type(e).__name__}: {e}")
    await asyncio.sleep(3.0)
    for attempt in range(1, 4):
        try:
            await radio.set_state_async(RadioState.ON)
        except Exception:
            pass
        await asyncio.sleep(2.0)
        if radio.state == RadioState.ON:
            return "restarted", i18n.L(f"第 {attempt} 次确认已开回",
                                       f"confirmed back on (attempt {attempt})")
    return "failed", i18n.L(f"关开之后没能开回来（当前状态 {radio.state}）—— "
                            f"请立刻到「设置 → 蓝牙」手动打开",
                            f"it did not come back on (state {radio.state}) — "
                            f"turn Bluetooth back on in Settings right away")


def pairing_hint(name, text):
    """把蓝牙那几句"天书"翻译成该干什么。

    真机见过两次（别人机器上）：
      · `BleakGATTProtocolError: (5, 'GATT Protocol Error: Insufficient Authentication')`
        —— 遥控器的 ATT 服务要求一条**已加密**的链路，而 Windows 这边没拿那对密钥，
        **几乎都是配对没配好**：最有效的是"删掉设备重新配对"，不是重启程序、更不是动驱动。
      · 连接直接被拒/找不到设备 —— 系统里根本没配好这台设备。
    返回空串表示"这句我认不出来，不瞎猜"。
    """
    t = f"{name} {text}".lower()
    if "insufficient authentication" in t or "insufficient_authentication" in t:
        return i18n.L(
            "语音：遥控器要求一条已加密的链路（GATT Insufficient Authentication）—— "
            "通常是配对没配好。请到「设置 → 蓝牙和其他设备」把遥控器**删除设备**，"
            "然后按住遥控器的【返回 + 主页】重新配对，回来再点「重启」。"
            "（不用动驱动、不用重装程序）",
            "Voice: the remote requires an encrypted link (GATT Insufficient Authentication) — "
            "this is almost always an incomplete pairing. Remove the device under "
            "Settings → Bluetooth & devices, then hold 【Back + Home】 on the remote to pair it "
            "again, and click Restart afterwards. (No drivers, no reinstall.)")
    if "not paired" in t or "unreachable" in t or "not found" in t or "device not found" in t:
        return i18n.L(
            "语音：系统里找不到这台设备 —— 先在「设置 → 蓝牙和其他设备」把它配上，再点「重启」。",
            "Voice: the device was not found in Windows — pair it under "
            "Settings → Bluetooth & devices first, then click Restart.")
    return ""
FRAME_BYTES = 128
# 高通截止频率：人声主体在 300–3400 Hz，男声基频最低 ~85 Hz，
# 所以 100 Hz 既安全又够用（ASR 前端一般 80–100 Hz 起）。
# 实测不切的时候 0–50 Hz 占了 7.4% 的能量、还有 ±600 的直流漂移在带偏 AGC。
HP_FC = 100.0

STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24686, 27086, 29794,
    32767,
]
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


# ============================================================ 音频元件
class ImaDecoder:
    """跨帧连续的 IMA/DVI ADPCM 解码器，输出 int16 列表。

    ATVV 是**高 nibble 先**（与 IMA 标准相反）。写反了会让
    直流偏移到 43.8% 满量程 —— 这是判断 nibble 顺序的决定性证据。
    """

    def __init__(self):
        self.predictor = 0
        self.index = 0

    def reset(self):
        self.predictor = 0
        self.index = 0

    def decode(self, data: bytes) -> list:
        pred, idx = self.predictor, self.index
        out = []
        for byte in data:
            for nib in (byte >> 4, byte & 0x0F):
                step = STEP_TABLE[idx]
                diff = step >> 3
                if nib & 1:
                    diff += step >> 2
                if nib & 2:
                    diff += step >> 1
                if nib & 4:
                    diff += step
                pred = pred - diff if nib & 8 else pred + diff
                if pred > 32767:
                    pred = 32767
                elif pred < -32768:
                    pred = -32768
                idx += INDEX_TABLE[nib]
                if idx < 0:
                    idx = 0
                elif idx > 88:
                    idx = 88
                out.append(pred)
        self.predictor, self.index = pred, idx
        return out


class DcBlocker:
    """一阶高通，抹掉遥控器麦克风的直流偏置。y[n]=x[n]-x[n-1]+a*y[n-1]"""

    def __init__(self, a=0.995):
        self.a = a
        self.x1 = 0.0
        self.y1 = 0.0

    def process(self, x):
        out = []
        for v in x:
            y = v - self.x1 + self.a * self.y1
            self.x1 = v
            self.y1 = y
            out.append(y)
        return out


class HighPass:
    r"""二阶高通（RBJ biquad）：把**不属于人声的低频**整段拿掉。

    为什么需要（实测数据）：把虚拟声卡线上的语音拿去算频谱，得到
        0–20 Hz 3.4% / 20–50 Hz 4.0% / 50–100 Hz 10.5% / 100–200 Hz 17.2%
    而对照（同一套分析跑标准语音）0–50 Hz 只有 1.1%。
    更要命的是**每 170 ms 的直流均值在 ±600 之间漂移**（满量程 32768）——
    那是 IMA ADPCM 在低码率下步长自适应慢留下的**缓慢漂移**，不是人声。

    它有两个直接危害：
      · 占掉了 AGC 的动态范围：AGC 盯的是**峰值**，而峰值被这个漂移/隆隆声顶着，
        于是增益被压低，真正的人声（300–3400 Hz）反而录得偏小；
      · 识别器看的是语音特征，这些低频纯属噪声。

    人声主体在 300–3400 Hz，男声基频最低到 ~85 Hz，所以切在 100 Hz 既安全又够用
    （电话带宽的标准做法是 300 Hz 起，ASR 前端一般 80–100 Hz 起）。
    """

    def __init__(self, fc=100.0, sr=16000.0, q=0.707):
        w0 = 2.0 * math.pi * fc / sr
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2.0 * q)
        b0, b1, b2 = (1.0 + cw) / 2.0, -(1.0 + cw), (1.0 + cw) / 2.0
        a0, a1, a2 = 1.0 + alpha, -2.0 * cw, 1.0 - alpha
        self.b0, self.b1, self.b2 = b0 / a0, b1 / a0, b2 / a0
        self.a1, self.a2 = a1 / a0, a2 / a0
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def process(self, x):
        out = []
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        for v in x:
            y = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, v
            y2, y1 = y1, y
            out.append(y)
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return out

    def reset(self):
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0


class Resampler3x:
    """16k → 48k：整数倍线性插值，跨块保持相位。"""

    def __init__(self, factor=3):
        self.f = factor
        self.prev = 0.0

    def process(self, x):
        if not x:
            return []
        out = []
        prev = self.prev
        f = self.f
        for v in x:
            step = (v - prev) / f
            cur = prev
            for _ in range(f):
                cur += step
                out.append(cur)
            prev = v
        self.prev = prev
        return out


class Agc:
    """极简自动增益：跟踪近段峰值，向目标电平靠拢（带上下限）。"""

    def __init__(self, target=0.35, lo=1.0, hi=12.0, decay=0.995):
        self.target, self.lo, self.hi, self.decay = target, lo, hi, decay
        self.peak = 0.0
        self.gain = 1.0
        self.blocks = 0

    def process(self, x):
        peak = 0.0
        for v in x:
            a = v if v >= 0 else -v
            if a > peak:
                peak = a
        self.peak = max(peak, self.peak * self.decay)
        if self.blocks % 20 == 0 and self.peak > 1e-4:
            want = self.target / self.peak
            self.gain = max(self.lo, min(self.hi, want))
        self.blocks += 1
        g = self.gain
        return [v * g for v in x]


class Sink:
    """把 PCM 写进虚拟声卡的输出端点（CABLE Input）。

    用 RawOutputStream + 阻塞写：不需要 numpy，也不需要处理回调缓冲区类型。
    待播缓冲刻意留小（~200ms）—— 采集和播放是两个独立时钟，
    速率微差会让积压一路涨，延迟就变成「听到几百毫秒前的声音」。
    """

    def __init__(self, device, gain=4.0, agc=True, soft_k=0.9, on_log=None):
        self.device = device
        self.gain = gain
        self.use_agc = agc
        self.soft_k = soft_k
        self.on_log = on_log or (lambda m: None)
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.max_bytes = int(SR_OUT * 0.2) * 4      # 200ms 立体声 int16
        self.stream = None
        self.thread = None
        self.run_flag = threading.Event()
        self.alive = False                  # 出口是否可用（看护任务据此重建）
        self.decoder = ImaDecoder()
        self.dc = DcBlocker()
        self.hp = HighPass(HP_FC, SR_IN)     # ★ 去掉低频漂移/隆隆声，让 AGC 盯着人声
        self.rs = Resampler3x()
        self.agc = Agc()
        self.frames = 0
        self.underruns = 0

    def start(self):
        import sounddevice as sd
        self.stream = sd.RawOutputStream(device=self.device, samplerate=SR_OUT,
                                         channels=2, dtype="int16",
                                         blocksize=int(SR_OUT * 0.02))
        self.stream.start()
        self.run_flag.set()
        self.alive = True
        self.thread = threading.Thread(target=self._pump, daemon=True, name="audio-out")
        self.thread.start()

    def _pump(self):
        chunk = int(SR_OUT * 0.02) * 4              # 20ms 立体声 int16
        silent = b"\x00" * chunk
        fails = 0
        while self.run_flag.is_set():
            with self.lock:
                if len(self.buf) >= chunk:
                    data = bytes(self.buf[:chunk])
                    del self.buf[:chunk]
                else:
                    data = silent
                    self.underruns += 1
            try:
                self.stream.write(data)
                fails = 0
            except Exception:
                # 连续半秒都写不进去 = 设备被拔了/音频服务重启了 → 判掉线，
                # 让看护任务重建一条（开机时尤其常见）。
                fails += 1
                if fails > 25:
                    self.alive = False
                    return
                time.sleep(0.02)

    def feed_adpcm(self, frame: bytes):
        if not self.alive:
            return
        self.frames += 1
        pcm = self.decoder.decode(frame)
        x = self.dc.process(pcm)
        x = self.hp.process(x)                      # 高通：低频漂移在这里被拿掉
        x = self.rs.process(x)                      # -> 48k float
        if self.use_agc:
            x = self.agc.process([v / 32768.0 for v in x])
        else:
            x = [v / 32768.0 * self.gain for v in x]
        k = self.soft_k
        out = bytearray()
        for v in x:
            s = int(k * math.tanh(v / k) * 32767.0)
            if s > 32767:
                s = 32767
            elif s < -32768:
                s = -32768
            out += (s & 0xFFFF).to_bytes(2, "little") * 2      # 左右同相
        with self.lock:
            self.buf += out
            if len(self.buf) > self.max_bytes:                 # 超了丢最旧的
                del self.buf[:len(self.buf) - self.max_bytes]

    def reset(self):
        with self.lock:
            self.buf.clear()
        self.decoder.reset()
        self.dc.x1 = self.dc.y1 = 0.0
        self.hp.reset()
        self.rs.prev = 0.0

    def stop(self):
        self.alive = False
        self.run_flag.clear()
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class MicRelay:
    """系统默认麦克风 → 同一个 CABLE Input 的实时直通。

    为什么需要：输入法把麦克风**钉死在配置里**（千问的 deviceId 且
    isDefault=false），改系统默认录音设备对它无效；而遥控器音频必须经
    虚拟声卡才能被输入法读到。于是让 CABLE 这一路在「按住键盘语音快捷键」
    时永远有声音 —— 日常用键盘说话照旧，其他应用仍走真实麦克风，互不影响。
    """

    def __init__(self, sink, on_log=None):
        self.sink = sink
        self.on_log = on_log or (lambda m: None)
        self.stream = None
        self.thread = None
        self.run_flag = threading.Event()
        self.active_holder = False          # 由 _alt_watch 置位
        self.device = None
        self.name = "?"
        self.sr = SR_OUT
        self._prev = 0.0
        self._t = 0.0
        self.frames = 0

    @staticmethod
    def _usable(d):
        """这个录音端点能不能当"直通源"。

        ★ 虚拟声卡自己的端点绝对不行：机器上很常见的一种配置是把系统默认
          录音设备就设成 `CABLE Output`（为了让输入法/微信听到遥控器）。
          这时候如果直通还去读"默认录音设备"，就等于**把虚拟声卡的输出
          再灌回它自己的输入** —— 自己喂自己。遥控器的声音会在声卡里
          叠上一层延迟副本（听起来发闷、像有混响），输入法的识别率会
          明显下降。所以这里必须把虚拟声卡/回环端点排除掉。
        """
        if d["max_input_channels"] <= 0:
            return False
        raw = d["name"]
        n = raw.lower()
        if _is_vb_audio(n) or "cable" in n:
            return False
        if "voicemeeter" in n or "point" in n:
            return False
        # 立体声混音 / What U Hear 这类是"回放的回环"，同样会自己喂自己
        if "stereo mix" in n or "what u hear" in n or "立体声混音" in raw:
            return False
        return True

    def probe(self):
        """挑一个**真实**麦克风当直通源（默认设备优先，但绝不选虚拟声卡）。"""
        import sounddevice as sd
        devs, hosts = sd.query_devices(), sd.query_hostapis()
        wasapi = [h for h in hosts if "wasapi" in h["name"].lower()]
        order, seen = [], set()

        def push(i):
            if i is None or i < 0 or i in seen or i >= len(devs):
                return
            seen.add(i)
            if self._usable(devs[i]):
                order.append(i)

        for h in wasapi:                       # 1) WASAPI 的默认录音设备（用户自己选的那个）
            push(h.get("default_input_device"))
        for i in range(len(devs)):             # 2) 其余 WASAPI 录音端点
            try:
                is_wasapi = "wasapi" in hosts[devs[i]["hostapi"]]["name"].lower()
            except Exception:
                is_wasapi = False
            if is_wasapi:
                push(i)
        for i in range(len(devs)):             # 3) 兜底：别的 host API 也行（延迟差一点，总比没有好）
            push(i)

        if not order:
            return False
        i = order[0]
        self.device = i
        self.name = devs[i]["name"]
        self.sr = int(devs[i].get("default_samplerate") or 0) or SR_OUT
        return True

    def start(self):
        import sounddevice as sd
        self.stream = sd.RawInputStream(device=self.device, samplerate=self.sr,
                                        channels=1, dtype="int16",
                                        blocksize=int(self.sr * 0.02))
        self.stream.start()
        self.run_flag.set()
        self.thread = threading.Thread(target=self._pump, daemon=True, name="mic-relay")
        self.thread.start()

    def _pump(self):
        step = self.sr / float(SR_OUT)
        while self.run_flag.is_set():
            try:
                data, _overflow = self.stream.read(int(self.sr * 0.02))
            except Exception:
                time.sleep(0.05)
                continue
            if not self.active_holder:
                self._t = 0.0
                continue
            samples = memoryview(data).cast("h")
            out = bytearray()
            pos = self._t
            n = len(samples)
            while pos < n - 1:
                i = int(pos)
                f = pos - i
                v = samples[i] * (1 - f) + samples[i + 1] * f
                s = int(v)
                out += (s & 0xFFFF).to_bytes(2, "little") * 2
                pos += step
            self._t = pos - n
            self._prev = samples[-1] if n else self._prev
            self.frames += 1
            with self.sink.lock:
                self.sink.buf += out
                if len(self.sink.buf) > self.sink.max_bytes:
                    del self.sink.buf[:len(self.sink.buf) - self.sink.max_bytes]

    def stop(self):
        self.run_flag.clear()
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


def _is_vb_audio(name):
    n = name.lower()
    return ("vb-audio" in n or "vbaudio" in n or "vb audio" in n)


def _score_render(idx, dev, apis):
    """给"可能是那根线的输出端"打分，分高者优先。

    ⚠ 优先顺序很重要：VB-CABLE 会建**两个**渲染端点 ——
      · "CABLE Input"（2 声道）这是规范的那一个，输入法读的 "CABLE Output" 就是它的对面；
      · "CABLE In 16 Ch"（16 声道）是另一个端点，不是输入法那一侧。
    所以必须让 2 声道那个赢：只按"CABLE In"给高分，会选中 16 声道那路
    —— 而 Windows 把规范端点改名成「扬声器 (2- VB-Audio Virtual Cable)」之后，
    "扬声器"这个名字一个关键字都不含，很容易被 16 声道那路挤掉。
    """
    n = dev["name"].lower()
    if dev["max_output_channels"] <= 0:
        return None
    if not _is_vb_audio(n):
        return None
    # 别家的产品：Voicemeeter 的 "Point" 系列也带 VB-Audio 的名字，但它不是我们要的线
    if "point" in n or "voicemeeter" in n:
        return None
    score = 0
    if "cable input" in n:
        score += 100            # 规范名，最好
    elif "16 ch" in n or "16ch" in n:
        score -= 40             # 16 声道那路：不是输入法读的那一侧
    else:
        score += 25             # 其他 VB-Audio 渲染端点（被改名成"扬声器"时就是这种）
    try:
        if apis[dev["hostapi"]]["name"].lower().startswith("windows wasapi"):
            score += 20         # WASAPI：延迟和质量都更好
    except Exception:
        pass
    return score


def find_cable():
    """找 VB-CABLE 的两端：输出=我们往里写的那一侧，输入=输入法读的那一侧。

    ⚠ **不能只认 "cable input" / "cable output" 这两个字样。**
    驱动被 PnP 重装（卸载后重启，Windows 从 DriverStore 里又把它装回来）之后，
    Windows 会把**渲染端点**改名成「扬声器 (2- VB-Audio Virtual Cable)」——
    名字里根本没有 "cable input"。后果是语音桥接找不到输出端点、完全没有声音，
    而设备在设备管理器里明明是好的。

    所以分两步：
      ① 先按规范名找 —— 绝大多数机器上就是它，行为完全不变；
      ② 找不到再按**厂商名**兜底：名字里有 vb-audio、有输出通道、且不是
         Voicemeeter 的 "Point" 那一侧；优先规范名 > 非 16 声道 > WASAPI。
    """
    import sounddevice as sd
    devs = sd.query_devices()
    apis = sd.query_hostapis()
    out_idx = in_idx = None
    # ① 规范名（保持原行为）。输入端要排除 Voicemeeter 的 "Point" 那一侧 ——
    #    它的名字里也带 "cable output"，但那是**另一个产品**的线，
    #    报给用户看会让人以为"输入法该选它"。
    for i, d in enumerate(devs):
        name = d["name"].lower()
        if "cable input" in name and d["max_output_channels"] > 0:
            out_idx = i
        if ("cable output" in name and d["max_input_channels"] > 0
                and "point" not in name and "voicemeeter" not in name):
            in_idx = i
    # ② 兜底：按厂商名找输出端
    if out_idx is None:
        best = None
        for i, d in enumerate(devs):
            s = _score_render(i, d, apis)
            if s is None:
                continue
            if best is None or s > best[0]:
                best = (s, i)
        if best is not None:
            out_idx = best[1]
    # ② 兜底：输入端（"CABLE Output ..." 通常不会被改名，但一并兜住）
    if in_idx is None:
        for i, d in enumerate(devs):
            n = d["name"].lower()
            if d["max_input_channels"] > 0 and _is_vb_audio(n) \
                    and "point" not in n and "voicemeeter" not in n:
                if in_idx is None or "cable output" in n:
                    in_idx = i
    return out_idx, in_idx


def cable_device_names():
    """给"状态显示"用：先给**我们真正会写入的那个端点**，再补输入端。

    app.py 的 cable_state() 靠它判断"声卡到底能不能用"，所以这里必须走
    find_cable() 那套（含兜底）逻辑 —— 两边判断不一致的话，就会出现
    "体检说没找到、清单说已装"这种自相矛盾的诊断。
    """
    try:
        import sounddevice as sd
        devs = sd.query_devices()
    except Exception:
        return []
    out_idx, in_idx = find_cable()
    names = []
    for i in (out_idx, in_idx):
        if i is not None:
            names.append(devs[i]["name"])
    if names:
        return names
    # 真的一个可用端点都没有：把名字里带 cable 的都列出来（至少让用户看到"有东西在"）
    return [d["name"] for d in devs if "cable" in d["name"].lower()]


# ============================================================ 语音键
def spec_to_vk(spec):
    """{'mode','key','mods'} -> (mode, [修饰 VK], 主键 VK)；认不出返回 None。"""
    key = keys.vk_of(spec.get("key"))
    if not key:
        return None
    mods = [v for v in (keys.vk_of(m) for m in (spec.get("mods") or [])) if v]
    return (spec.get("mode") or "hold"), mods, key


class VoiceKey:
    """把「语音键按下/松开」变成键盘动作。

    hold（按住）：先按修饰键再按主键，松开时反序抬起 —— 输入法的
                  「按住说话」要的就是这个。
    tap（按一下）：给 Windows 语音输入（Win+H）那种开关式快捷键。
    """

    def __init__(self, spec):
        self.spec = spec or {}
        parsed = spec_to_vk(self.spec)
        self.ok = parsed is not None
        self.mode, self.mods, self.key = parsed or ("hold", [], 0)
        self.down = False

    @property
    def label(self):
        names = [m for m in (self.spec.get("mods") or [])]
        return "+".join(names + [str(self.spec.get("key") or "")])

    def press(self):
        if not self.ok or self.down:
            return
        self.down = True
        for m in self.mods:
            keys.key_down(m)
        keys.key_down(self.key)

    def release(self):
        if not self.ok or not self.down:
            return
        self.down = False
        keys.key_up(self.key)
        for m in reversed(self.mods):
            keys.key_up(m)

    def tap(self):
        if not self.ok:
            return
        for m in self.mods:
            keys.key_down(m)
        keys.key_down(self.key)
        keys.key_up(self.key)
        for m in reversed(self.mods):
            keys.key_up(m)

    def clear_stuck(self):
        """启动时补一次抬起：上次进程在「按住」期间被杀，键会一直处于按下态。"""
        if not self.ok:
            return False
        vks = self.mods + [self.key]
        if not any(keys.key_is_down(v) for v in vks):
            return False
        keys.key_up(self.key)
        for m in reversed(self.mods):
            keys.key_up(m)
        return True


# ============================================================ 语音角色
class VoiceRole(threading.Thread):
    """独立线程里跑一个 asyncio 事件循环，保持 ATVV 长连接。"""

    def __init__(self, on_event, addr=ADDR_DEFAULT, audio=None, voice_spec=None,
                 relay=True):
        super().__init__(daemon=True, name="VoiceRole")
        self.on_event = on_event
        self.addr = addr
        cfg = dict(audio or {})
        self.gain = float(cfg.get("gain", 4.0))
        self.agc = bool(cfg.get("agc", True))
        self.want_relay = bool(relay)
        self.voice_spec = voice_spec or {"mode": "hold", "key": "RALT", "mods": []}
        self.voice = VoiceKey(self.voice_spec)
        self.stop_flag = threading.Event()
        self._poke = threading.Event()      # 遥控器有按键活动 → 立刻重试连接
        self.sink = None
        self.relay_obj = None
        self.connected = False
        self.note = i18n.L("未启动", "Not started")
        self.sessions = 0
        self.frames = 0
        self.misses = 0
        self._session_frames = 0
        self._active = False
        self._cable_warned = False
        self._relay_warned = False
        self._audio_task = None
        self._watch = None
        self._client = None
        self._loop = None
        # 所有"还没结束"的连接任务。超时的 connect 不能取消（WinRT 的 COM
        # IAsyncAction 取消会卡死 30s+），它会在后台继续尝试并可能迟到成功。
        # 必须记住它，等它收尾后再建新连接 —— 否则遥控器会把新连接当成
        # "第二个客户端"，只给 4 个基础服务，ATVV 语音服务整个消失。
        self._pending = set()

        # ---------- 语音键手势：单击 / 长按 / 双击 ----------
        # 只要 voice 配置里带了 double 或 tap，这个键就进入"手势模式"：
        #   · 单击（快速点一下）→ 发 tap 的动作（Ctrl+Win+Shift = 开关式语音，点一下就开始录，
        #                        说完再点一下结束 —— 不用一直按着，开录最快）
        #   · 长按（按住超过 200ms）→ 按住说话（voice 自己那套 Ctrl+Win），说多久按多久
        #   · 双击（第二下在 200ms 内）→ 发 double 的动作（Ctrl+F10 唤 Codex）
        # ★ 三者怎么分开：按下先挂 200ms 计时器 ——
        #   到点还按着 = 长按（开始说话）；没到点就松开 = 短按，再等 200ms 确认不是双击 → 单击。
        self._gesture = bool(self.voice_spec.get("long") or self.voice_spec.get("double")
                             or self.voice_spec.get("tap"))
        _default_gesture = {"mode": "tap", "key": "F10", "mods": ["CTRL"]}
        _default_tap = {"mode": "tap", "key": "SHIFT", "mods": ["CTRL", "LWIN"]}
        self._double_spec = self.voice_spec.get("double") or _default_gesture
        self._tap_spec = self.voice_spec.get("tap") or _default_tap
        self._long_key = VoiceKey(self.voice_spec.get("long") or _default_gesture)
        self._double_key = VoiceKey(self._double_spec)
        self._tap_key = VoiceKey(self._tap_spec)
        self._last_tap = 0.0          # 上一轮"短按"松开的时间（双击判定用）
        self._long_timer = None       # 长按判定计时器
        self._long_fired = False      # 这一轮是否已经判成长按（已经在说话）
        self._tap_timer = None        # 单击延迟计时器（等双击窗口过去）
        self._suppress = False        # 这一轮按下被判定为双击的第二下 → 什么都不做

    # ---------- 对外 ----------
    def poke(self):
        """遥控器刚有按键活动 —— 现在它醒着，立刻重试一次连接。

        为什么关键：BLE 外设只在刚醒的一小段时间里能被连上（之后又睡，
        或被系统 HID 拿走那唯一的连接），而失败退避要等 3~10 秒 ——
        靠"等"就可能把窗口错过去。按键钩子（keys.KeyTap.on_activity）负责叫醒这里。
        """
        # ★ 2026-11-09：按键是**成功率最高**的时刻，顺手把"重新发现"的时间窗也清零，
        #   这样这一轮如果只看到 4 个服务，会立刻再试一次 UNCACHED（不必等 30 秒）。
        self._uncached_at = 0.0
        self._poke.set()

    async def _maybe_unstick(self):
        """连续几次"扫不到广播"之后**自动解卡**：把蓝牙关开一次。

        只在**确认特征**时动手，并且处处留退路：
          · 连续 ≥3 次「没扫到广播」（= 系统 HID 攥着唯一那条连接）才触发；
          · 限流：10 分钟最多一次（别把用户蓝牙反复关开）；
          · 用 Radio API（和设置里那个开关同一机制、不要管理员），**每次都读回状态确认**；
          · 关开失败/开不回来 → 日志里立刻给出手动补救（设置里打开蓝牙 / 设备管理器启用）。
        可用 `voice` 配置里的 `auto_unstick: false` 关掉这个行为。
        """
        if not bool((self.voice_spec or {}).get("auto_unstick", True)):
            return
        if getattr(self, "_stuck", 0) < 3:
            return
        now = time.time()
        if now - getattr(self, "_unstuck_at", 0.0) < UNSTICK_COOLDOWN:
            return
        self._unstuck_at = now
        self._stuck = 0
        self._log(i18n.L("语音：连续几次都扫不到广播 —— 自动把蓝牙关开一次解卡"
                         "（蓝牙设备会断开几秒，马上恢复）…",
                         "Voice: several scans in a row found no advertisement — auto-cycling "
                         "Bluetooth to unstick it (Bluetooth devices drop for a few seconds)…"))
        try:
            kind, detail = await unstick_bluetooth()
        except Exception as e:
            kind, detail = "failed", f"{type(e).__name__}: {e}"
        if kind == "restarted":
            self._log(i18n.L(f"语音：蓝牙已关开一次（{detail}）—— 继续重连。"
                             f"若还不行，请把遥控器**电池取出 10 秒**再装回。",
                             f"Voice: Bluetooth was cycled ({detail}) — reconnecting. If it still "
                             f"fails, take the remote's battery out for 10 seconds."))
        elif kind == "turned-on":
            self._log(i18n.L(f"语音：蓝牙原本是关的，已帮你打开（{detail}）",
                             f"Voice: Bluetooth was off; turned it on ({detail})"))
        elif kind == "unavailable":
            self._log(i18n.L(f"语音：自动解卡用不了（{detail}）—— 请手动在「设置 → 蓝牙」里"
                             f"关开一次，或把遥控器电池取出 10 秒。",
                             f"Voice: auto-unstick is unavailable ({detail}) — cycle Bluetooth by "
                             f"hand in Settings, or take the remote's battery out for 10 seconds."))
        else:
            self._log(i18n.L(f"语音：⚠ 自动解卡没能把蓝牙开回来（{detail}）",
                             f"Voice: ⚠ auto-unstick could not turn Bluetooth back on ({detail})"))
        self._poke.set()                    # 立刻再试一次连接
        # ★ 2026-11-07：解卡之后连接必然已经断了，下一轮不要再走 _close_current
        #   里的「等旧连接收尾（最长 8 秒 + 2 秒）」。那 10 秒正是 Windows 的 HID
        #   驱动把连接抢回去的时间——语音用地址直连（不扫描）本来能抢在前面，
        #   结果被自己的等待让掉了。跳过它，直接抢。
        self._just_unstuck = True

    def set_addr(self, addr):
        """遥控器蓝牙地址热更新。

        ⚠ 以前这里没有：界面里把地址改好、保存之后，**运行中的语音角色还用着旧地址**，
        表现就是"地址明明填对了，语音键还是没反应"，非得重启客户端才行。
        返回 True 表示地址真的变了（调用方拿来决定要不要记日志 / 立刻重连）。
        """
        new = (addr or "").strip()
        if new == (self.addr or "").strip():
            return False
        self.addr = new
        self._addr_warned = False
        self._no_addr = not new
        self._fails = 0
        self.poke()                      # 停止退避，马上按新地址试一次
        return True

    def _emit(self, kind, payload=None):
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def _log(self, msg):
        self._emit("log", msg)

    def set_voice_spec(self, spec):
        self.voice_spec = spec or self.voice_spec
        self.voice = VoiceKey(self.voice_spec)
        # 手势字段也要跟着热更新，否则改了配置得重启客户端才生效
        self._gesture = bool(self.voice_spec.get("long") or self.voice_spec.get("double")
                             or self.voice_spec.get("tap"))
        _default_gesture = {"mode": "tap", "key": "F10", "mods": ["CTRL"]}
        _default_tap = {"mode": "tap", "key": "SHIFT", "mods": ["CTRL", "LWIN"]}
        self._double_spec = self.voice_spec.get("double") or _default_gesture
        self._tap_spec = self.voice_spec.get("tap") or _default_tap
        self._long_key = VoiceKey(self.voice_spec.get("long") or _default_gesture)
        self._double_key = VoiceKey(self._double_spec)
        self._tap_key = VoiceKey(self._tap_spec)

    # ---------- 线程主体 ----------
    def run(self):
        try:
            # 监听线程里必须自己初始化 COM，否则 WinRT 会报
            # "Illegal attempt to resolve or release an IAgileReference after uninitializing COM"
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)      # COINIT_MULTITHREADED
        except Exception:
            pass
        try:
            asyncio.run(self._main())
        except Exception as e:
            self._emit("status", {"connected": False,
                                  "note": i18n.L(f"语音线程退出：{e}",
                                                 f"Voice thread exited: {e}")})

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        if self.voice.clear_stuck():
            self._log("语音键：清掉上次残留的按下状态")

        # ★ 音频出口交给「看护任务」，不在启动这一刻一锤定音。
        #   开机自启时，虚拟声卡 / Windows 音频服务往往比我们晚就绪；
        #   只在启动时 find_cable() 一次的话，找不到就**永远**没声音 ——
        #   这正是「开机后按语音键没反应」的真因。现在改成每 5 秒重试，
        #   声卡一就绪就自动接上，中途掉了也会重建。
        self._audio_task = asyncio.ensure_future(self._audio_supervisor())
        self._watch = asyncio.ensure_future(self._alt_watch())

        try:
            await self._loop_sessions()
        finally:
            for t in (self._watch, self._audio_task):
                if t:
                    t.cancel()
            if self.relay_obj:
                self.relay_obj.stop()
                self.relay_obj = None
            if self.sink:
                self.sink.stop()
                self.sink = None
            self.voice.release()

    async def _audio_supervisor(self):
        """看护「音频出口 + 内置麦克风直通」：没有就建，坏了就重建。"""
        while not self.stop_flag.is_set():
            if self.sink is not None and not self.sink.alive:
                self._log("语音：音频出口掉线，正在重开…")
                self.sink.stop()
                self.sink = None
                if self.relay_obj:
                    self.relay_obj.stop()
                    self.relay_obj = None

            if self.sink is None:
                out_idx, _in_idx = find_cable()
                if out_idx is None:
                    if not self._cable_warned:
                        self._cable_warned = True
                        self.note = i18n.L("等虚拟声卡就绪…",
                                           "Waiting for the virtual audio cable…")
                        self._emit("status", {"connected": False, "note": self.note})
                        self._log("语音：暂时找不到虚拟声卡 VB-CABLE"
                                  "（开机后声卡常比程序晚就绪）—— 每 5 秒重试")
                else:
                    try:
                        s = Sink(out_idx, gain=self.gain, agc=self.agc,
                                 on_log=self._log)
                        s.start()
                        self.sink = s
                        self._cable_warned = False
                        # ★ 把**设备名**一起写进日志：用户最常问的就是"虚拟声卡的 in/out
                        #   到底哪个是哪个"。日志里直接说清"程序往哪写、输入法该读哪个"，
                        #   能省一整轮来回（真机上被问过两次）。
                        dev_out = ""
                        try:
                            import sounddevice as sd
                            dev_out = str(sd.query_devices(out_idx)["name"])
                        except Exception:
                            pass
                        try:
                            _ci, _co = find_cable()
                        except Exception:
                            _ci = _co = None
                        self._log("语音：音频出口已开启（"
                                  + ("AGC" if self.agc else f"固定增益 {self.gain}x")
                                  + "）" + (f" → 写入「{dev_out}」" if dev_out else "")
                                  + (f"；输入法那边请选「{_dev_name(_co)}」（录制设备）"
                                     if _co is not None else ""))
                    except Exception as e:
                        if not self._cable_warned:
                            self._cable_warned = True
                            self._log(f"语音：打开音频出口失败 "
                                      f"{type(e).__name__}: {e}（每 5 秒重试）")

            if self.want_relay and self.sink is not None and self.relay_obj is None:
                try:
                    r = MicRelay(self.sink, on_log=self._log)
                    if r.probe():
                        r.start()
                        self.relay_obj = r
                        self._log(f"语音：麦克风直通已就绪（{r.name}，"
                                  f"按住键盘语音快捷键时转发）")
                        self._relay_warned = False
                    elif not self._relay_warned:
                        self._relay_warned = True
                        self._log("语音：没找到可用的真实麦克风，直通暂时不开"
                                  "（系统默认录音设备若是 CABLE Output，"
                                  "直通会自己喂自己，所以这里故意不用）")
                except Exception:
                    pass
            elif not self.want_relay and self.relay_obj is not None:
                self.relay_obj.stop()
                self.relay_obj = None

            await asyncio.sleep(5)

    async def _alt_watch(self):
        """轮询键盘语音键状态，决定麦克风直通开不开。

        ⚠ 遥控器正在送音频时必须关掉直通：遥控器的声音本来就是从
        CABLE 这一路出去的，这时候再直通一路进来，输入法会同时听到
        **同一句话的两份**（真实麦克风还会早到几百毫秒），识别率会
        明显变差 —— 这个「双份」曾被认为是"千问不认虚拟声卡"。
        所以：只有「按住键盘语音键、且遥控器没在说话」时才直通。
        """
        vks = self.voice.mods + [self.voice.key] if self.voice.ok else []
        while not self.stop_flag.is_set():
            if self.relay_obj and vks:
                # `all` 而不是 `any`：组合键要**整组**按住才算"在按语音键"。
                # 写成 any 的话，只要碰一下 Ctrl（很多组合键的修饰键）就开直通，
                # 真实麦克风会被悄悄混进虚拟声卡里。
                held = all(keys.key_is_down(v) for v in vks)
                self.relay_obj.active_holder = bool(held) and not bool(
                    getattr(self, "_active", False))
            await asyncio.sleep(0.05)

    # ---------- 连接 ----------
    def _new_client(self, dev, timeout, cached=True):
        """统一构造 BleakClient。

        ★ 2026-11-07 修正：这里必须**读缓存**，原来写的 False 是错的。

        实测证据（Windows 睡眠唤醒后，HID 独占着设备，用 WinRT 直接枚举）：
          GetGattServicesAsync(UNCACHED) → 0 个服务   ← 设备不响应"重新发现"
          GetGattServicesAsync(CACHED)   → 8 个服务   ← 1800/1801/180a/180f/1812/ae40/ab5e0001/d343bfc0

        也就是说 ab5e0001（ATVV 语音）一直躺在 Windows 的 GATT 缓存里，
        而强制重新发现时设备根本不理你，于是语音侧"看不到 ATVV"，
        表现为"按键好、语音断，只能重启客户端"。

        所以 use_cached_services 必须是 True（也就是 bleak 的默认行为）。
        老版本 bleak 不认这个关键字就退回默认，同样是读缓存，也正确。
        """
        from bleak import BleakClient
        try:
            return BleakClient(dev, timeout=timeout,
                               winrt={"use_cached_services": bool(cached)})
        except TypeError:
            return BleakClient(dev, timeout=timeout)

    async def _connect_wait(self, client, timeout):
        """超时后**不等**这个连接：bleak 的 WinRT 连接底层是 COM IAsyncAction，
        被 asyncio.wait_for 取消时不会真正中断（会卡死 30s+）。
        所以只等一段时间，没完成就放手，另挂回调收尾。"""
        task = asyncio.ensure_future(client.connect())
        self._pending.add(task)
        task.add_done_callback(lambda t: self._pending.discard(t))
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if done:
            try:
                task.result()
                return True
            except Exception as e:
                self._log(f"语音：连接失败 {type(e).__name__}: {e}")
                return False
        self._log(f"语音：连接超时（{timeout:.0f}s）—— 遥控器多半在休眠")
        task.add_done_callback(lambda t: asyncio.ensure_future(self._reap(t, client)))
        return False

    async def _pending_settled(self, timeout):
        """等"还没收尾的连接任务"结束。返回 True = 通道干净，可以建新连接。

        ★ 2026-11-09 小柯：BLE 外设只给**第一个**客户端完整服务表。本进程里只要
          还挂着一条半开的连接（connect 超时后底层 IAsyncAction 仍在跑），新连接就会
          被当成第二个客户端，枚举到的永远是 1800/1801/180a/180f —— ATVV 语音服务
          整个不见，重连多少次都一样。所以每次建新连接之前都要过这一关。
        """
        pend = [t for t in list(getattr(self, "_pending", ())) if not t.done()]
        if not pend:
            return True
        self._log(f"语音：等上一条连接收尾（{len(pend)} 条）…")
        try:
            await asyncio.wait(pend, timeout=timeout)
        except Exception:
            pass
        left = [t for t in pend if not t.done()]
        if left:
            self._log(f"语音：仍有 {len(left)} 条连接没结束，这一轮先不连"
                      "（宁可等，也不并发第二条 —— 并发只会让遥控器只给 4 个基础服务）")
        return not left

    def _wake_hint(self, why):
        """叫不醒遥控器时，给一句"该怎么办"，**绝不重启进程**。

        ★★ 2026-11-09 小柯：进程级自愈在这里是净损失，已经删掉 ★★
          原来"连续 2 轮连不上 / 连续 2 轮只看到 4 个服务"就拉起 `_自愈重启.ps1`
          换一个进程。实测（client.log 20:33~22:36，34 次换进程）：
            · 深睡时换进程**完全无效**（22:18:39、22:26:23、22:27:42 三次全新进程
              都只拿到 4 个或 6 个服务，ATVV 依然不在）；
            · 浅睡时偶尔有效（22:10:19 换进程 → 22:10:33 恢复），但代价是按键映射
              重建 10~20 秒 + 界面端口消失 30~60 秒，用户按语音键正好落在这段空窗里；
            · 更糟的是：脚本/看门狗/网页三层各自都会重建角色，而旧角色线程没断开
              GATT → 同时挂两个客户端 → 遥控器只给 4 个基础服务。**自愈本身在制造故障。**
          真正能叫醒它的只有遥控器上的物理按键（HID 报文，系统蓝牙栈直接收），
          软件侧没有任何通道（ATVV 不在线时连 CTL 都没有）。所以这里只留提示，
          由 `poke()` 在按键一到就立刻重连 —— 对用户就是"按一下就好"。
        """
        now = time.time()
        if now - getattr(self, "_hint_at", 0.0) < 300:
            return
        self._hint_at = now
        self._log(i18n.L(
            f"语音：{why}。遥控器睡着时软件叫不醒它 —— 按住【语音键】约 1 秒再松开，"
            f"它一醒这边会立刻连上（不会重启客户端，按键映射全程不断）。",
            f"Voice: {why}. A sleeping remote cannot be woken from the host side — hold the "
            f"VOICE key for about a second and release; it reconnects within a moment "
            f"(no client restart, key mapping stays up the whole time)."))

    async def _reap(self, task, client):
        """迟到的连接：真连上了就关掉，别占着设备。"""
        try:
            await task
        except Exception:
            return
        try:
            if client.is_connected and client is not self._client:
                await client.disconnect()
        except Exception:
            pass

    async def _close_current(self):
        """开新会话前，把上一条连接彻底关掉。

        ★ 这是「缺少 ATVV 特征」死循环的真因：遥控器的 GATT 通道只给第一个
        客户端完整服务表，第二个客户端只剩 4 个基础服务。本进程里只要挂着
        一条没关干净的会话（典型来源：connect 超时后那个迟到的连接），之后
        每次新连接都会被当成第二个客户端 —— 重连治不好，只有换进程才行。
        """
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass
        # ★ 2026-11-09：把本轮用的 WinRT 设备句柄也关掉。
        #   bleak 的 disconnect 只关它自己内部建的 requester/session，
        #   而 _device_with_details() 另外 new 出来的那个 BluetoothLEDevice 没人管 ——
        #   累积的句柄会让设备侧以为"还有客户端"，于是只肯给 4 个基础服务。
        d = getattr(self, "_last_dev", None)
        if d is not None:
            self._last_dev = None
            try:
                obj = getattr(d, "details", None)
                if obj is not None and hasattr(obj, "close"):
                    obj.close()
                    self._log("语音：已释放设备句柄")
            except Exception:
                pass
        # ★ 等"迟到的连接"彻底收尾，再让下一轮建新连接。
        #   遥控器的 GATT 只给第一个客户端发完整服务表；只要上一条连接还挂着，
        #   新连接就会被当成第二个客户端，枚举到的永远是 1800/1801/180a/180f 这
        #   4 个基础服务，ab5e0001 整个不见 —— 这就是"按键能用、语音不行、只能重启"
        #   的真正成因。等它结束 + 再留一点时间给 _reap 的 disconnect 生效。
        pend = [t for t in list(getattr(self, "_pending", ())) if not t.done()]
        if pend:
            self._log(f"语音：等上一条连接收尾（{len(pend)} 条）…")
            try:
                await asyncio.wait(pend, timeout=8.0)
            except Exception:
                pass
            left = [t for t in pend if not t.done()]
            if left:
                self._log(f"语音：仍有 {len(left)} 条连接没结束，先等 2 秒让通道释放")
            await asyncio.sleep(2.0 if left else 1.0)
            # 还没结束就别连了：宁可这一轮不连（下一轮会自动重试），也不能让
            # 新连接和旧连接同时挂在遥控器上 —— 那样必然只剩 4 个基础服务。
            if [t for t in pend if not t.done()]:
                self.note = i18n.L("蓝牙通道还在释放，下一轮自动重连",
                                   "Releasing the Bluetooth channel; will retry next round")
                return False
        # 连续几次都只枚举到 4 个基础服务 → 通道被占得很死，给蓝牙栈一段安静的
        # 时间彻底释放（比让用户去点「重启」省事；只在第 3~6 次之间做，避免永久变慢）。
        # ★ 2026-11-08：原来写的是 `3 <= self.misses <= 6`，一旦 misses 涨过 6 就
        #   **永久失效**，于是彻底卡在"每 4 秒重连、永远 4 个服务"的空转里，
        #   日志刷屏、退避也不起作用 —— 这正是"语音总是断开"的机械原因。
        #   改成每 3 次静置一次：既不会永久失效，也不会每轮都慢 12 秒。
        # ★ 2026-11-09 小柯：静置从 12 秒降到 4 秒。原来的 12 秒正好卡在用户
        #   "按一下语音键"之后 —— 遥控器已经醒了，我们却还在睡，于是又判成没连上，
        #   接着退避更久。4 秒足够让蓝牙栈释放通道，又不会把恢复窗口拖长。
        if self.misses >= 3 and self.misses % 3 == 0:
            self._log("语音：连续看不到 ATVV，静置 4 秒让蓝牙通道彻底释放"
                      "（自动恢复，不用重启客户端）")
            # 静置期间遥控器一有按键就立刻结束静置（不消费 _poke，交给主循环判断）。
            _end = time.time() + 4.0
            while (time.time() < _end and not self.stop_flag.is_set()
                   and not self._poke.is_set()):
                await asyncio.sleep(0.2)
            self.misses += 1
        return True

    async def _loop_sessions(self):
        while not self.stop_flag.is_set():
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except (ImportError, ModuleNotFoundError) as e:
                # 依赖没装：每 3 秒重试一万次也没用，只会把日志刷爆。
                # 明确停下来说清楚该干什么。
                self.note = i18n.L(
                    f"缺依赖（{e}）—— 装完依赖重启客户端即可",
                    f"Missing dependency ({e}) — install dependencies, then restart the client")
                self._emit("status", {"connected": False, "note": self.note})
                self._log(f"语音：{self.note}；本角色已停止（不会反复重试）")
                return
            except Exception as e:
                self._log(f"语音：连接异常 {type(e).__name__}: {e}")
                hint = pairing_hint(type(e).__name__, str(e))
                if hint and hint != getattr(self, "_last_hint", None):
                    self._last_hint = hint
                    self._log(hint)
            if self.stop_flag.is_set():
                break
            self._emit("status", {"connected": False, "note": self.note})
            # ★ 退避：原来固定 3 秒重试一轮，一晚上能发起上万次连接，而每次超时都是
            #   一条"半开"的 WinRT 连接留在蓝牙栈里 —— 反倒更容易把设备拖在"已连接"
            #   状态、再也不广播。改成成功即重置、失败越久越慢。
            #   ★ 2026-11-09 小柯：封顶 45 秒 → **10 秒**。用户要的是"像蓝牙鼠标一样
            #   按一下就能用"：45 秒封顶意味着他按完语音键最多还要等 45 秒，实际体验
            #   就是"按了没反应"。现在即使一路失败也每 10 秒重试一次；真正的唤醒通道
            #   是按键（poke() 会打断退避立刻重连），这里只是兜底。
            if getattr(self, "_no_addr", False):
                # 还没填遥控器地址：这不是"连不上"，别拿它刷"已连续 N 次没连上"
                # （新用户首启会看到这条，容易误读成程序坏了）。
                fails = getattr(self, "_fails", 0)
                delay = 10.0
            else:
                fails = 0 if getattr(self, "_session_ok", False) else getattr(self, "_fails", 0) + 1
                self._fails = fails
                delay = min(3.0 * (2 ** min(fails, 2)), 10.0)
                if fails >= 8 and fails % 8 == 0:
                    self._log(f"语音：已连续 {fails} 次没连上，退避到 {delay:.0f} 秒；"
                              f"若一直这样，按住遥控器语音键 1 秒即可（软件侧叫不醒它）")
            # 等退避，但遥控器一有按键活动就立刻重试（那会儿它刚醒，成功率最高）
            end = time.time() + delay
            while (time.time() < end and not self.stop_flag.is_set()
                   and not self._poke.is_set()):
                await asyncio.sleep(0.2)
            if self._poke.is_set():
                self._poke.clear()
                self._log("语音：遥控器有按键活动 —— 不再退避，立刻重试连接")

    async def _device_with_details(self):
        """不靠广播拿到设备对象，塞进 BLEDevice.details。

        为什么必须这么做（真机实测）：BLE 外设只接受**一个**连接。Windows 的 HID
        栈一旦拿住遥控器，它就不再广播 —— 同一秒里"按键能进 Windows、扫描却什么都
        看不到"。而原来的流程是「details=None 硬连 → 扫广播 → 再连」，于是只要
        HID 拿着连接，**扫不到就永远连不上**，一卡就是一整晚（重启程序、重启电脑都
        治不好，因为开机自启起来立刻又进这个循环）。

        Windows 自己有配对记录，`FromBluetoothAddressAsync` 拿设备对象**不需要广播**，
        把对象塞进 details 后 bleak 就能直接连（实测这一步能走到真正的 connect）。
        """
        try:
            from winrt.windows.devices.bluetooth import BluetoothLEDevice
            from bleak.backends.device import BLEDevice
        except Exception:
            return None
        try:
            n = int(str(self.addr).replace(":", "").replace("-", ""), 16)
        except Exception:
            return None
        try:
            dev = await BluetoothLEDevice.from_bluetooth_address_async(n)
        except Exception as e:
            self._log(f"语音：按地址取设备对象失败 {type(e).__name__}: {e}")
            return None
        if dev is None:
            return None
        h = "%012X" % int(dev.bluetooth_address)
        mac = ":".join(h[i:i + 2] for i in range(0, 12, 2))
        return BLEDevice(mac, dev.name or "Chromecast Remote", dev)

    async def _session(self):
        from bleak import BleakScanner
        from bleak.backends.device import BLEDevice

        self._session_ok = False
        if not (self.addr or "").strip():
            # 出厂默认地址是空的（不把开发机那支遥控器的地址写进公开仓库），
            # 所以新用户第一次启动会走到这里：说清楚该干什么，别刷超时。
            self._no_addr = True
            if not getattr(self, "_addr_warned", False):
                self._addr_warned = True
                self._log("语音：还没填遥控器蓝牙地址 —— 到「设置」点「扫描」选中它，或直接粘贴地址")
            self.note = i18n.L("还没填遥控器蓝牙地址（到设置页扫描一次）",
                               "No remote address yet (scan it on the Settings page)")
            return
        self._no_addr = False

        # ★ 2026-11-07：刚解卡过（蓝牙刚关开一次），连接一定是断的，旧会话也没了。
        #   这时候千万别再做"等上一条连接收尾"那套（最长 10 秒），因为 Windows 的
        #   HID 驱动会在蓝牙恢复后的两三秒内把连接抢回去；语音是按地址直连、不用扫描，
        #   本来天然比 HID 快，唯一的短板就是自己在这儿白等。直接冲。
        if getattr(self, "_just_unstuck", False):
            self._just_unstuck = False
            old, self._client = self._client, None
            if old is not None:
                try:
                    await old.disconnect()
                except Exception:
                    pass
        elif not await self._close_current():
            return
        self._log(f"语音：连接遥控器 {self.addr} …")
        dev = await self._device_with_details()
        if dev is None:
            dev = BLEDevice(self.addr, "Chromecast Remote", None)
        # ★ 2026-11-09：记下这次用的设备句柄，断开时显式 close。
        #   以前每次连接都新建一个 WinRT BluetoothLEDevice 却从不释放，
        #   句柄越积越多；设备侧很可能因此认为"还有客户端在"，于是
        #   不广播、只给 4 个基础服务 —— 这也是"只有换进程才救得回来"的来源。
        self._last_dev = dev
        client = self._new_client(dev, 3.0)
        self._client = client
        ok = await self._connect_wait(client, 3.0)
        # ★ 2026-11-09 小柯：角色正在被停掉（重启/重建）→ 立刻放开连接再退出。
        #   否则这条"迟到的连接"会在新角色连上之后才落地，遥控器上就同时挂两个
        #   客户端，只给 4 个基础服务 —— 也就是"重建反而把语音弄死"。
        if self.stop_flag.is_set():
            await self._shutdown()
            return

        if not ok:
            # 兜底：扫一次（只在设备正好在广播时有用；HID 拿着连接时它不会广播）
            self._log("语音：直连没成，扫一次看它在不在广播（它平时不广播，属正常）…")
            found = None
            try:
                found = await BleakScanner.find_device_by_address(self.addr, timeout=4.0)
            except Exception as e:
                self._log(f"语音：扫描异常 {type(e).__name__}: {e}")
            if found is None:
                self._log("语音：没扫到广播（多半被系统 HID 拿着连接）—— 重试连接")
                # ★ 这就是"按键能用、只有语音不行"的特征：系统 HID 攥着那唯一一条 BLE 连接，
                #   遥控器因此不广播，我们也建不起自己的 GATT 连接。连续几次都这样 → 自动解卡
                #   （用 Windows 的 Radio API 把蓝牙关开一次；跟设置里那个开关同一个机制，
                #    不需要管理员，也不会把电台留在禁用状态）。
                self._stuck = getattr(self, "_stuck", 0) + 1
                await self._maybe_unstick()
            # ★★ 2026-11-09 小柯：单飞，绝不并发第二条连接 ★★
            #   上一把 connect 超时后，底层 COM IAsyncAction 其实还在跑（WinRT 取消会卡死，
            #   所以 `_connect_wait` 只是"放手"，任务仍留在 self._pending 里）。
            #   这时候再 new 一个客户端去连，遥控器上就同时挂着两条 GATT —— 它只给**第一条**
            #   完整服务表，第二条永远只剩 1800/1801/180a/180f，ab5e0001（ATVV）消失。
            #   这就是"重连越勤、语音越死"的机械原因（日志 22:18:23 起连续几十次 4 个服务）。
            #   所以先等上一条收尾；收不了尾就这一轮不连，下一轮再试。
            if not await self._pending_settled(3.0):
                self.note = i18n.L("蓝牙通道还在释放，下一轮自动重连",
                                   "Releasing the Bluetooth channel; will retry next round")
                return
            client = self._new_client(found or dev, 10.0)
            self._client = client
            ok = await self._connect_wait(client, 10.0)
            if self.stop_flag.is_set():
                await self._shutdown()
                return

        if not ok:
            self.note = i18n.L(
                "未连上（按一下遥控器任意键会重试；一直不行就点「重置蓝牙电台」）",
                "Not connected (press any remote button to retry; if it keeps failing use "
                "“Reset Bluetooth radio”)")
            # ★★ 2026-11-09 小柯：这里原来是"连续 2 次叫不醒 → 换进程" ★★
            #   34 次实测证明深睡时换进程无效、浅睡时偶尔有效但要付 30~60 秒按键空窗，
            #   而且换进程过程中新旧实例会同时抢 GATT（旧实例的连接没断），反而更坏。
            #   现在只给一句可执行的提示；按键一到 poke() 会立刻重连。
            try:
                self._fail_streak = getattr(self, "_fail_streak", 0) + 1
                if self._fail_streak >= 2:
                    self._wake_hint(i18n.L("连续 2 次都没连上",
                                           "could not connect twice in a row"))
            except Exception as _e:
                self._log(f"语音：叫醒提示出错 {type(_e).__name__}: {_e}")
            return
        self._log(f"语音：已连接遥控器 {self.addr}")
        # ★ 2026-11-08：连上 ≠ 能用。原来在这里就置 _session_ok = True，于是
        #   "连上了但只有 4 个基础服务"也被当成成功，_loop_sessions 里的退避计数
        #   每轮都被清零 → 每 3 秒空转一次、日志刷屏，永远好不了。
        #   改成真正拿到 ATVV 之后才置（见下面 self.misses = 0 处）。
        # ★ 不在这里清零 _stuck（2026-11-06 修正）：连上 ≠ 可用。
        #   真正的故障是"连上了却只有 4 个基础服务"（ab5e0001 ATVV 不在）——
        #   那种情况以前从不增加 _stuck，于是 _maybe_unstick 永远到不了阈值 3，
        #   表现为"语音断了只能重启客户端"。清零改到真正拿到 ATVV 之后。

        chars, uuids = [], []
        try:
            for s in client.services:
                uuids.append(str(s.uuid).lower()[:8])
                chars.extend(s.characteristics)
        except Exception as e:
            self._log(f"语音：枚举服务失败 {type(e).__name__}: {e}")
            await self._close_current()
            return

        def find(prefix):
            for c in chars:
                if str(c.uuid).lower().startswith(prefix):
                    return c
            return None

        audio, status = find(ATVV_AUDIO), find(ATVV_STATUS)
        ctl = find(ATVV_CTL)

        # ★★ 2026-11-08 根治"语音总是断开"的主改动 ★★
        # 本进程里只要有一条没关干净的连接挂着，遥控器就把新连接当成"第二个客户端"，
        # 只发 4 个基础服务（1800/1801/180a/180f），ab5e0001 整个不见 —— 然后每 4 秒
        # 重连一次、永远好不了，只能重启客户端。
        #
        # ★ 2026-11-09 23:0x 小柯：**这条路的成功率不高，但不能只试一次**。
        #   实测（client.log 22:53:44 → 22:53:46）：试了一次 UNCACHED，回来的仍是
        #   4 个服务；而原来 `_tried_uncached` 只在"成功拿到 ATVV"后才复位，
        #   于是这次失败把唯一一条自救通道**永久关掉** —— 之后 28 次失败再没试过。
        #   现在改成"时间窗"：30 秒内最多试一次，失败也还能再来。
        #   （代价可控：每次只是多一次 0.8s 间隔的重连，不会并发两条连接。）
        _unc_at = float(getattr(self, "_uncached_at", 0.0))
        if (not (audio and status)) and len(uuids) <= 4 \
                and (time.time() - _unc_at) > 30.0:
            self._uncached_at = time.time()
            self._log("语音：只看到 4 个基础服务 —— 改用「重新发现」模式再连一次（不读缓存）")
            old_c, self._client = client, None
            try:
                await old_c.disconnect()
            except Exception:
                pass
            await asyncio.sleep(0.8)
            client = self._new_client(dev, 10.0, cached=False)
            self._client = client
            if await self._connect_wait(client, 8.0):
                try:
                    chars.clear()          # 保持同一列表对象，find() 闭包才跟着更新
                    uuids.clear()
                    for s in client.services:
                        uuids.append(str(s.uuid).lower()[:8])
                        chars.extend(s.characteristics)
                    self._log(f"语音：重新发现后枚举到 {len(uuids)} 个服务："
                              f"{', '.join(uuids) or '空'}")
                except Exception as e2:
                    self._log(f"语音：重新发现后枚举失败 {type(e2).__name__}: {e2}")
                audio, status = find(ATVV_AUDIO), find(ATVV_STATUS)
                ctl = find(ATVV_CTL)

        if not (audio and status):
            self.misses += 1
            # ★ 计入"卡住"计数（2026-11-06 修正）：这条路径以前从不增加 _stuck，
            #   导致 _maybe_unstick 永远触发不了，只能无限重连。
            self._stuck = getattr(self, "_stuck", 0) + 1
            self._log(f"语音：缺少 ATVV 特征（本次枚举到 {len(uuids)} 个服务："
                      f"{', '.join(uuids) or '空'}）")
            if self.misses >= 3:
                self._log("语音：连续 3 次看不到 ATVV —— 蓝牙通道被占住，"
                          "正在自动释放通道并重试（不用重启客户端）")
            if self._stuck >= 3:
                # 走到这里说明"自己关也关不干净"：Windows 的 HID 驱动攥着那条唯一
                # 的连接，只有关开蓝牙电台才能把它松掉。_maybe_unstick 自带 10 分钟
                # 限流，不会反复折腾用户的其他蓝牙设备。
                await self._maybe_unstick()
                self.misses = 0     # 解卡后重新开始计静置周期，避免 misses 一路涨到
                                    # 超过 6 之后 _close_current 的静置永久失效。
            if self.misses >= 2 and not getattr(self, "_variant_hinted", False):
                # ★ 2026-11-09 修正文案（原说法是"这台遥控器没有语音服务、换遥控器"）。
                #   实测（_dual_check / _svc_hint_check，2026-11-09 11:36~11:37）：
                #   bleak 只认到 4 个基础服务、按 UUID 请求 ATVV 返回 0 个，
                #   而设备其实是被"芯片级省电"关掉了 ATVV —— 沉睡后就不逐条列出来了。
                #   所以这是**半睡**，不是没有硬件：别让用户白买一支新遥控器。
                self._variant_hinted = True
                self._log(i18n.L(
                    "语音：遥控器这会儿只给出 4 个基础服务（1800/1801/180a/180f），"
                    "ATVV 语音服务暂时不在线。这几乎都是遥控器进入了半睡省电，"
                    "**不是这支遥控器没有语音硬件，也不用换遥控器**。"
                    "叫醒它：按住【语音键】约 2 秒再松（按音量键、方向键不算），"
                    "或者取下电池等 10 秒再装回。醒来后软件会自动连上。",
                    "Voice: the remote is currently exposing only the 4 basic services "
                    "(1800/1801/180a/180f); the ATVV voice service is temporarily offline. "
                    "This almost always means the remote went into a half-sleep power-saving "
                    "state — it does **not** mean this remote lacks voice hardware, and you "
                    "do not need to replace it. Wake it: hold the VOICE key for about 2 "
                    "seconds and release (volume or dpad keys do not count), or take the "
                    "battery out for 10 seconds and put it back."))
            # ★★ 2026-11-09 小柯：这里原来是"连续 2 轮只剩 4 个服务 → 换进程" ★★
            #   实测（client.log 20:33~22:36）深睡时换进程完全无效：22:18:39、
            #   22:26:23、22:27:42 三次全新进程照样只有 4 个服务；浅睡时偶尔有效，
            #   但要付 30~60 秒按键空窗。更要命的是换进程期间新旧实例会同时抢 GATT，
            #   于是"只剩 4 个服务"变成常态 —— 自愈本身在制造故障。
            #   现在只提示；按键一到 poke() 会立刻重连（这是唯一真正能叫醒它的通道）。
            try:
                self._four_streak = getattr(self, "_four_streak", 0) + 1
                if self._four_streak >= 2:
                    self._wake_hint(i18n.L("遥控器只剩 4 个基础服务（ATVV 不在）",
                                           "the remote exposes only the 4 basic services (no ATVV)"))
            except Exception as _e2:
                self._log(f"语音：提示判断出错 {type(_e2).__name__}: {_e2}")
            await self._close_current()
            return

        self.misses = 0
        self._stuck = 0                     # ★ 真正拿到 ATVV 才算这次成功（2026-11-06）
        self._session_ok = True             # ★ 2026-11-08：真正能用才算成功，退避才归零
        self._uncached_at = 0.0             # ★ 2026-11-09：成功一次就清零，下次"只剩 4 个"能立刻重发现
        self.connected = True
        self.note = i18n.L("已连接", "Connected")
        self._emit("status", {"connected": True, "note": self.note})
        if self.sink:
            self.sink.reset()

        loop = asyncio.get_running_loop()
        last_frame = [0.0]

        def on_status(_c, data: bytearray):
            b = bytes(data)
            if not b:
                return
            loop.call_soon_threadsafe(self._on_status, b)

        def on_audio(_c, data: bytearray):
            self.frames += 1
            self._session_frames += 1
            last_frame[0] = time.time()
            if self.sink:
                try:
                    self.sink.feed_adpcm(bytes(data))
                except Exception:
                    pass

        # 音频订阅必须先于状态订阅，避免丢掉起始帧
        await client.start_notify(audio, on_audio)
        await client.start_notify(status, on_status)
        if ctl:
            try:
                await client.write_gatt_char(ctl, bytes([0x0A, 0x00, 0x04, 0x00, 0x01]),
                                             response=False)
            except Exception as e:
                self._log(f"语音：GET_CAPS 发送失败（不影响收流）: {e}")

        how = "按一下" if self.voice.mode == "tap" else "按住"
        if self.voice.ok:
            self._log(f"语音：语音键目标 = {how} {self.voice.label}")
        else:
            self._log(f"语音：✗ 认不出语音键目标 {self.voice_spec} —— 语音键不会发任何键")
        self._log("语音：就绪 —— 按住遥控器【语音键】说话，松开即停")

        disc = asyncio.Event()

        def on_disc(_c):
            loop.call_soon_threadsafe(disc.set)

        try:
            client._backend._disconnected_callback = on_disc      # 兜底
        except Exception:
            pass

        # 用轮询 + 断线事件维持会话：任何一边退出就结束本次会话
        # ★ 2026-11-09 保活（"开机好好的、一会儿就不行了"的根治手段）
        #   实测证据：08:37:57 连上拿到完整服务 → 08:39~08:42 用语音正常 →
        #   08:45:23 连接断开（中间空闲约 3 分钟）→ 之后一直"连接超时（遥控器多半在休眠）"
        #   → 按音量键唤醒后能连上，但只剩 1800/1801/180a/180f 四个基础服务，ATVV 消失。
        #   也就是说：**设备空闲就会自己睡，睡着的状态下 GATT 只暴露 4 个基础服务**，
        #   这不是 bleak 缓存问题，也不是"第二个客户端"问题（重启进程也救不回来）。
        #   BLE 主机侧无法远程唤醒它，唯一能做的就是"不让它闲下来"：
        #   每 15 秒往 CTL 写一次 GET_CAPS，让 ATVV 链路一直有活动。
        #   用户明确说过"不在乎遥控器的电池消耗，不要紧、要像鼠标一样随按随用"，
        #   所以这个心跳是刻意为之，不是漏电。
        keep_last = [time.time()]
        # 首次用「有应答写」；若这支遥控器不支持，下面会自动切成无应答写并记住
        beat_use_response = [True]

        try:
            while not self.stop_flag.is_set():
                if not client.is_connected:
                    self._log("语音：连接已断开")
                    break
                now = time.time()
                if ctl is not None and now - keep_last[0] >= 8.0:
                    keep_last[0] = now
                    # ★★ 2026-11-09 第二次加固：心跳从 15 秒缩到 8 秒，
                    #   并且优先用「有应答写」（Write Request/Response），因为无应答写
                    #   只到链路层就结束，设备上层看不到任何活动，照样按空闲计时器休眠
                    #   （实测 15 秒心跳仍会 7~30 分钟断一次）。有应答写会等设备回一个
                    #   确认，等于强制一次完整往返；若这支遥控器不支持有应答写，则自动
                    #   退回无应答写，不影响原有功能。
                    try:
                        await client.write_gatt_char(
                            ctl, bytes([0x0A, 0x00, 0x04, 0x00, 0x01]),
                            response=beat_use_response[0])
                    except Exception:
                        if beat_use_response[0]:
                            beat_use_response[0] = False
                            try:
                                await client.write_gatt_char(
                                    ctl, bytes([0x0A, 0x00, 0x04, 0x00, 0x01]),
                                    response=False)
                            except Exception as e:
                                self._log(f"语音：保活心跳失败 {type(e).__name__}: {e}"
                                          " —— 设备可能又睡了，准备重连")
                                break
                        else:
                            self._log("语音：保活心跳失败（无应答写也失败）"
                                      " —— 设备可能又睡了，准备重连")
                            break
                await asyncio.sleep(0.5)
        finally:
            self.connected = False
            self.note = i18n.L("已断开", "Disconnected")
            self.voice.release()
            await self._close_current()

    # ---------- 语音键手势（长按 = 说话，双击 = 第二个动作） ----------
    @staticmethod
    def _spec_label(spec):
        names = [str(m).upper() for m in (spec.get("mods") or [])]
        return "+".join(names + [str(spec.get("key") or "")])

    def _arm_long(self):
        """按下时挂 200ms 计时器：到点还按着，就开始「按住说话」。"""
        self._disarm_long()

        def _fire():
            if not self._active or self._long_fired:
                return
            self._long_fired = True
            if self.voice.mode == "tap":
                self.voice.tap()
            else:
                self.voice.press()

        t = threading.Timer(VOICE_LONG_MS / 1000.0, _fire)
        t.daemon = True
        self._long_timer = t
        t.start()

    def _disarm_long(self):
        t, self._long_timer = self._long_timer, None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _cancel_tap_timer(self):
        t, self._tap_timer = self._tap_timer, None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _fire_tap(self):
        """短按松开、双击窗口也过去了 —— 这一下就是「单击」。"""
        if self._long_fired or self._active:
            return
        self._log("语音：单击 → " + self._spec_label(self._tap_spec))
        self._tap_key.tap()

    def _on_short_tap(self):
        """短按松开：先等 200ms 看有没有第二下，没有才发单击。"""
        self._cancel_tap_timer()
        t = threading.Timer(VOICE_TAP_DELAY_MS / 1000.0, self._fire_tap)
        t.daemon = True
        self._tap_timer = t
        t.start()

    # ---------- 语音键事件 ----------
    def _on_status(self, b: bytes):
        active = getattr(self, "_active", False)
        if b[0] == 0x04 and not active:
            self._active = True
            self.sessions += 1
            self._session_frames = 0
            self._emit("key", {"id": "voice", "down": True})
            self._log(f"语音：▼ 开始（第 {self.sessions} 次会话）")
            if not self._gesture:
                # 没配手势：最朴素的按住说话，按下立刻发
                if self.voice.mode == "tap":
                    self.voice.tap()
                else:
                    self.voice.press()
            else:
                now = time.monotonic()
                if self._last_tap and (now - self._last_tap) <= VOICE_DOUBLE_MS / 1000.0:
                    # 双击的第二下：唤 Codex，这一轮什么都不做
                    self._last_tap = 0.0
                    self._suppress = True
                    self._log("语音：双击 → " + self._spec_label(self._double_spec))
                    self._double_key.tap()
                else:
                    self._suppress = False
                    self._long_fired = False
                    self._arm_long()
        elif b[0] == 0x00 and active:
            self._active = False
            self._emit("key", {"id": "voice", "down": False})
            if not self._gesture:
                self.voice.release()
            else:
                self._disarm_long()
                if self._suppress:
                    self._suppress = False              # 双击的第二下：什么都不做
                elif self._long_fired:
                    self._long_fired = False
                    self.voice.release()                # 按住说话结束
                    self._last_tap = 0.0
                else:
                    # 短按：记下时间，等双击窗口过去；没第二下就发单击
                    self._last_tap = time.monotonic()
                    self._on_short_tap()
            n = self._session_frames
            self._log(f"语音：▲ 结束（本段 {n} 帧音频 ≈ {n / 62.5:.1f}s）")
            if n < 20:
                # 按键通了、但遥控器没送音频 —— 这是另一类问题（没真正进录音态），
                # 跟「语音键读不到」完全不是一回事，日志里必须分开说。
                self._log("语音：⚠ 音频帧太少 —— 遥控器没在送音频（多半没真正进录音态）")
            elif self.sink is None:
                self._log("语音：⚠ 音频出口还没就绪，这段没送出去（看护任务正在重试）")
        elif b[0] not in (0x00,):
            # 既不是"开始说话"(0x04) 也不是"结束"(0x00)：把原始字节打出来。
            # 遥控器换过固件/重新配对之后，通知里的操作码**有可能不一样** ——
            # 以前这种情况会被静默丢掉，表现就是"按语音键日志一行都没有"，
            # 让排查变成瞎猜。这里留一行原始帧，一眼就能看出它到底发了什么。
            self._log(f"语音：未识别的状态帧 {b.hex()}（首字节 0x{b[0]:02x}）")

    def stop(self, join_timeout=3.0):
        """停掉语音角色 —— **必须真的断开 GATT**，不能只置一个标志位。

        ★ 2026-11-09 小柯：这是"越重连越坏"的根因之一，实测证据在 client.log：
          原来这里只 `stop_flag.set()`，线程要等当前 `_session()` 走完才会看这个标志；
          而 `Roles.stop()/restart()/refresh_voice()` 紧接着就 new 一个新的 VoiceRole
          去连 —— 旧线程手里的 BleakClient 还挂着，遥控器就把新连接当成"第二个客户端"，
          只给 1800/1801/180a/180f 四个基础服务，ab5e0001（ATVV 语音）整个消失。
          22:35:09~22:35:16 七秒内角色被重建 4 次，日志里同时出现 4 条
          "重新发现后枚举到 4 个服务"，就是这个机制。
          现在改成：置标志 → 在语音线程自己的事件循环里断开连接 + 释放 WinRT 设备句柄
          → join 线程。调用方（重启/重建）可以安全地接着 new 新的 VoiceRole。
        """
        self.stop_flag.set()
        self.voice.release()
        loop = getattr(self, "_loop", None)
        if loop is not None:
            try:
                if loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                    fut.result(4.0)
            except Exception:
                pass
        if self.is_alive() and threading.current_thread() is not self:
            self.join(join_timeout)

    async def _shutdown(self):
        """断开当前连接并释放设备句柄（在 VoiceRole 自己的事件循环里跑）。

        不在这里等"迟到的连接"收尾 —— 那是 `_close_current()` 的活，最长会等 8 秒；
        退出路径要快。剩下的半开连接会随线程结束由 COM 释放。
        """
        c, self._client = self._client, None
        if c is not None:
            try:
                await asyncio.wait_for(c.disconnect(), 3.0)
            except Exception:
                pass
        d, self._last_dev = getattr(self, "_last_dev", None), None
        if d is not None:
            try:
                obj = getattr(d, "details", None)
                if obj is not None and hasattr(obj, "close"):
                    obj.close()
            except Exception:
                pass
        self.connected = False


# ============================================================ 语音 worker 监督者
class VoiceWorkerRole(threading.Thread):
    """把 BLE 连接交给一个**短命 worker 进程**，本类只做监督（2026-11-10）。

    ★ 为什么必须跨进程：Windows 的 WinRT 设备对象一旦建起来，它内部的 GATT
      服务快照就冻住了 —— 进程内 close 句柄 / 重建 BluetoothLEDevice / 换
      UNCACHED 重新发现，拿回来的还是 1800/1801/180a/180f 四个基础服务。
      小柯的对照实验：同一秒，App 进程内 28 次全是 4 个，另起独立进程直接问
      系统，CACHED/UNCACHED 都是 8 个。只有进程边界能重置这份快照。

    ★ 对外接口与 VoiceRole **完全一致**（start/stop/poke/is_alive/connected/
      note/ready/set_addr/set_voice_spec），所以 app.py 只换构造那一行。

    ★ 回收节流（防止把"自愈"又变成"重启风暴"）：
       · worker 报"连续 20 秒 / 累计 3 次只有 4 个服务"→ 这是快照过期，**立刻换新进程**
         （等 1.5 秒再起，让 Windows 把旧 GATT 句柄还给系统）；
       · 用户按下遥控器按键时，若 worker 已经见过 ≥2 次 4 服务却还没连上 → worker 立刻
         退出换进程（按键那一刻是遥控器唯一醒着的窗口，留在坏进程里只会错过）；
       · 若 60 秒内又因同样原因退出 → 说明不是快照问题（设备真的半睡），
         退避 60 → 120 → 300 秒，不再连续折腾（这期间按键可以立刻打断退避）；
       · 设备真睡（压根连不上）时 worker **不会**退出，本类也不会重建。
    """

    def __init__(self, on_event, addr=ADDR_DEFAULT, audio=None, voice_spec=None,
                 relay=True):
        super().__init__(daemon=True, name="VoiceWorkerRole")
        self.on_event = on_event
        self._addr = (addr or "").strip()
        self._audio = dict(audio or {})
        self._voice_spec = voice_spec or {"mode": "hold", "key": "RALT", "mods": []}
        self._relay = bool(relay)
        self._halt = threading.Event()
        # ★ 2026-11-10 小柯：按键叫醒用的"打断退避"事件。
        #   原来回收失败后要退避 60/120/300 秒，这期间 poke() 只是往 stdin 写一行
        #   （进程根本不在，写了个寂寞）—— 用户按键的**唯一**唤醒窗口就这么被错过，
        #   表现还是"按了没反应"。现在 poke() 同时置位这个事件，run() 的退避等待
        #   每 0.2 秒看一眼，一置位就立刻重建进程。
        self._poke_evt = threading.Event()
        self._lock = threading.Lock()
        self._proc = None
        self._connected = False
        self._ready = False
        self._note = i18n.L("未启动", "Not started")
        # ★ app.py 的 state_snapshot() 会读 `v.voice.ok`（判断语音键能不能用）。
        #   只要接口还叫 "和 VoiceRole 完全一致"，就必须有同名的 voice 字段，
        #   否则主线程会在刷新状态时抛 AttributeError、整个客户端起不来。
        self.voice = VoiceKey(self._voice_spec)
        self._recycles = 0                # 连续"4 个服务"回收的次数（用于退避）
        self._last_recycle_at = 0.0
        self._child_pid = 0
        self._last_why = ""
        self.sessions = 0
        self.frames = 0

    # ---------- 对外接口（与 VoiceRole 同名同义） ----------
    @property
    def connected(self):
        return self._connected

    @property
    def note(self):
        return self._note

    @property
    def ready(self):
        """至少起过一个 worker 进程、并且它报过 ready —— 看门狗据此决定要不要催。"""
        return self._ready

    def poke(self):
        """遥控器刚有按键活动：转发给 worker，让它**立刻**重试连接。

        为什么必须转发而不是本进程自己连：本进程根本不持有 BLE 连接。
        按键是唯一能叫醒遥控器的通道（HID 报文走系统蓝牙栈），所以这条链路
        必须通 —— 通了就是"按一下就好"。
        """
        # 先置位：万一 worker 进程不在（正在退避），监督者要能被打断、立刻重建。
        self._poke_evt.set()
        self._send({"c": "poke"})

    def set_addr(self, addr):
        new = (addr or "").strip()
        if new == self._addr:
            return False
        self._addr = new
        self._send({"c": "addr", "v": new})
        return True

    def set_voice_spec(self, spec):
        if not spec:
            return
        self._voice_spec = spec
        try:
            self.voice = VoiceKey(spec)
        except Exception:
            pass
        self._send({"c": "spec", "v": spec})

    def stop(self, join_timeout=3.0):
        """停 worker：先礼后兵 —— 发 stop 让它自己断开 GATT，等 3 秒，再强杀。

        为什么不直接 kill：worker 手里的 BleakClient 如果不 disconnect 就死掉，
        遥控器会认为"还有客户端在"，下一次连接又只剩 4 个基础服务 —— 那就正好
        制造了我们想消灭的那个故障。
        """
        self._halt.set()
        p = self._proc
        if p is not None and p.poll() is None:
            self._send({"c": "stop"})
            for _ in range(max(1, int(join_timeout / 0.1))):
                if p.poll() is not None:
                    break
                time.sleep(0.1)
            if p.poll() is None:
                # ★ 2026-11-10 小柯：必须**连子进程一起杀**（taskkill /T）。
                #   .venv 的 python.exe 是"转发器"：真正的 worker 是它的子进程。
                #   只 terminate() 转发器会留下一个孤儿 worker —— 它还握着 BLE 连接，
                #   遥控器就会把下一条连接当"第二个客户端"，只给 4 个基础服务，
                #   正好是我们花一整天在治的那个病。
                killed = False
                try:
                    import subprocess
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                   timeout=10)
                    killed = True
                except Exception:
                    pass
                if not killed:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                # 兜底：worker 自己报过 pid（真进程），转发器那层杀不到就直接杀它
                child = int(self._child_pid or 0)
                if child and child != p.pid:
                    try:
                        import subprocess
                        subprocess.run(["taskkill", "/F", "/PID", str(child)],
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                       timeout=10)
                    except Exception:
                        pass
        if self.is_alive() and threading.current_thread() is not self:
            self.join(join_timeout)
        self._connected = False

    # ---------- 内部 ----------
    def _send(self, obj):
        p = self._proc
        if p is None or p.poll() is not None:
            return False
        try:
            import json
            # ensure_ascii=True：中文一律走 \uXXXX 转义。worker 的 stdin 在 Windows
            # 下默认是 cp936，直接送 UTF-8 中文会被解坏；纯 ASCII 则永不踩编码。
            p.stdin.write((json.dumps(obj, ensure_ascii=True) + "\n").encode("utf-8"))
            p.stdin.flush()
            return True
        except Exception:
            return False

    @staticmethod
    def _python_exe():
        """选一个**带 console 流**的解释器。

        ★ 关键：pythonw.exe 不分配标准流，sys.stdin 可能是 None —— 那样 worker
          收不到 poke，退化成"只能等退避"。所以这里优先用同目录的 python.exe。
        """
        import os
        import sys
        exe = sys.executable or ""
        if exe.lower().endswith("pythonw.exe"):
            cand = os.path.join(os.path.dirname(exe), "python.exe")
            if os.path.exists(cand):
                return cand
        return exe

    def _spawn_args(self):
        """worker 进程的命令行。★ 名字不能叫 _args —— threading.Thread 内部
        有个同名字段（保存 target 的位置参数），会把方法整个盖掉。"""
        import base64
        import json
        import os
        cfg = {"addr": self._addr, "audio": self._audio, "voice": self._voice_spec,
               "relay": self._relay}
        blob = base64.b64encode(json.dumps(cfg, ensure_ascii=False).encode("utf-8")).decode("ascii")
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_worker.py")
        if not os.path.exists(script):
            # 打包成单个 exe 时没有独立的 voice_worker.py 文件 —— 用同一个 exe 的
            # `--voice-worker` 分支当 worker（app.py 的 main() 第一件事就是它）。
            import sys
            return [sys.executable, "--voice-worker", "--cfg", blob]
        return [self._python_exe(), script, "--cfg", blob]

    def _log(self, msg):
        try:
            self.on_event("log", msg)
        except Exception:
            pass

    def _handle(self, obj):
        t = obj.get("t")
        if t == "log":
            self._log(str(obj.get("m") or ""))
        elif t == "key":
            p = obj.get("p") or {}
            if p.get("down"):
                self.sessions += 1
            try:
                self.on_event("key", p)
            except Exception:
                pass
        elif t == "status":
            p = obj.get("p") or {}
            self._connected = bool(p.get("connected"))
            self._note = p.get("note") or self._note
            try:
                self.on_event("status", p)
            except Exception:
                pass
        elif t == "ready":
            self._ready = True
            self._child_pid = int(obj.get("pid") or 0)
        elif t == "exit":
            self._last_why = str(obj.get("why") or "")

    def run(self):
        import collections
        import json
        import os
        import subprocess

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        err_tail = collections.deque(maxlen=40)

        # ★ 强制子进程用 UTF-8 标准流（PYTHONUTF8 + PYTHONIOENCODING）。
        #   中文机器默认走 cp936，管道里一混编码，界面上就会冒出"就绪◆◆◆"。
        #   worker 自己也会 reconfigure 一遍，这里是双保险。
        child_env = dict(os.environ)
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"

        while not self._halt.is_set():
            try:
                proc = subprocess.Popen(
                    self._spawn_args(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, creationflags=flags, close_fds=True,
                    env=child_env)
            except Exception as e:
                self._log(i18n.L(f"语音worker：启动失败 {type(e).__name__}: {e} —— 3 秒后重试",
                                 f"Voice worker: failed to start {type(e).__name__}: {e} — "
                                 f"retrying in 3s"))
                self._halt.wait(3.0)
                continue

            self._proc = proc
            self._connected = False
            self._last_why = ""
            # 新进程已经起来了，之前攒下的 poke 就作废 —— 否则会白跳过下一次退避。
            self._poke_evt.clear()
            self._log(i18n.L(f"语音worker：已启动（pid {proc.pid}）—— BLE 连接住在这个独立进程里，"
                             f"服务快照过期时换新进程即可自动恢复",
                             f"Voice worker: started (pid {proc.pid}) — the BLE link lives in this "
                             f"separate process, so a stale service snapshot is reset by a new one"))

            def read_out(p=proc):
                for raw in iter(p.stdout.readline, b""):
                    s = raw.decode("utf-8", "replace").strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except Exception:
                        continue
                    try:
                        self._handle(obj)
                    except Exception:
                        pass

            def read_err(p=proc):
                for raw in iter(p.stderr.readline, b""):
                    s = raw.decode("utf-8", "replace").strip()
                    if s:
                        err_tail.append(s)

            t1 = threading.Thread(target=read_out, daemon=True, name="worker-out")
            t2 = threading.Thread(target=read_err, daemon=True, name="worker-err")
            t1.start()
            t2.start()

            rc = proc.wait()
            self._proc = None
            self._connected = False
            self._ready = False
            t1.join(1.0)
            t2.join(1.0)

            if self._halt.is_set():
                break

            # 把 worker 的 stderr 尾巴写进日志：import 失败、依赖缺失这类问题
            # 否则是"静默退出"，用户只会看到"语音没了"。
            if err_tail:
                for line in list(err_tail)[-6:]:
                    self._log(f"语音worker(stderr)：{line}")
                err_tail.clear()

            four_recycle = ("只有 4 个" in self._last_why) or (rc == 3)
            now = time.time()
            if four_recycle:
                if now - self._last_recycle_at < 60.0:
                    self._recycles += 1          # 连续：不是快照问题，退避
                else:
                    self._recycles = 1           # 这一轮第一次：立刻重建
                self._last_recycle_at = now
                if self._recycles <= 1:
                    # ★ 2026-11-10 小柯：0.5 秒太急 —— 旧进程刚退出，Windows 还没把
                    #   GATT 句柄还给系统，新进程连上去常常只拿到 6 个服务（实测
                    #   09:23:26 就是 6 个：1800/1801/180a/180f/ae40/d343bfc0）。
                    #   等 1.5 秒再起，一次拿到完整 8 个的概率高得多。
                    delay = 1.5
                    self._log(i18n.L(
                        f"语音worker：{self._last_why} —— 换一个新进程重置服务快照"
                        f"（这就是「断了自己回来」，不需要任何人去点重启）",
                        f"Voice worker: {self._last_why} — spawning a fresh process to reset the "
                        f"service snapshot (this is the automatic recovery; nobody has to click "
                        f"restart)"))
                else:
                    delay = min(60.0 * (2 ** (self._recycles - 2)), 300.0)
                    self._log(i18n.L(
                        f"语音worker：换进程也没拿回 ATVV（{self._last_why}）—— 说明这次不是快照过期，"
                        f"多半是遥控器半睡。退避 {delay:.0f} 秒再试；这期间按一下遥控器任意键会立刻重试。",
                        f"Voice worker: a fresh process still got no ATVV ({self._last_why}) — so this "
                        f"is not a stale snapshot but the remote half-asleep. Backing off {delay:.0f}s; "
                        f"pressing any remote key retries immediately."))
            else:
                # 不是"4 个服务"导致的退出（崩溃 / 依赖问题 / 主动 stop）
                self._recycles = 0
                delay = 3.0
                self._log(i18n.L(
                    f"语音worker：进程退出（code {rc}{'，' + self._last_why if self._last_why else ''}）"
                    f"—— {delay:.0f} 秒后重启",
                    f"Voice worker: process exited (code {rc}"
                    f"{', ' + self._last_why if self._last_why else ''}) — restarting in {delay:.0f}s"))

            # 退避等待必须**可被按键打断**：用户按下遥控器的那一刻就是遥控器醒着
            # 的唯一窗口，等满 60/120/300 秒再重连等于又错过一次。
            deadline = time.time() + delay
            woke_by_key = False
            while not self._halt.is_set() and time.time() < deadline:
                if self._poke_evt.wait(0.2):
                    self._poke_evt.clear()
                    woke_by_key = True
                    break
            if self._halt.is_set():
                break
            if woke_by_key:
                self._log(i18n.L(
                    "语音worker：遥控器有按键活动 —— 打断退避，立刻重建连接进程",
                    "Voice worker: remote key activity — cutting the backoff short and "
                    "respawn immediately"))
