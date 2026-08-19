import process_manager


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.nice_calls: list[int] = []
        self.affinity_calls: list[list[int]] = []

    def nice(self, value):
        self.nice_calls.append(value)

    def cpu_affinity(self, cores):
        self.affinity_calls.append(cores)


def test_apply_resource_policy_normal_priority_does_not_call_nice(monkeypatch):
    fake = FakeProcess(1234)
    monkeypatch.setattr(process_manager.psutil, "Process", lambda pid: fake)

    ok = process_manager.apply_resource_policy(1234, process_manager.ResourcePolicy(priority="normal"))

    assert ok is True
    assert fake.nice_calls == []


def test_apply_resource_policy_sets_low_priority(monkeypatch):
    fake = FakeProcess(1234)
    monkeypatch.setattr(process_manager.psutil, "Process", lambda pid: fake)

    process_manager.apply_resource_policy(1234, process_manager.ResourcePolicy(priority="low"))

    assert fake.nice_calls == [process_manager.psutil.IDLE_PRIORITY_CLASS]


def test_apply_resource_policy_sets_cpu_affinity(monkeypatch):
    fake = FakeProcess(1234)
    monkeypatch.setattr(process_manager.psutil, "Process", lambda pid: fake)
    monkeypatch.setattr(process_manager.psutil, "cpu_count", lambda: 8)

    process_manager.apply_resource_policy(
        1234, process_manager.ResourcePolicy(cpu_affinity_core_count=2)
    )

    assert fake.affinity_calls == [[0, 1]]


def test_apply_resource_policy_returns_false_on_missing_process(monkeypatch):
    def raise_not_found(pid):
        raise process_manager.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(process_manager.psutil, "Process", raise_not_found)

    ok = process_manager.apply_resource_policy(9999, process_manager.ResourcePolicy(priority="low"))

    assert ok is False
