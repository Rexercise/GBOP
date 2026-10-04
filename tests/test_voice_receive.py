"""Synthetic packet regressions; no Discord connection or recorded audio."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


class ReceiveTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'bot.py'
        tree = ast.parse(source.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('_gbop_process_packet', '_gbop_rtp_payload')]
        stats = next(n for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'GBOP_RX_STATS' for t in n.targets))
        self.session = NS(ready=True, decrypt=Mock(return_value=b'opus'))
        self.decoder = NS(_cached_id=42, _get_cached_member=lambda: 'member',
            _decoder=NS(decode=Mock(return_value=b'pcm')),
            sink=NS(wants_opus=lambda: False, voice_client=NS(
                _connection=NS(dave_session=self.session, dave_protocol_version=1))))
        self.ns = dict(discord=NS(opus=NS(OpusError=RuntimeError)),
            davey=NS(MediaType=NS(audio='audio')), _gbop_rx_note=Mock(),
            gbop_recv_opus=NS(VoiceData=lambda packet, member, pcm: NS(packet=packet, source=member, pcm=pcm)))
        exec(compile(ast.Module(body=[stats, *nodes], type_ignores=[]), str(source), 'exec'), self.ns)

    def process(self, data, padding=False):
        packet = NS(decrypted_data=data, padding=padding, sequence=7, timestamp=960)
        return self.ns['_gbop_process_packet'](self.decoder, packet)

    def test_padded_dave_frame_is_unpadded_before_authentication(self):
        self.process(b'encrypted-frame\x00\x00\x03', True)
        self.session.decrypt.assert_called_once_with(42, 'audio', b'encrypted-frame')
        self.decoder._decoder.decode.assert_called_once_with(b'opus', fec=False)

    def test_padded_silence_bypasses_dave_like_unpadded_silence(self):
        result = self.process(b'\xf8\xff\xfe\x00\x02', True)
        self.session.decrypt.assert_not_called()
        self.decoder._decoder.decode.assert_called_once_with(b'\xf8\xff\xfe', fec=False)
        self.assertEqual(result.pcm, b'pcm')

    def test_malformed_padding_is_concealed_without_decrypt_or_decode(self):
        for payload in (b'frame\x00', b'frame\xff', b''):
            with self.subTest(payload=payload):
                result = self.process(payload, True)
                self.assertEqual(result.pcm, bytes(3840))
        self.session.decrypt.assert_not_called()
        self.decoder._decoder.decode.assert_not_called()
        self.assertEqual(self.ns['GBOP_RX_STATS']['padding_errors'], 3)
        self.assertEqual((self.decoder._last_seq, self.decoder._last_ts), (7, 960))

    def test_padding_only_packet_is_concealed(self):
        self.assertEqual(self.process(b'\x00\x02', True).pcm, bytes(3840))
        self.session.decrypt.assert_not_called()
        self.decoder._decoder.decode.assert_not_called()

    def test_unflagged_payload_is_never_trimmed(self):
        for payload in (b'frame\x01', b'frame\x00', b'frame\xff'):
            self.process(payload)
            self.session.decrypt.assert_called_with(42, 'audio', payload)

    def test_plain_opus_padding_uses_same_path(self):
        self.decoder.sink.voice_client._connection.dave_protocol_version = 0
        self.process(b'opus\x01', True)
        self.decoder._decoder.decode.assert_called_once_with(b'opus', fec=False)
        self.session.decrypt.assert_not_called()

    def test_bad_frame_does_not_prevent_next_good_frame(self):
        self.session.decrypt.side_effect = [ValueError('authentication failed'), b'opus']
        self.assertEqual(self.process(b'bad').pcm, bytes(3840))
        self.assertEqual(self.process(b'good\x01', True).pcm, b'pcm')
        self.assertEqual(self.ns['GBOP_RX_STATS']['dave_errors'], 1)

    def test_opus_sink_receives_only_authenticated_unpadded_frame(self):
        self.decoder.sink.wants_opus = lambda: True
        self.assertIsNone(self.process(b'frame\x00', True))
        result = self.process(b'frame\x01', True)
        self.assertEqual(result.packet.decrypted_data, b'opus')
        self.session.decrypt.assert_called_once_with(42, 'audio', b'frame')
        self.decoder._decoder.decode.assert_not_called()

    def test_missing_keys_and_sender_still_fail_closed(self):
        self.session.ready = False
        self.assertEqual(self.process(b'frame\x01', True).pcm, bytes(3840))
        self.session.ready = True
        self.decoder._cached_id = None
        self.assertEqual(self.process(b'frame\x01', True).pcm, bytes(3840))
        self.session.decrypt.assert_not_called()
        self.decoder._decoder.decode.assert_not_called()

    def test_maximum_padding_count_and_bytearray_input(self):
        self.process(bytearray(b'frame' + bytes(254) + b'\xff'), True)
        self.session.decrypt.assert_called_once_with(42, 'audio', b'frame')


if __name__ == '__main__':
    unittest.main()
