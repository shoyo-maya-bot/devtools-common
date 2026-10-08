"""LLMClient / FakeLLM: record_path に書く応答の本文を伏せる・省く（record_content）. 通信はしない。アドレスは架空."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from devtools_common.llm import MASK, FakeLLM, Masker


def _msg(text):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _tool(data):
    m = _msg("")
    m["content"] = [{"type": "tool_use", "id": "toolu_1", "name": "record", "input": data}]
    m["stop_reason"] = "tool_use"
    return m


class RecordMaskTest(unittest.TestCase):
    """record_path に応答本文を伏せ字なしで書き出していた."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Path(self.tmp.name) / "r.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _lines(self):
        return [json.loads(x) for x in self.rec.read_text(encoding="utf-8").splitlines()]

    def test_masker_applied_to_record_by_default(self):
        fake = FakeLLM(
            [_msg("連絡先 hanako@example.org"), _tool({"who": "hanako@example.org", "n": 1})],
            record_path=self.rec,
            masker=Masker(),
        )
        self.assertIn("hanako@example.org", fake.complete("a").text)  # 戻り値は伏せない
        fake.complete("b")
        text = self.rec.read_text(encoding="utf-8")
        self.assertNotIn("hanako@example.org", text)
        lines = self._lines()
        self.assertEqual(lines[0]["content"][0]["text"], f"連絡先 {MASK}")
        self.assertEqual(lines[1]["content"][0]["input"], {"who": MASK, "n": 1})

    def test_record_content_omit(self):
        fake = FakeLLM([_msg("秘密の本文"), _tool({"who": "x"})], record_path=self.rec, record_content="omit")
        fake.complete("a")
        fake.complete("b")
        text = self.rec.read_text(encoding="utf-8")
        self.assertNotIn("秘密の本文", text)
        lines = self._lines()
        self.assertEqual(lines[0]["content"][0]["text"], "<text 5 chars>")
        self.assertEqual(lines[1]["content"][0]["input"], {})
        self.assertEqual(lines[0]["usage"], {"input_tokens": 1, "output_tokens": 1})

    def test_record_content_raw_keeps_body(self):
        fake = FakeLLM([_msg("a@example.org")], record_path=self.rec, masker=Masker(), record_content="raw")
        fake.complete("a")
        self.assertIn("a@example.org", self.rec.read_text(encoding="utf-8"))

    def test_record_content_auto_without_masker_is_raw(self):
        fake = FakeLLM([_msg("本文")], record_path=self.rec)
        fake.complete("a")
        self.assertEqual(self._lines()[0]["content"][0]["text"], "本文")

    def test_masked_without_masker_is_error(self):
        with self.assertRaises(ValueError):
            FakeLLM([], record_path=self.rec, record_content="masked")

    def test_unknown_mode_is_error(self):
        with self.assertRaises(ValueError):
            FakeLLM([], record_path=self.rec, record_content="secret")


if __name__ == "__main__":
    unittest.main()
