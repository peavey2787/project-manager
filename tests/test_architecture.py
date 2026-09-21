from scripts.check_architecture import check_repo


def test_architecture_gate() -> None:
    violations = check_repo()
    assert not violations, "\n".join(f"{item.path}: {item.message}" for item in violations)
