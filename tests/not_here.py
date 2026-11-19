"""Upstream's tests that can't run in the fork, each with why; `tests/conftest.py` skips them.

A test lands here only when what it tests is retired or switched off in a way its own switch
can't undo (a tool the fork removed, a parameter the fork's tools don't take), or when it can't
run on Windows, where the fork runs. UPSTREAM.md says the same for each feature.
`tests/test_not_here.py` fails when an entry names a test that no longer exists.

The fork's own; a file apart, so upstream's test files stay as upstream has them.
"""

MAILBOX = (
    "upstream's voice mailbox is off here: whats_new and session_recap are retired, and the"
    " fork's session tools don't take `ack` (UPSTREAM.md)"
)
OLD_SKILL = "the fork switches every upstream skill off by name, upstream's pointer write-plugin included"
POSIX_ONLY = "POSIX paths only: on Windows a backslash is a separator, so the folder isn't a child of tmp_path"

NOT_HERE = {
    "tests/test_api.py::test_the_phone_pages_stream_never_marks_a_reply_heard": MAILBOX,
    "tests/test_mailbox.py::test_whats_new_carries_unread_replies_even_without_a_cursor": MAILBOX,
    "tests/test_mailbox.py::test_whats_new_hands_over_each_reply_once": MAILBOX,
    "tests/test_mailbox.py::test_whats_new_says_when_replies_wait_unheard": MAILBOX,
    "tests/test_mailbox.py::test_a_held_reply_survives_the_cursor_of_an_answer_that_did_not_carry_it": MAILBOX,
    "tests/test_mailbox.py::test_with_two_voice_clients_a_reply_is_repeated_never_lost": MAILBOX,
    "tests/test_mailbox_sessions.py::test_waiting_on_a_session_brings_its_unheard_replies_and_no_one_else_s": MAILBOX,
    "tests/test_mailbox_sessions.py::test_a_reply_a_wait_handed_over_is_confirmed_by_its_number": MAILBOX,
    "tests/test_mailbox_sessions.py::test_asking_a_session_brings_its_unheard_replies": MAILBOX,
    "tests/test_mailbox_sessions.py::test_a_recap_brings_the_session_s_unheard_replies": MAILBOX,
    "tests/test_mailbox_sessions.py::test_an_ack_on_a_session_tool_marks_its_replies_heard": MAILBOX,
    "tests/test_mailbox_sessions.py::test_the_other_session_tools_take_an_ack_too": MAILBOX,
    "tests/test_mailbox_sessions.py::test_an_ack_with_an_unknown_or_read_id_is_ignored": MAILBOX,
    "tests/test_mailbox_sessions.py::test_an_ack_marks_only_the_replies_it_names": MAILBOX,
    "tests/test_mailbox_sessions.py::test_a_reply_from_a_session_under_an_older_name_still_reaches_whats_new": MAILBOX,
    "tests/test_plugin_skill.py::test_no_file_links_to_the_old_skill_after_the_move": OLD_SKILL,
    "tests/test_routing.py::test_a_home_folder_with_backslashes_is_read_as_it_is": POSIX_ONLY,
}
