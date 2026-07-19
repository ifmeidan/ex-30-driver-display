"""Tests for PID definitions and decoders."""

from obd2.pids import (
    PIDRegistry,
    PollGroup,
    ResearchStatus,
    BECM,
    VCFRONT,
    ECU_E,
    _extract_data,
    _decode_odometer,
    _decode_hv_voltage,
    _decode_hv_soh,
    _decode_soc_display,
    _decode_speed_f40d,
    _decode_voltage,
    _decode_hv_current,
    _decode_brake_multi,
    _make_raw_decoder,
    _make_packed_decoder,
)


class TestOdometerDecoder:
    def test_known_value(self):
        assert _decode_odometer("62DD01000940") == 2368.0

    def test_snoop_value(self):
        assert _decode_odometer("62DD010009D1") == 2513.0

    def test_with_spaces(self):
        assert _decode_odometer("62 DD 01 00 09 D1") == 2513.0

    def test_invalid(self):
        assert _decode_odometer("NO DATA") is None

    def test_empty(self):
        assert _decode_odometer("") is None


class TestHvVoltageDecoder:
    def test_snoop_value(self):
        assert _decode_hv_voltage("624801A73A") == 428.10

    def test_lower_value(self):
        assert abs(_decode_hv_voltage("624801A72F") - 427.99) < 0.01

    def test_invalid(self):
        assert _decode_hv_voltage("NO DATA") is None


class TestHvSohDecoder:
    def test_full_health(self):
        assert _decode_hv_soh("62496D00002710") == 100.0

    def test_invalid(self):
        assert _decode_hv_soh("NO DATA") is None


class TestSocDisplayDecoder:
    def test_full(self):
        assert _decode_soc_display("62D90164") == 100.0

    def test_half(self):
        assert _decode_soc_display("62D90132") == 50.0

    def test_invalid(self):
        assert _decode_soc_display("") is None


class TestSpeedDecoder:
    def test_stationary(self):
        assert _decode_speed_f40d("62F40D00") == 0.0

    def test_highway(self):
        assert _decode_speed_f40d("62F40D78") == 120.0

    def test_max(self):
        assert _decode_speed_f40d("62F40DFF") == 255.0

    def test_invalid(self):
        assert _decode_speed_f40d("NO DATA") is None

    def test_empty(self):
        assert _decode_speed_f40d("") is None


class TestVoltageDecoder:
    def test_normal_voltage(self):
        assert _decode_voltage("12.6V") == 12.6

    def test_without_v_suffix(self):
        assert _decode_voltage("12.6") == 12.6

    def test_low_voltage(self):
        assert _decode_voltage("11.2V") == 11.2

    def test_invalid(self):
        assert _decode_voltage("ERROR") is None


class TestHvCurrentDecoder:
    def test_at_rest(self):
        # raw=0x4000=16384, (16384-16384)*0.1 = 0.0A
        assert _decode_hv_current("6248024000") == 0.0

    def test_discharge(self):
        # raw=0x4852=18514, (18514-16384)*0.1 = 213.0A
        assert abs(_decode_hv_current("6248024852") - 213.0) < 0.1

    def test_regen(self):
        # raw=0x3D9C=15772, (15772-16384)*0.1 = -61.2A
        assert abs(_decode_hv_current("6248023D9C") - (-61.2)) < 0.1

    def test_invalid(self):
        assert _decode_hv_current("NO DATA") is None


class TestHVBattTempDecoders:
    """491B avg / 4945 max — confirmed via the reference scanner app snoop 2026-07-16."""

    def test_avg_snoop_value(self, pid_registry):
        # 0x1FD1 = 8145 → 8145/100 - 50 = 31.45°C (exact reference-app match)
        decoder = pid_registry.get("hv_batt_temp_avg").decoder
        assert abs(decoder("62491B1FD1") - 31.45) < 1e-9

    def test_avg_april_cold(self, pid_registry):
        # 0x19C3 = 6595 → 15.95°C (April snoop, cold battery)
        decoder = pid_registry.get("hv_batt_temp_avg").decoder
        assert abs(decoder("62491B19C3") - 15.95) < 1e-9

    def test_max_snoop_value(self, pid_registry):
        # 11 2026: byte0 = sensor index 17, u16 0x2026 = 8230 → 32.30°C
        decoder = pid_registry.get("hv_batt_temp_max").decoder
        assert abs(decoder("624945112026") - 32.30) < 1e-9

    def test_invalid(self, pid_registry):
        assert pid_registry.get("hv_batt_temp_avg").decoder("NO DATA") is None
        assert pid_registry.get("hv_batt_temp_max").decoder("") is None


class TestBrakeMultiDecoder:
    """Multi-DID read 22 FD00 FD01 FD02 FD03 — one round trip, 4 channels."""

    def test_multiframe_response(self):
        # 17 data bytes -> ISO-TP multi-frame, ELM prints length line + 0:/1:/2:
        raw = "011\r0: 62 FD 00 08 13 FD\r1: 01 08 12 FD 02 08 14\r2: FD 03 08 13"
        # channels: 2067, 2066, 2068, 2067 raw -> avg 2067 -> 20.67 bar
        assert abs(_decode_brake_multi(raw) - 20.67) < 1e-9

    def test_no_spaces_variant(self):
        raw = "011\r0:62FD000813FD\r1:010812FD020814\r2:FD030813"
        assert abs(_decode_brake_multi(raw) - 20.67) < 1e-9

    def test_zero_pressure(self):
        raw = "011\r0:62FD000000FD\r1:010000FD020000\r2:FD030000"
        assert _decode_brake_multi(raw) == 0.0

    def test_partial_echo_returns_none(self):
        # ECU answered only FD00 — multi-DID unsupported, must trigger fallback
        assert _decode_brake_multi("62FD000813") is None

    def test_negative_response_returns_none(self):
        assert _decode_brake_multi("7F2231") is None

    def test_garbage_values_rejected(self):
        raw = "011\r0:62FD00FFFFFD\r1:01FFFFFD02FFFF\r2:FD03FFFF"
        assert _decode_brake_multi(raw) is None


class TestExtractDataMultiFrame:
    """Tests for multi-frame ISO-TP response parsing."""

    def test_single_frame(self):
        assert _extract_data("62DD01000940", "DD01") == "000940"

    def test_with_spaces(self):
        assert _extract_data("62 DD 01 00 09 40", "DD01") == "000940"

    def test_multi_frame_with_separators(self):
        # Simulates ELM327 multi-frame response
        raw = "0: 62 DD 01 00 09\n1: 40 AA BB CC DD"
        result = _extract_data(raw, "DD01")
        assert result == "000940AABBCCDD"

    def test_multi_frame_no_spaces(self):
        raw = "0:62DD010009\n1:40AABBCCDD"
        result = _extract_data(raw, "DD01")
        assert result == "000940AABBCCDD"

    def test_no_match(self):
        assert _extract_data("62ABCD1234", "DD01") is None

    def test_empty(self):
        assert _extract_data("", "DD01") is None


class TestMakeRawDecoder:
    def test_unsigned_2byte(self):
        dec = _make_raw_decoder("FD00", num_bytes=2, signed=False)
        assert dec("62FD001770") == 6000.0  # 0x1770 = 6000

    def test_signed_2byte_positive(self):
        dec = _make_raw_decoder("FD01", num_bytes=2, signed=True)
        assert dec("62FD0100C8") == 200.0

    def test_signed_2byte_negative(self):
        dec = _make_raw_decoder("FD01", num_bytes=2, signed=True)
        assert dec("62FD01FF38") == -200.0  # 0xFF38 = -200

    def test_with_scale(self):
        dec = _make_raw_decoder("FD02", num_bytes=2, signed=False, scale=0.1)
        assert abs(dec("62FD0203E8") - 100.0) < 0.01  # 0x03E8=1000, ×0.1=100

    def test_with_offset(self):
        dec = _make_raw_decoder("F40D", num_bytes=1, signed=False, offset=-40.0)
        assert dec("62F40D64") == 60.0  # 0x64=100, 100-40=60

    def test_invalid(self):
        dec = _make_raw_decoder("FD00", num_bytes=2)
        assert dec("NO DATA") is None

    def test_empty(self):
        dec = _make_raw_decoder("FD00", num_bytes=2)
        assert dec("") is None

    def test_short_data(self):
        dec = _make_raw_decoder("FD00", num_bytes=2)
        assert dec("62FD00AB") is None  # only 1 byte, need 2


class TestMakePackedDecoder:
    def test_first_byte(self):
        dec = _make_packed_decoder("4945", byte_offset=0, num_bytes=1)
        # 8 data bytes: 11 19 11 19 00 00 00 00
        assert dec("62494511191119" + "00000000") == 17.0  # 0x11

    def test_second_byte(self):
        dec = _make_packed_decoder("4945", byte_offset=1, num_bytes=1)
        assert dec("62494511191119" + "00000000") == 25.0  # 0x19

    def test_third_byte(self):
        dec = _make_packed_decoder("4945", byte_offset=2, num_bytes=1)
        assert dec("62494511191119" + "00000000") == 17.0  # 0x11

    def test_fourth_byte(self):
        dec = _make_packed_decoder("4945", byte_offset=3, num_bytes=1)
        assert dec("62494511191119" + "00000000") == 25.0  # 0x19

    def test_2byte_at_offset(self):
        dec = _make_packed_decoder("4801", byte_offset=0, num_bytes=2)
        assert dec("6248010100020003000400") == 256.0  # 0x0100

    def test_2byte_at_offset_2(self):
        dec = _make_packed_decoder("4801", byte_offset=2, num_bytes=2)
        assert dec("6248010100020003000400") == 512.0  # 0x0200

    def test_invalid(self):
        dec = _make_packed_decoder("4945", byte_offset=0, num_bytes=1)
        assert dec("NO DATA") is None

    def test_short_data(self):
        dec = _make_packed_decoder("4945", byte_offset=3, num_bytes=1)
        assert dec("624945111911") is None  # only 3 bytes, need offset 3


class TestPIDRegistry:
    def test_confirmed_pids_registered(self, pid_registry):
        assert pid_registry.get("odometer") is not None
        assert pid_registry.get("voltage_12v") is not None
        assert pid_registry.get("hv_voltage") is not None
        assert pid_registry.get("hv_soh") is not None
        assert pid_registry.get("soc_display") is not None
        assert pid_registry.get("hv_current") is not None
        assert pid_registry.get("hv_batt_temp_avg") is not None
        assert pid_registry.get("hv_batt_temp_max") is not None

    def test_candidate_pids_registered(self, pid_registry):
        assert pid_registry.get("speed") is not None
        assert pid_registry.get("brake_pressure") is not None
        assert pid_registry.get("brake_pressure_b") is not None
        assert pid_registry.get("brake_pressure_c") is not None
        assert pid_registry.get("brake_pressure_d") is not None
        assert pid_registry.get("wheel_speed_fl") is not None
        assert pid_registry.get("wheel_speed_rr") is not None

    def test_brake_pids_removed(self, pid_registry):
        assert pid_registry.get("brake_temp_fl") is None
        assert pid_registry.get("brake_temp_rr") is None
        assert pid_registry.get("brake_pressure_fl") is None
        assert pid_registry.get("brake_pressure_rr") is None

    def test_hv_current_is_confirmed_4802(self, pid_registry):
        hv = pid_registry.get("hv_current")
        assert hv.status == ResearchStatus.CONFIRMED
        assert hv.command == "224802"
        assert hv.ecu == BECM

    def test_soc_display_on_vcfront(self, pid_registry):
        soc = pid_registry.get("soc_display")
        assert soc.ecu == VCFRONT

    def test_ecu_context_assigned(self, pid_registry):
        assert pid_registry.get("odometer").ecu == BECM  # moved 2026-07-16
        assert pid_registry.get("speed").ecu == ECU_E
        assert pid_registry.get("voltage_12v").ecu is None  # ELM direct
        assert pid_registry.get("hv_batt_temp_avg").ecu == BECM
        assert pid_registry.get("brake_pressure").ecu == ECU_E
        assert pid_registry.get("wheel_speed_fl").ecu == ECU_E

    def test_get_by_ecu_becm(self, pid_registry):
        becm_pids = pid_registry.get_by_ecu(BECM)
        names = [p.name for p in becm_pids]
        assert "hv_voltage" in names
        assert "hv_soh" in names
        assert "hv_current" in names
        assert "hv_batt_temp_avg" in names
        assert "odometer" in names   # moved from 11-bit gateway 2026-07-16
        # soc_display moved to VCFRONT
        assert "soc_display" not in names

    def test_get_by_ecu_vcfront(self, pid_registry):
        vcfront_pids = pid_registry.get_by_ecu(VCFRONT)
        names = [p.name for p in vcfront_pids]
        assert "soc_display" in names

    def test_get_by_group_fast(self, pid_registry):
        """Only the active lanes are FAST after the 2026-07-16 cleanup."""
        fast = pid_registry.get_by_group(PollGroup.FAST)
        names = [p.name for p in fast]
        # the multi-DID brake read is the only FAST PID (latency fix 2026-07-16)
        assert names == ["brake_pressure_multi"]

    def test_none_group_pids_stay_registered(self, pid_registry):
        """NONE-lane PIDs remain queryable by scripts, just not polled."""
        for name in ("speed", "soc_display", "hv_batt_temp_max", "hv_soh"):
            pid = pid_registry.get(name)
            assert pid is not None
            assert pid.poll_group == PollGroup.NONE

    def test_ecu_contexts(self, pid_registry):
        ctxs = pid_registry.ecu_contexts()
        assert BECM in ctxs
        assert ECU_E in ctxs
        assert VCFRONT in ctxs

    def test_all_available_excludes_empty_commands(self, pid_registry):
        for pid in pid_registry.all_available():
            assert pid.command != ""

    def test_all_available_excludes_unavailable(self, pid_registry):
        for pid in pid_registry.all_available():
            assert pid.status != ResearchStatus.UNAVAILABLE

    def test_unknown_pid_returns_none(self, pid_registry):
        assert pid_registry.get("nonexistent") is None
