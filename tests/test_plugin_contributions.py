from __future__ import annotations

import pytest

from pet.plugins import CapabilitySet, CommandRegistry
from pet.plugins.ports import CommandNotFound


def test_old_command_handle_cannot_remove_replacement():
    registry = CommandRegistry()
    old = registry.register("test.run", lambda: "old", owner="test")
    old.unregister()
    new = registry.register("test.run", lambda: "new", owner="test")
    old.unregister()
    assert registry.invoke("test.run") == "new"
    assert new.invoke() == "new"
    with pytest.raises(CommandNotFound):
        old.invoke()


def test_contribution_batch_is_atomic_and_identity_bound():
    from pet.plugins.contributions import Contribution, ContributionRegistry

    commands = CommandRegistry()
    registry = ContributionRegistry(commands)
    port = registry.bind("official.test", "window-1", CapabilitySet(["menu.contribute"]))
    batch = port.register([Contribution("run", "menu", command="run")], commands={"run": lambda: "old"})
    old = batch.handles[0]
    assert old.invoke() == "old"
    with pytest.raises(ValueError):
        port.register([Contribution("run", "menu", command="other")], commands={"other": lambda: None})
    assert len(commands.list()) == 1
    assert old.invoke() == "old"
    batch.dispose()
    new = port.register([Contribution("run", "menu", command="run")], commands={"run": lambda: "new"})
    old.revoke()
    assert new.handles[0].invoke() == "new"
    with pytest.raises(CommandNotFound):
        old.invoke()
    new.dispose()
    new.dispose()
    assert registry.list() == ()
    assert commands.list() == ()


def test_owner_capability_and_factory_failure_are_isolated():
    from pet.plugins import CapabilityDenied
    from pet.plugins.contributions import Contribution, ContributionRegistry

    registry = ContributionRegistry(CommandRegistry())
    denied = registry.bind("denied", "app", CapabilitySet())
    with pytest.raises(CapabilityDenied):
        denied.register([Contribution("x", "menu", command="x")], commands={"x": lambda: None})
    a = registry.bind("a", "app", CapabilitySet(["settings.contribute"]))
    b = registry.bind("b", "app", CapabilitySet(["settings.contribute"]))
    good = a.register([Contribution("settings", "settings", factory=lambda: "safe")])
    bad = b.register([Contribution("settings", "settings", factory=lambda: 1 / 0)])
    with pytest.raises(ZeroDivisionError):
        bad.handles[0].create()
    assert good.handles[0].create() == "safe"
    registry.revoke_owner("b")
    assert len(registry.list(owner="a")) == 1


def test_registration_requires_owning_thread():
    from concurrent.futures import ThreadPoolExecutor

    from pet.plugins.contributions import ContributionRegistry

    registry = ContributionRegistry(CommandRegistry())
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(RuntimeError, match="thread"):
            pool.submit(registry.revoke_owner, "test").result(timeout=5)


def test_feature_host_keeps_settings_on_disable_and_invalidates_old_actions():
    from pet.plugins.contributions import Contribution
    from pet.plugins.feature_host import FeatureDefinition, FeatureHost

    host = FeatureHost()
    host.provide(FeatureDefinition("test", (Contribution("run", "menu", command="run"),), lambda *a: "page"))
    stopped = []
    host.attach("test", "window", {"run": lambda: "ran"}, lambda: stopped.append(True))
    old = host.menu("test", "window", "run")
    settings = host.settings("test", "dialog")
    assert old.invoke() == "ran"
    host.disable("test")
    assert stopped == [True]
    assert host.menu("test", "window", "run") is None
    assert settings.create() == "page"
    with pytest.raises(CommandNotFound):
        old.invoke()
    host.enable("test")
    assert host.menu("test", "window", "run").invoke() == "ran"
    with pytest.raises(CommandNotFound):
        old.invoke()
    host.detach("test", "window")
    assert host.menu("test", "window", "run") is None
    assert settings.active


def test_feature_removal_can_be_cancelled_and_fault_revokes_immediately():
    from pet.plugins.contributions import Contribution
    from pet.plugins.feature_host import FeatureDefinition, FeatureHost

    host = FeatureHost()
    host.provide(FeatureDefinition("test", (Contribution("run", "menu", command="run"),), lambda: None))
    page = host.settings("test", "dialog")
    host.before_remove("test", lambda: False)
    assert host.remove("test") is False
    assert page.active
    host.fault("test", "factory_failed")
    assert not page.active
    assert host.state("test") == "fault"
    assert host.diagnostics() == {"test": "factory_failed"}


def test_command_only_batch_is_cleaned_by_owner():
    from pet.plugins.contributions import ContributionRegistry

    registry = ContributionRegistry(CommandRegistry())
    registry.bind("test", "app", CapabilitySet(["menu.contribute"])).register([], commands={"run": lambda: None})
    registry.revoke_owner("test")
    assert registry.commands.list() == ()


def test_same_command_in_two_batches_never_replaces_or_revokes_other_batch():
    from pet.plugins.contributions import Contribution, ContributionRegistry

    registry = ContributionRegistry(CommandRegistry())
    port = registry.bind("test", "window", CapabilitySet(["menu.contribute"]))
    first = port.register([Contribution("one", "menu", command="run")], commands={"run": lambda: 1})
    second = port.register([Contribution("two", "menu", command="run")], commands={"run": lambda: 2})
    assert first.handles[0].invoke() == 1
    second.dispose()
    assert first.handles[0].invoke() == 1


def test_command_only_registration_checks_capability():
    from pet.plugins import CapabilityDenied
    from pet.plugins.contributions import ContributionRegistry

    registry = ContributionRegistry(CommandRegistry())
    with pytest.raises(CapabilityDenied):
        registry.bind("test", "app", CapabilitySet()).register([], commands={"run": lambda: None})
    assert registry.commands.list() == ()


def test_partial_command_registration_rolls_back_without_touching_other_owner(monkeypatch):
    from pet.plugins.contributions import Contribution, ContributionRegistry

    commands = CommandRegistry()
    stable = commands.register("stable", lambda: "safe", owner="other")
    registry = ContributionRegistry(commands)
    original = commands.register

    def fail_second(name, callback, *, owner, **kwargs):
        if name.endswith(":second"):
            raise RuntimeError("registration failed")
        return original(name, callback, owner=owner, **kwargs)

    monkeypatch.setattr(commands, "register", fail_second)
    with pytest.raises(RuntimeError, match="registration failed"):
        registry.bind("test", "app", CapabilitySet(["menu.contribute"])).register(
            [Contribution("one", "menu", command="first"), Contribution("two", "menu", command="second")],
            commands={"first": lambda: None, "second": lambda: None},
        )
    assert not registry.list()
    assert len(commands.list()) == 1
    assert stable.invoke() == "safe"


def test_platform_filtered_contribution_does_not_leave_command():
    from pet.plugins.contributions import Contribution, ContributionRegistry

    registry = ContributionRegistry(CommandRegistry())
    batch = registry.bind("test", "app", CapabilitySet(["menu.contribute"])).register(
        [Contribution("other-platform", "menu", command="run", platforms=("not-a-platform",))],
        commands={"run": lambda: None},
    )
    assert not registry.list()
    assert not registry.commands.list()
    batch.dispose()
