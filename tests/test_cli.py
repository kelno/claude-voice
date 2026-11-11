from claude_voice.cli import COMMANDS, main


class Recorder:
    def __init__(self):
        self.configs = []

    def __call__(self, cfg):
        self.configs.append(cfg)


def env(tmp_path, **extra):
    return {"CLAUDE_VOICE_ROOT": str(tmp_path), **extra}


def test_the_command_has_serve_check_plugins_passkey_agent_machines_and_permissions():
    assert COMMANDS == ("serve", "check", "plugins", "passkey", "agent", "machines", "permissions")


def test_serve_starts_the_bridge_with_the_chosen_transport(tmp_path):
    serve = Recorder()
    code = main(["serve", "--transport", "stdio"], env=env(tmp_path), serve=serve)
    assert code == 0
    assert serve.configs[0].transport == "stdio"


def test_no_verb_means_serve_so_existing_service_files_keep_working(tmp_path):
    serve = Recorder()
    token = {"CLAUDE_VOICE_TOKEN": "x" * 32}
    main(["--transport", "http"], env=env(tmp_path, **token), serve=serve)
    assert serve.configs[0].transport == "http"


def signed_in():
    return True, "signed in to claude.ai"


def signed_out():
    return False, "Claude Code is not signed in"


def test_check_reports_a_usable_configuration_without_serving(tmp_path, capsys):
    serve = Recorder()
    code = main(["check"], env=env(tmp_path), serve=serve, login=signed_in)
    assert code == 0
    assert serve.configs == []
    assert capsys.readouterr().out.strip() == (f"claude-voice: ready to serve over stdio; projects under {tmp_path}")


def test_check_refuses_when_claude_code_is_not_signed_in_and_says_how_to_sign_in(tmp_path, capsys):
    code = main(["check"], env=env(tmp_path), serve=Recorder(), login=signed_out)
    assert code == 1
    err = capsys.readouterr().err
    assert "Claude Code is not signed in" in err and "/login" in err


def test_a_missing_project_folder_says_how_to_make_or_choose_one(tmp_path, capsys):
    missing = tmp_path / "nowhere"
    code = main(["check"], env={"CLAUDE_VOICE_ROOT": str(missing)}, serve=Recorder(), login=signed_in)
    assert code == 1
    err = capsys.readouterr().err
    assert f"mkdir -p {missing}" in err and "CLAUDE_VOICE_ROOT" in err


def test_a_short_or_missing_secret_says_how_to_make_one(tmp_path, capsys):
    main(["check", "--transport", "http"], env=env(tmp_path), serve=Recorder(), login=signed_in)
    assert "secrets.token_urlsafe(32)" in capsys.readouterr().err


def test_check_can_answer_in_json_for_an_assistant_doing_the_setup(tmp_path, capsys):
    import json

    code = main(["check", "--json", "--transport", "http"], env=env(tmp_path), serve=Recorder(), login=signed_out)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ready"] is False
    by_name = {c["name"]: c for c in out["checks"]}
    assert by_name["signed_in"]["ok"] is False and "/login" in by_name["signed_in"]["fix"]
    assert by_name["secret"]["ok"] is False
    assert by_name["project_root"]["ok"] is True
    assert by_name["public_address"]["ok"] is False and by_name["public_address"]["required"] is False


def test_help_lists_every_command(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--help"], env={}, serve=Recorder(), login=signed_in)
    out = capsys.readouterr().out
    assert all(name in out for name in COMMANDS)


def test_a_bad_configuration_is_one_line_and_exit_code_one(tmp_path, capsys):
    code = main(["check"], env=env(tmp_path, ANTHROPIC_API_KEY="sk-x"), serve=Recorder(), login=signed_in)
    assert code == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith("claude-voice: ANTHROPIC_API_KEY is set")
    assert "\n" not in err


def test_a_passkey_is_added_from_a_one_time_link_made_at_the_pc(tmp_path, capsys):
    from claude_voice import passkeys
    from claude_voice.store import Store

    settings = env(
        tmp_path, CLAUDE_VOICE_DB=str(tmp_path / "b.db"), CLAUDE_VOICE_PUBLIC_URL="https://pc.example.com/cv"
    )
    assert main(["passkey", "add"], env=settings) == 0
    link = next(w for w in capsys.readouterr().out.split() if w.startswith("https://"))
    assert link.startswith("https://pc.example.com/cv/confirm/passkey/")
    book = passkeys.for_url(Store(tmp_path / "b.db"), "https://pc.example.com/cv")
    book.check_registration(link.rsplit("/", 1)[1])  # a live, unused link
    assert main(["passkey", "list"], env=settings) == 0
    assert "No passkey" in capsys.readouterr().out
    assert main(["passkey", "remove", "nope"], env=settings) == 1


def test_the_passkey_command_works_without_the_folder_of_work_folders(tmp_path, capsys):
    """It keeps passkeys in the database; the root is only the bridge's, and may be unset in a plain shell."""
    settings = {"CLAUDE_VOICE_ROOT": str(tmp_path / "missing"), "CLAUDE_VOICE_DB": str(tmp_path / "b.db")}
    assert main(["passkey", "list"], env=settings) == 0
    assert "No passkey" in capsys.readouterr().out
    assert main(["passkey", "remove", "nope"], env=settings) == 1
    assert capsys.readouterr().err.startswith("claude-voice: no passkey 'nope'")


def test_a_passkey_link_is_never_made_for_an_address_nobody_set(tmp_path, capsys):
    """A passkey is bound to the link's host: one made for the fallback address fails on the real page."""
    settings = env(tmp_path, CLAUDE_VOICE_DB=str(tmp_path / "b.db"))
    assert main(["passkey", "add"], env=settings) == 1
    out, err = capsys.readouterr()
    assert "/confirm/passkey/" not in out
    assert "CLAUDE_VOICE_PUBLIC_URL" in err and "http://127.0.0.1:8811" in err
    local = {**settings, "CLAUDE_VOICE_PUBLIC_URL": "http://127.0.0.1:8811"}
    assert main(["passkey", "add"], env=local) == 0  # set on purpose, for confirming at the PC
    assert "http://127.0.0.1:8811/confirm/passkey/" in capsys.readouterr().out
