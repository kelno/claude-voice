"""Reading Claude Code's own transcript file, including messages that arrive mid-turn."""

import json

from claude_agent_sdk import project_key_for_directory

from claude_voice import transcripts
from claude_voice.transcripts import read_raw_transcript, recent_turns


def test_a_transcript_is_read_as_utf8_whatever_the_system_encoding(tmp_path):
    # Claude Code writes UTF-8. Windows reads text as cp1252 by default, where
    # the last byte of a closing quote (E2 80 9D) is not a character at all.
    cwd = "/home/someone/project"
    text = "Renamed “Café del Mar” – done ✓"
    path = tmp_path / project_key_for_directory(cwd) / "sid-1.jsonl"
    path.parent.mkdir(parents=True)
    entry = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    path.write_bytes((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))

    turns = recent_turns(read_raw_transcript("sid-1", cwd, projects_dir=tmp_path), None)
    assert [t["text"] for t in turns] == [text]


def test_peer_messages_that_arrive_mid_turn_are_kept_and_other_meta_entries_are_not(tmp_path):
    cwd = "/home/someone/project"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "Fix the export"}},
        {
            "type": "user",
            "isMeta": True,
            "message": {"role": "user", "content": "<system-reminder>noise</system-reminder>"},
        },
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Working."}]}},
        {
            "type": "user",
            "isMeta": True,
            "origin": {"kind": "peer", "name": "Roy via stemmen"},
            "message": {"role": "user", "content": "Another Claude session sent a message:\nWhich folder?"},
        },
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "subagent"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "In billing."}]}},
        {"type": "queue-operation", "operation": "enqueue", "content": "ignored"},
    ]
    path = tmp_path / project_key_for_directory(cwd) / "sid-1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines) + '{"type": "user", "trunc')

    turns = recent_turns(read_raw_transcript("sid-1", cwd, projects_dir=tmp_path), None)
    assert [(t["role"], t["text"]) for t in turns] == [
        ("user", "Fix the export"),
        ("assistant", "Working."),
        ("user", "Another Claude session sent a message:\nWhich folder?"),
        ("assistant", "In billing."),
    ]


def test_a_missing_transcript_reads_as_empty(tmp_path):
    assert read_raw_transcript("nope", "/x", projects_dir=tmp_path) == []


def write(path, entries, mode="w"):
    with path.open(mode, encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


def test_a_transcript_keeps_only_what_a_person_reads_not_tool_output(tmp_path):
    """Transcripts reach hundreds of megabytes, nearly all of it tool output nobody reads back."""
    path = tmp_path / project_key_for_directory("/w") / "s.jsonl"
    path.parent.mkdir(parents=True)
    huge = "x" * 200_000
    write(
        path,
        [
            {"type": "user", "message": {"content": "Run the tests"}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": huge}}]},
            },
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": huge}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "All green."}]}},
        ],
    )
    msgs = transcripts.read_raw_transcript("s", "/w", tmp_path)
    assert huge not in json.dumps([m.message for m in msgs])
    assert transcripts.recent_turns(msgs, None) == [
        {"role": "user", "text": "Run the tests"},
        {"role": "assistant", "text": "All green.", "tools": ["Bash"]},
    ]


def test_a_growing_transcript_is_read_from_where_it_left_off(tmp_path):
    path = tmp_path / project_key_for_directory("/w") / "g.jsonl"
    path.parent.mkdir(parents=True)
    write(path, [{"type": "user", "message": {"content": "one"}}])
    assert [m.message["content"] for m in transcripts.read_raw_transcript("g", "/w", tmp_path)] == ["one"]
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "two"}}) + "\n")
        f.write('{"type": "user", "message": {"content": "thr')  # still being written
    assert [m.message["content"] for m in transcripts.read_raw_transcript("g", "/w", tmp_path)] == ["one", "two"]
    with path.open("a", encoding="utf-8") as f:
        f.write('ee"}}\n')
    assert [m.message["content"] for m in transcripts.read_raw_transcript("g", "/w", tmp_path)] == [
        "one",
        "two",
        "three",
    ]


def test_a_model_claude_code_does_not_know_reads_as_a_clear_error_with_what_to_do(tmp_path):
    cwd = "/home/someone/project"
    text = "There's an issue with the selected model (opsu). It may not exist or you may not have access to it."
    entry = {
        "type": "assistant",
        "error": "model_not_found",
        "isApiErrorMessage": True,
        "message": {"role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": text}]},
    }
    path = tmp_path / project_key_for_directory(cwd) / "sid-1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    [turn] = recent_turns(read_raw_transcript("sid-1", cwd, projects_dir=tmp_path), None)
    assert turn["text"].startswith(text)
    assert "stop_active_session" in turn["text"] and "start_active_session" in turn["text"]


NOTE = "<task-notification><task-id>{}</task-id><status>completed</status></task-notification>"


def _task_lines(started_at="2026-11-11T23:04:06.725Z"):
    """A shell command and a helper agent started in the background; only the shell's note has come."""
    return [
        {"type": "user", "message": {"role": "user", "content": "sleep 90s, then say done"}},
        {
            "type": "user",
            "timestamp": started_at,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
            "toolUseResult": {"stdout": "", "backgroundTaskId": "bcwyg4e4s"},
        },
        {
            "type": "user",
            "timestamp": started_at,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "y"}]},
            "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "a694b47"},
        },
        {
            "type": "user",
            "timestamp": started_at,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "z"}]},
            "toolUseResult": {"status": "completed", "agentId": "a111"},  # a helper that ran in the turn
        },
        {
            "type": "user",
            "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": NOTE.format("bcwyg4e4s")},
        },
    ]


def _read(tmp_path, lines):
    path = tmp_path / project_key_for_directory("/w") / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return transcripts.read_raw_transcript("s", "/w", tmp_path)


def test_a_background_task_runs_until_its_completion_note(tmp_path):
    msgs = _read(tmp_path, _task_lines())
    assert transcripts.background_tasks(msgs) == ["a694b47"]
    assert transcripts.background_tasks(msgs[:2]) == ["bcwyg4e4s"]


def test_a_task_started_before_the_session_s_restart_is_not_running(tmp_path):
    # A restart ends what the session ran in the background, and no note comes for it.
    msgs = _read(tmp_path, _task_lines())
    restarted = 1794438247000  # 2026-11-11T23:04:07Z, in milliseconds, as the registry keeps it
    assert transcripts.background_tasks(msgs, since_ms=restarted) == []
    assert transcripts.background_tasks(msgs, since_ms=restarted - 5000) == ["a694b47"]


def test_a_completion_note_counts_in_every_form_claude_code_records_it(tmp_path):
    # From the command line, the note comes as a queued command, not as a turn of its own.
    lines = _task_lines()[:3] + [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTE.format("bcwyg4e4s")},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": NOTE.format("a694b47")}},
    ]
    msgs = _read(tmp_path, lines)
    assert transcripts.background_tasks(msgs) == []
    assert [t["text"] for t in recent_turns(msgs, None)][0] == "sleep 90s, then say done"  # the notes are no turns
