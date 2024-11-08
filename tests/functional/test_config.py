import pytest
import typing
import os
import uuid
import pathlib
import pytest_mock
import tempfile
from simvue.config.user import SimvueConfiguration


@pytest.mark.config
@pytest.mark.parametrize(
    "use_env", (True, False),
    ids=("use_env", "no_env")
)
@pytest.mark.parametrize(
    "use_file", (None, "basic", "extended", "pyproject.toml"),
    ids=("no_file", "basic_file", "extended_file", "pyproject_toml")
)
@pytest.mark.parametrize(
    "use_args", (True, False),
    ids=("args", "no_args")
)
def test_config_setup(
    use_env: bool,
    use_file: str | None,
    use_args: bool,
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest_mock.MockerFixture
) -> None:
    _token: str = f"{uuid.uuid4()}".replace('-', '')
    _other_token: str = f"{uuid.uuid4()}".replace('-', '')
    _arg_token: str = f"{uuid.uuid4()}".replace('-', '')
    _url: str = "https://simvue.example.com/"
    _other_url: str = "http://simvue.example.com/"
    _arg_url: str = "http://simvue.example.io/"
    _description: str = "test case for runs"
    _description_ppt: str = "test case for runs using pyproject.toml"
    _folder: str = "/test-case"
    _folder_ppt: str = "/test-case-ppt"
    _tags: list[str] = ["tag-test", "other-tag"]
    _tags_ppt: list[str] = ["tag-test-ppt", "other-tag-ppt"]

    # Deactivate the server checks for this test
    monkeypatch.setenv("SIMVUE_NO_SERVER_CHECK", "True")
    monkeypatch.delenv("SIMVUE_TOKEN", False)
    monkeypatch.delenv("SIMVUE_URL", False)

    if use_env:
        monkeypatch.setenv("SIMVUE_TOKEN", _other_token)
        monkeypatch.setenv("SIMVUE_URL", _other_url)

    with tempfile.TemporaryDirectory() as temp_d:
        _config_file = None
        _ppt_file = None
        if use_file:
            if use_file == "pyproject.toml":
                _lines_ppt: str = f"""
[tool.poetry]
name = "simvue_testing"
version = "0.1.0"
description = "A dummy test project"

[tool.simvue.run]
description = "{_description_ppt}"
folder = "{_folder_ppt}"
tags = {_tags_ppt}
"""
                with open((_ppt_file := pathlib.Path(temp_d).joinpath("pyproject.toml")), "w") as out_f:
                    out_f.write(_lines_ppt)
            with open(_config_file := pathlib.Path(temp_d).joinpath("simvue.toml"), "w") as out_f:
                _lines: str = f"""
[server]
url = "{_url}"
token = "{_token}"

[offline]
cache = "{temp_d}"
"""

                if use_file == "extended":
                    _lines += f"""
[run]
description = "{_description}"
folder = "{_folder}"
tags = {_tags}
"""
                out_f.write(_lines)
            SimvueConfiguration.config_file.cache_clear()

        mocker.patch("simvue.config.parameters.get_expiry", lambda *_, **__: 1e10)


        def _mocked_find(file_names: list[str], *_, ppt_file=_ppt_file, conf_file=_config_file, **__) -> str:
            if "pyproject.toml" in file_names:
                return ppt_file
            else:
                return conf_file

        mocker.patch("simvue.config.user.sv_util.find_first_instance_of_file", _mocked_find)

        import simvue.config.user

        if not use_file and not use_env and not use_args:
            with pytest.raises(RuntimeError):
                simvue.config.user.SimvueConfiguration.fetch()
            return
        elif use_args:
            _config = simvue.config.user.SimvueConfiguration.fetch(
                server_url=_arg_url,
                server_token=_arg_token
            )
        else:
            _config = simvue.config.user.SimvueConfiguration.fetch()

        if use_file and use_file != "pyproject.toml":
            assert _config.config_file() == _config_file

        if use_env:
            assert _config.server.url == _other_url
            assert _config.server.token == _other_token
        elif use_args:
            assert _config.server.url == _arg_url
            assert _config.server.token == _arg_token
        elif use_file and use_file != "pyproject.toml":
            assert _config.server.url == _url
            assert _config.server.token == _token
            assert _config.offline.cache == temp_d

        if use_file == "extended":
            assert _config.run.description == _description
            assert _config.run.folder == _folder
            assert _config.run.tags == _tags
        elif use_file == "pyproject.toml":
            assert _config.run.description == _description_ppt
            assert _config.run.folder == _folder_ppt
            assert _config.run.tags == _tags_ppt
        elif use_file:
            assert _config.run.folder == "/"
            assert not _config.run.description
            assert not _config.run.tags

        simvue.config.user.SimvueConfiguration.config_file.cache_clear()

