"""语音会话状态和尾音交接的回归测试；不访问蓝牙或注入键盘。"""
import asyncio
import pathlib
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import voice


class VoiceReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.events = []
        self.patches = [
            patch.object(voice.keys, "key_down", lambda key: self.actions.append(("down", key))),
            patch.object(voice.keys, "key_up", lambda key: self.actions.append(("up", key))),
        ]
        for p in self.patches:
            p.start()
        self.role = voice.VoiceRole(lambda kind, obj: self.events.append((kind, obj)),
                                    voice_spec={"mode": "hold", "key": "LWIN",
                                                "mods": ["CTRL"], "tail_ms": 120})

    def tearDown(self):
        self.role.stop_flag.set()
        self.role._reset_voice_session()
        for p in reversed(self.patches):
            p.stop()

    def test_short_and_long_start_immediately_and_hold_after_release(self):
        for duration in (0.01, 0.25):
            self.role._on_status(b"\x04")
            self.assertTrue(self.role.voice.down)
            self.assertEqual(self.actions[-1][0], "down")
            time.sleep(duration)
            self.role._on_status(b"\x00")
            self.assertTrue(self.role.voice.down)
            time.sleep(0.17)
            self.assertFalse(self.role.voice.down)

    def test_second_press_cancels_old_release(self):
        self.role._on_status(b"\x04")
        self.role._on_status(b"\x00")
        time.sleep(0.06)
        self.role._on_status(b"\x04")
        time.sleep(0.10)
        self.assertTrue(self.role.voice.down)
        self.assertFalse(any(action == "up" for action, _ in self.actions))
        self.role._on_status(b"\x00")
        time.sleep(0.17)
        self.assertFalse(self.role.voice.down)

    def test_disconnect_clears_stale_state_and_accepts_next_press(self):
        self.role._on_status(b"\x04")
        self.role._reset_voice_session()
        self.assertFalse(self.role._active)
        self.assertFalse(self.role.voice.down)
        self.role._on_status(b"\x04")
        self.assertTrue(self.role.voice.down)
        self.assertEqual(self.role.sessions, 2)
        self.assertIn(("key", {"id": "voice", "down": False}), self.events)

    def test_short_zero_frame_session_does_not_trigger_reconnect(self):
        self.role._on_status(b"\x04")
        # 结束通知可能延迟，短按也可能表现为约一秒的零帧会话。
        self.role._session_t0 -= 1.1
        self.role._on_status(b"\x00")
        self.assertFalse(self.role._reconnect_now)

    def test_new_press_cancels_pending_zero_frame_reconnect(self):
        self.role._on_status(b"\x04")
        self.role._session_t0 -= 2.1
        self.role._on_status(b"\x00")
        self.assertTrue(self.role._reconnect_now)
        self.role._on_status(b"\x04")
        self.assertFalse(self.role._reconnect_now)
        self.assertTrue(self.role.voice.down)

    def test_setting_change_releases_old_shortcut(self):
        self.role._on_status(b"\x04")
        self.role.set_voice_spec({"mode": "hold", "key": "RALT", "mods": []})
        self.assertFalse(self.role.voice.down)
        self.assertEqual([a for a, _ in self.actions], ["down", "down", "up", "up"])

    def test_partial_final_audio_block_is_played(self):
        sink = voice.Sink(device=0)
        sink.buf.extend(b"\x01\x02" * 30)
        written = []

        class Stream:
            def write(self, data):
                written.append(data)
                sink.run_flag.clear()

        sink.stream = Stream()
        sink.run_flag.set()
        sink._pump()
        self.assertEqual(written[0][:60], b"\x01\x02" * 30)
        self.assertEqual(len(written[0]), int(voice.SR_OUT * 0.02) * 4)
        self.assertEqual(len(sink.buf), 0)

    def test_only_real_disconnected_key_activity_interrupts_backoff(self):
        self.role.poke("watchdog")
        self.assertFalse(self.role._poke.is_set())
        self.role.connected = True
        self.role.poke("key")
        self.assertFalse(self.role._poke.is_set())
        self.role.connected = False
        self.role.poke("key")
        self.assertTrue(self.role._poke.is_set())


class ConnectionCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_established_disconnect_gets_one_fast_retry(self):
        role = voice.VoiceRole(lambda *_: None, addr="11:22:33:44:55:66")
        calls = []

        async def session():
            calls.append(time.monotonic())
            if len(calls) == 1:
                role._session_ok = True
            else:
                role.stop_flag.set()

        role._session = session
        await role._loop_sessions()
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1] - calls[0], 0.45)
        self.assertLess(calls[1] - calls[0], 1.2)

    async def test_late_connection_is_closed_before_next_connect(self):
        role = voice.VoiceRole(lambda *_: None)
        finish_connect = asyncio.Event()
        finish_disconnect = asyncio.Event()
        disconnect_started = asyncio.Event()

        class Client:
            is_connected = False

            async def connect(self):
                await finish_connect.wait()
                self.is_connected = True

            async def disconnect(self):
                disconnect_started.set()
                await finish_disconnect.wait()
                self.is_connected = False

        client = Client()
        role._client = client
        self.assertFalse(await role._connect_wait(client, 0.001))
        finish_connect.set()
        await asyncio.wait_for(disconnect_started.wait(), 1)
        self.assertFalse(await role._pending_settled(0.001))
        finish_disconnect.set()
        self.assertTrue(await role._pending_settled(1))
        self.assertFalse(client.is_connected)

    async def test_subscription_failure_clears_connected_state_and_closes(self):
        role = voice.VoiceRole(lambda *_: None)
        role.connected = True
        role._active = True
        closed = []

        async def failed_session():
            raise RuntimeError("notification subscription failed")

        async def close():
            closed.append(True)
            role.stop_flag.set()

        role._session = failed_session
        role._close_current = close
        await role._loop_sessions()
        self.assertEqual(closed, [True])
        self.assertFalse(role.connected)
        self.assertFalse(role._active)


if __name__ == "__main__":
    unittest.main()
