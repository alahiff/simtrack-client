import pytest
import uuid
import simvue.filter as sv_filter


@pytest.mark.unit
def test_generate_meta_filter() -> None:
    _filter = sv_filter.RunsFilter()
    _attribute: str = f"{uuid.uuid4()}"
    _filter.has_metadata_attribute(_attribute)
    assert f"{_filter}" == f"metadata.{_attribute} exists"
    _filter.clear()

    _expected_members: dict[str, str] = {
        "eq": "==",
        "leq": "<=",
        "geq": ">=",
        "lt": "<",
        "gt": ">",
        "neq": "!=",
        "contains": "contains"
    }

    assert all(hasattr(_filter, f"metadata_{i}") for i in _expected_members.keys())

    _comparison: str = "0234"

    for func, symbol in _expected_members.items():
        getattr(_filter, f"metadata_{func}")(_attribute, _comparison)
        assert f"{_filter}" == f"metadata.{_attribute} {symbol} {_comparison}"
        _filter.clear()