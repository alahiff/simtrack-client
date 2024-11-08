import pytest
import pathlib
import re
import simvue.metadata as sv_meta


@pytest.mark.metadata
def test_cargo_env() -> None:
    metadata = sv_meta._rust_env(pathlib.Path(__file__).parents[1].joinpath("example_data"))
    assert metadata["rust.environment.serde"] == "1.0.123"
    assert metadata["rust.project.name"] == "example_project"

@pytest.mark.metadata
def test_python_env() -> None:
    metadata = sv_meta._python_env(pathlib.Path(__file__).parents[1].joinpath("example_data"))
    assert re.findall(r"\d+\.\d+\.\d+", metadata["python.environment.click"])
    assert metadata["python.project.name"] == "spam-eggs"

