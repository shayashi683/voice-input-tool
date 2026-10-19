import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from voice_input_tool import audio_devices


class AudioDeviceTests(unittest.TestCase):
    def device_list(self, devices):
        return lambda index=None: devices if index is None else devices[index]

    def test_named_microphone_survives_index_reuse(self):
        internal = {"name": "Internal Mic", "max_input_channels": 1}
        mv7 = {"name": "Shure MV7+", "max_input_channels": 2}
        with (
            patch.object(audio_devices, "refresh_audio_devices"),
            patch.object(audio_devices.sd, "query_devices", self.device_list([internal, mv7])),
        ):
            self.assertEqual(audio_devices.resolve_input_device("name:Shure MV7+"), 1)
        with (
            patch.object(audio_devices, "refresh_audio_devices"),
            patch.object(audio_devices.sd, "query_devices", self.device_list([mv7, internal])),
        ):
            self.assertEqual(audio_devices.resolve_input_device("name:Shure MV7+"), 0)

    def test_disconnected_preferred_mic_uses_default_until_reconnected(self):
        devices = [{"name": "Internal Mic", "max_input_channels": 1}]
        with (
            patch.object(audio_devices, "refresh_audio_devices"),
            patch.object(audio_devices.sd, "query_devices", self.device_list(devices)),
            patch.object(audio_devices.sd, "default", SimpleNamespace(device=(0, 0))),
        ):
            self.assertEqual(audio_devices.resolve_input_device("name:Shure MV7+"), 0)
            devices.append({"name": "Shure MV7+", "max_input_channels": 2})
            self.assertEqual(audio_devices.resolve_input_device("name:Shure MV7+"), 1)

    def test_migration_and_settings_use_names_without_refreshing_open_streams(self):
        devices = [{"name": "Shure MV7+", "max_input_channels": 2}]
        with (
            patch.object(audio_devices, "refresh_audio_devices") as refresh,
            patch.object(audio_devices.sd, "query_devices", self.device_list(devices)),
        ):
            self.assertEqual(audio_devices.stable_input_device_id("0"), "name:Shure MV7+")
            self.assertEqual(audio_devices.stable_input_device_id("name:Missing Mic"), "name:Missing Mic")
            self.assertEqual(audio_devices.list_input_devices(),
                             [{"id": "name:Shure MV7+", "legacy_id": "0", "label": "Shure MV7+ (2ch)"}])
        refresh.assert_not_called()

    def test_no_inputs_returns_none(self):
        with (
            patch.object(audio_devices, "refresh_audio_devices"),
            patch.object(audio_devices.sd, "query_devices", self.device_list([])),
            patch.object(audio_devices.sd, "default", SimpleNamespace(device=(-1, -1))),
        ):
            self.assertIsNone(audio_devices.resolve_input_device("name:Shure MV7+"))

    def test_topology_reads_hal_without_refreshing_portaudio(self):
        with (
            patch.object(audio_devices, "_core_audio", return_value=object()),
            patch.object(audio_devices, "_hardware_ids", side_effect=[(23, 51), (51,)]) as ids,
            patch.object(audio_devices, "refresh_audio_devices") as refresh,
        ):
            self.assertEqual(audio_devices.audio_device_topology(), ((23, 51), (51,)))
            self.assertEqual([call.args[1] for call in ids.call_args_list], ["dev#", "dIn "])
        refresh.assert_not_called()

    def test_topology_failure_leaves_watchdog_available(self):
        with patch.object(audio_devices, "_core_audio", side_effect=OSError("HAL unavailable")):
            self.assertIsNone(audio_devices.audio_device_topology())

    def test_resolve_refreshes_portaudio_before_selecting_device(self):
        events = []

        with (
            patch.object(audio_devices.sd, "_terminate", side_effect=lambda: events.append("terminate")),
            patch.object(audio_devices.sd, "_initialize", side_effect=lambda: events.append("initialize")),
            patch.object(
                audio_devices.sd,
                "query_devices",
                side_effect=lambda device_id: (
                    events.append(f"query:{device_id}")
                    or {"name": "USB Mic", "max_input_channels": 1}
                ),
            ),
        ):
            selected = audio_devices.resolve_input_device("5")

        self.assertEqual(selected, 5)
        self.assertEqual(events, ["terminate", "initialize", "query:5"])

    def test_refresh_failure_falls_back_to_normal_device_resolution(self):
        query_devices = Mock(return_value={"name": "USB Mic", "max_input_channels": 1})

        with (
            patch.object(audio_devices.sd, "_terminate", side_effect=RuntimeError("busy")),
            patch.object(audio_devices.sd, "_initialize") as initialize,
            patch.object(audio_devices.sd, "query_devices", query_devices),
        ):
            selected = audio_devices.resolve_input_device("7")

        self.assertEqual(selected, 7)
        initialize.assert_not_called()
        query_devices.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
