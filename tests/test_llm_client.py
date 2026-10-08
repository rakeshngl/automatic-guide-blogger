import json

from guides_writer.agents.writer import ChecklistOnly, GuidePart2
from guides_writer.llm.client import LLMClient
from guides_writer.render.sample_data import SAMPLE_GUIDE


def _part2() -> GuidePart2:
    half = len(SAMPLE_GUIDE.phases) // 2 + len(SAMPLE_GUIDE.phases) % 2
    return GuidePart2(
        phases=list(SAMPLE_GUIDE.phases[half:]),
        checklist=list(SAMPLE_GUIDE.checklist),
        closing_alert=SAMPLE_GUIDE.closing_alert,
    )


def _good_part2_json() -> str:
    return json.dumps(_part2().model_dump(exclude_none=True, mode="json"))


def _bad_part2_json() -> str:
    good = _part2().model_dump(exclude_none=True)
    rows = list(good["checklist"])
    rows[0] = ""
    rows[1] = {"check": rows[1]["check"]}
    good["checklist"] = rows
    return json.dumps(good)


def _good_checklist_only_json() -> str:
    rows = _part2().checklist
    return json.dumps({"checklist": [r.model_dump(mode="json") for r in rows]})


def _all_text(messages: list[dict]) -> str:
    return " ".join(str(m.get("content") or "") for m in messages)


class ScriptedLLM(LLMClient):
    def __init__(self, script: list[str]):
        super().__init__(
            api_key="k", base_url="https://groq.com/v1", model="test-model",
            tokens_per_minute=0,
        )
        self.sent: list[list[dict]] = []
        self.script = list(script)

    def chat(self, messages, temperature=0.7, max_tokens=None,
             json_response=False, reasoning_effort="low"):
        self.sent.append(list(messages))
        return self.script.pop(0)


def _write(client: ScriptedLLM) -> GuidePart2:
    return client.chat_json(
        [{"role": "user", "content": "write part 2"}],
        schema=GuidePart2,
        repair_field="checklist",
        repair_schema=ChecklistOnly,
    )


class TestTargetedFieldRepair:
    def test_merges_targeted_checklist_repair(self):
        client = ScriptedLLM([_bad_part2_json(), _good_checklist_only_json()])
        out = _write(client)
        assert isinstance(out, GuidePart2)
        assert len(out.checklist) == len(_part2().checklist)
        assert all(row.command_why for row in out.checklist)
        assert len(out.phases) == len(_part2().phases)
        assert (
            out.model_dump(exclude_none=True)["closing_alert"]
            == _part2().model_dump(exclude_none=True)["closing_alert"]
        )
        assert len(client.sent) == 2
        assert 'Only the "checklist" field failed validation' in _all_text(client.sent[1])

    def test_falls_back_to_generic_repair(self):
        client = ScriptedLLM([_bad_part2_json(), '{"checklist": ["still bad"]}', _good_part2_json()])
        out = _write(client)
        assert len(out.checklist) == len(_part2().checklist)
        assert len(client.sent) == 3
        assert "Your previous JSON reply failed validation" in _all_text(client.sent[2])

    def test_skips_targeted_when_other_fields_bad(self):
        bad = json.loads(_bad_part2_json())
        bad["phases"][0]["title"] = 7
        client = ScriptedLLM([json.dumps(bad), _good_part2_json()])
        out = _write(client)
        assert isinstance(out, GuidePart2)
        assert len(client.sent) == 2
        assert 'Only the "checklist" field failed validation' not in _all_text(client.sent[1])
