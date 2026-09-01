from pet.exploration_watchdog import (
    ExplorationWatchdog, JudgeVerdict, WatchdogClass, classify_event,
    make_fingerprint, parse_judge_result,
)


def rec(tool, step, target="", session="s1", args_key=""):
    return {"event": "tool/call", "tool": tool, "step": step,
            "target": target, "argsKey": args_key, "sessionId": session}


class Collector:
    def __init__(self, watchdog):
        self.warnings = []
        self.judges = []
        watchdog.warning.connect(lambda s, p: self.warnings.append((s, p)))
        watchdog.judge_required.connect(lambda s, p: self.judges.append((s, p)))


def feed(watchdog, items):
    for item in items:
        watchdog.feed_record("dsh", item)


def test_classification_and_fingerprint_are_target_aware():
    assert classify_event({"event": "web_search_begin", "target": "docs"}) is WatchdogClass.SEARCH_WEB
    assert classify_event({"event": "tool/call", "tool": "Grep", "target": "foo"}) is WatchdogClass.SEARCH_CODE
    assert classify_event({"event": "tool/call", "tool": "Glob", "target": "pet/*.py"}) is WatchdogClass.GLOB
    a = make_fingerprint(rec("Read", 1, "a.py"), WatchdogClass.READ, "a.py")
    b = make_fingerprint(rec("Read", 2, "b.py"), WatchdogClass.READ, "b.py")
    assert a != b


def test_parallel_calls_same_step_count_once_and_sessions_are_isolated():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    for i in range(3):
        watchdog.feed_record("dsh", rec("Search", 1, f"q{i}", "a"))
    feed(watchdog, [rec("Read", 2, "a.py", "a"), rec("Think", 3, "plan", "a")])
    assert not collector.judges
    feed(watchdog, [rec("Search", 1, "same", "b"), rec("Search", 2, "same", "b")])
    assert not any(session == "a" for session, _ in collector.judges)


def test_abnormal_repeated_search_reaches_judge():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    feed(watchdog, [
        rec("Search", 1, "DSH settings"), rec("Think", 2, "plan"),
        rec("Search", 3, "DSH settings"), rec("Think", 4, "plan"),
        rec("Search", 5, "DSH settings"), rec("Search", 6, "DSH settings"),
    ])
    assert collector.judges
    assert collector.judges[0][1]["risk"] >= 5


def test_pwsh_get_content_reads_are_exploration():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    assert classify_event({"event": "tool/call", "tool": "Pwsh",
                           "target": "Get-Content C:/test.txt"}) is WatchdogClass.READ
    assert classify_event({"event": "tool/call", "tool": "Pwsh",
                           "argsKey": "argv0:Get-Content"}) is WatchdogClass.READ
    for i in range(1, 6):
        watchdog.feed_record("dsh", rec("Pwsh", i, "C:/test.txt", args_key="argv0:Get-Content"))
    assert collector.judges or collector.warnings


def test_shell_detection_compares_executed_command_not_card_description():
    first = {
        "event": "tool/call", "tool": "Pwsh", "step": "1",
        "description": "Read test file 1st time",
        "command": '"READ #1: " + (Get-Content "$env:USERPROFILE\\same.txt")',
        "argsKey": "command,description,argv0:\"READ",
    }
    fifth = {
        **first, "step": "5", "description": "Read test file 5th time",
        "command": '"READ #5: " + (Get-Content "$env:USERPROFILE\\same.txt")',
    }
    other = {
        **first, "step": "6", "description": "Read another file",
        "command": '"READ: " + (Get-Content "$env:USERPROFILE\\other.txt")',
    }
    assert classify_event(first) is WatchdogClass.READ
    from pet.exploration_watchdog import _target
    assert _target(first, "Pwsh") == _target(fifth, "Pwsh")
    assert _target(first, "Pwsh") != _target(other, "Pwsh")
    assert make_fingerprint(first, WatchdogClass.READ, _target(first, "Pwsh")) == \
        make_fingerprint(fifth, WatchdogClass.READ, _target(fifth, "Pwsh"))
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    for number in range(1, 6):
        watchdog.feed_record("dsh", {
            **first, "step": "one-agent-step",
            "description": f"Read test file {number} time",
            "command": f'"READ #{number}: " + (Get-Content "$env:USERPROFILE\\same.txt")',
        })
    assert collector.warnings


def test_legacy_argv0_records_still_detect_a_repeated_read_burst():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    for number in range(1, 6):
        watchdog.feed_record("dsh", {
            "event": "tool/call", "tool": "pwsh", "step": "one-agent-step",
            "argsKey": 'command,description,argv0:"READ',
            "description": f"Read test file {number} time",
        })
    assert collector.warnings


def test_normal_diverse_reads_does_not_trigger():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    tools = [
        ("Read", "pet/chat/service.py"), ("Read", "pet/agent_link.py"),
        ("Think", "plan"), ("Read", "pet/context_menus/shared.py"),
        ("Grep", "execution_failed"), ("Think", "plan"),
        ("Read", "pet/chat/models.py"), ("Read", "pet/chat/providers.py"),
        ("Think", "plan"),
    ]
    feed(watchdog, [rec(tool, i + 1, target) for i, (tool, target) in enumerate(tools)])
    assert not collector.warnings
    assert not collector.judges


def test_alternating_targets_are_not_described_as_single_target():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    feed(watchdog, [rec("Read", i, "a.txt" if i % 2 else "b.txt") for i in range(1, 7)])
    payloads = [item[1] for item in collector.warnings + collector.judges]
    assert payloads
    reasons = " ".join(payloads[0]["reasons"])
    assert "target 单一" not in reasons
    assert "target 重复" not in reasons


def test_turn_end_clears_old_window_before_next_turn():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    feed(watchdog, [rec("Read", i, "same.txt") for i in range(1, 7)])
    watchdog.feed_record("dsh", {"event": "turn/end", "sessionId": "s1", "step": 7})
    feed(watchdog, [rec("Pwsh", 1, "new.txt")])
    assert collector.judges or collector.warnings
    # A single new step must not inherit the previous turn's risk window.
    assert len(collector.judges) == 0 or collector.judges[-1][1].get("step") != 1


def test_turn_start_also_starts_a_fresh_window():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    feed(watchdog, [rec("Read", i, "same.txt") for i in range(1, 6)])
    watchdog.feed_record("dsh", {"event": "turn/start", "sessionId": "s1", "step": 10})
    feed(watchdog, [rec("Read", 1, "new.txt")])
    assert not collector.judges


def test_research_goal_with_broad_to_narrow_progress_stays_normal():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    watchdog.feed_record("dsh", {
        "event": "user/message", "sessionId": "research",
        "text": "了解项目结构和院内基础目录知识图谱如何组织",
    })
    feed(watchdog, [
        rec("Glob", 1, "**/*", "research"),
        rec("Grep", 2, "知识图谱|基础目录", "research"),
        rec("Glob", 2, "**/*graph*", "research"),
        rec("Glob", 2, "**/*catalog*", "research"),
        rec("Glob", 2, "**/*ontology*", "research"),
        rec("Read", 3, "docs/architecture/catalog.md", "research"),
        rec("Read", 3, "src/services/catalog.py", "research"),
        rec("Read", 3, "hospital/config/catalog.json", "research"),
        rec("Read", 4, "database/schema.sql", "research"),
    ])
    assert not collector.warnings
    assert not collector.judges


def test_user_goal_is_forwarded_to_judge_payload():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    watchdog.feed_record("dsh", {
        "event": "user/message", "sessionId": "goal-session",
        "text": "调查 DSH 设置入口",
    })
    for step in range(1, 7):
        watchdog.feed_record("dsh", rec("Search", step, "DSH settings", "goal-session"))
    assert collector.judges
    assert collector.judges[0][1]["goal"] == "调查 DSH 设置入口"


def test_repeated_think_is_scored_separately_from_normal_reads():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    for step in range(1, 9):
        watchdog.feed_record("dsh", rec("Think", step, "same unresolved plan", "think-loop"))
    assert collector.judges
    assert any("重复 Think" in reason for reason in collector.judges[0][1]["reasons"])


def test_repeated_edits_are_progress_not_exploration_loop():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    for step in range(1, 9):
        watchdog.feed_record("dsh", rec("Edit", step, "src/service.py", "edit-progress"))
    assert not collector.warnings
    assert not collector.judges


def test_cooldown_requires_new_steps():
    watchdog = ExplorationWatchdog()
    collector = Collector(watchdog)
    feed(watchdog, [rec("Search", i, "same") for i in range(1, 4)])
    first = len(collector.judges)
    feed(watchdog, [rec("Search", 4, "same"), rec("Search", 5, "same")])
    assert len(collector.judges) == first


def test_structured_judge_parser_has_safe_fallback():
    result = parse_judge_result('{"verdict":"ASK_USER","reason":"need context","next_action":"ask","confidence":0.8}')
    assert result["verdict"] == JudgeVerdict.ASK_USER.value
    assert parse_judge_result("garbage")["verdict"] == JudgeVerdict.UNKNOWN.value
