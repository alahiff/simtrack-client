import configparser
import datetime
import hashlib
import logging
import json
import tabulate
import pydantic
import importlib.util
import functools
import contextlib
import os
import typing

import jwt

from simvue.version import __version__

CHECKSUM_BLOCK_SIZE = 4096
EXTRAS: tuple[str, ...] = ("plot", "torch")

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from simvue.run import Run


def parse_validation_response(
    response: dict[str, list[dict[str, str]]],
) -> str:
    """Parse ValidationError response from server

    Reformats the error information from a validation error into a human
    readable table. Checks if 'body' exists within response to determine
    whether or not the output can contain the original input.

    Parameters
    ----------
    response : dict[str, list[dict[str, str]]]
        response from Simvue server

    Returns
    -------
    str
        return the validation information
    """
    if not (issues := response.get("detail")):
        raise RuntimeError(
            "Expected key 'detail' in server response during validation failure"
        )

    out: list[list[str]] = []

    for issue in issues:
        obj_type: str = issue["type"]
        location: list[str] = issue["loc"]
        location.remove("body")
        location_addr: str = ""
        for i, loc in enumerate(location):
            if isinstance(loc, int):
                location_addr += f"[{loc}]"
            else:
                location_addr += f"{'.' if i > 0 else ''}{loc}"
        headers = ["Type", "Location", "Message"]
        information = [obj_type, location_addr]

        # Check if server response contains 'body'
        if body := response.get("body"):
            headers = ["Type", "Location", "Input", "Message"]
            input_arg = body
            for loc in location:
                try:
                    input_arg = input_arg[loc]
                except TypeError:
                    break
            information.append(input_arg)

        msg: str = issue["msg"]
        information.append(msg)
        out.append(information)

    _table = tabulate.tabulate(out, headers=headers, tablefmt="fancy_grid")
    return str(_table)


def check_extra(extra_name: str) -> typing.Callable:
    def decorator(
        class_func: typing.Optional[typing.Callable] = None,
    ) -> typing.Optional[typing.Callable]:
        @functools.wraps(class_func)
        def wrapper(self, *args, **kwargs) -> typing.Any:
            if extra_name == "plot" and not all(
                [
                    importlib.util.find_spec("matplotlib"),
                    importlib.util.find_spec("plotly"),
                ]
            ):
                raise RuntimeError(
                    f"Plotting features require the '{extra_name}' extension to Simvue"
                )
            elif extra_name == "torch" and not importlib.util.find_spec("torch"):
                raise RuntimeError(
                    "PyTorch features require the 'torch' module to be installed"
                )
            elif extra_name not in EXTRAS:
                raise RuntimeError(f"Unrecognised extra '{extra_name}'")
            return class_func(self, *args, **kwargs) if class_func else None

        return wrapper

    return decorator


def parse_pydantic_error(class_name: str, error: pydantic.ValidationError) -> str:
    out_table: list[str] = []
    for data in json.loads(error.json()):
        out_table.append([data["loc"], data["type"], data["msg"]])
    err_table = tabulate.tabulate(
        out_table,
        headers=["Location", "Type", "Message"],
        tablefmt="fancy_grid",
    )
    return f"`{class_name}` Validation:\n{err_table}"


def skip_if_failed(
    failure_attr: str,
    ignore_exc_attr: str,
    on_failure_return: typing.Optional[typing.Any] = None,
) -> typing.Callable:
    """Decorator for ensuring if Simvue throws an exception any other code continues.

    If Simvue throws an exception and the user has specified that such failure
    should not abort the run but rather log errors this decorator will skip
    functionality leaving the runner in a dormant state.

    Parameters
    ----------
    failure_attr : str
        the attribute of the parent class which determines if
        Simvue has failed
    ignore_exc_attr : str
        the attribute of the parent class which defines whether
        an exception should be raised or ignore, by default
    on_failure_return : typing.Any | None, optional
        the value to return instead, by default None

    Returns
    -------
    typing.Callable
        wrapped class method
    """

    def decorator(class_func: typing.Callable) -> typing.Callable:
        @functools.wraps(class_func)
        def wrapper(self: "Run", *args, **kwargs) -> typing.Any:
            if getattr(self, failure_attr, None) and getattr(
                self, ignore_exc_attr, None
            ):
                logger.debug(
                    f"Skipping call to '{class_func.__name__}', "
                    f"client in fail state (see logs)."
                )
                return on_failure_return

            # Handle case where Pydantic validates the inputs
            try:
                return class_func(self, *args, **kwargs)
            except pydantic.ValidationError as e:
                error_str = parse_pydantic_error(class_func.__name__, e)
                if getattr(self, ignore_exc_attr, True):
                    setattr(self, failure_attr, True)
                    logger.error(error_str)
                    return on_failure_return
                self._error(error_str)

        setattr(wrapper, "__fail_safe", True)
        return wrapper

    return decorator


def prettify_pydantic(class_func: typing.Callable) -> typing.Callable:
    """Converts pydantic validation errors to a table

    Parameters
    ----------
    class_func : typing.Callable
        function to wrap

    Returns
    -------
    typing.Callable
        wrapped function

    Raises
    ------
    RuntimeError
        the formatted validation error
    """

    @functools.wraps(class_func)
    def wrapper(self, *args, **kwargs) -> typing.Any:
        try:
            return class_func(self, *args, **kwargs)
        except pydantic.ValidationError as e:
            error_str = parse_pydantic_error(class_func.__name__, e)
            raise RuntimeError(error_str)

    return wrapper


def get_auth():
    """
    Get the URL and access token
    """
    url = None
    token = None

    # Try reading from config file
    for filename in (
        os.path.join(os.path.expanduser("~"), ".simvue.ini"),
        "simvue.ini",
    ):
        with contextlib.suppress(Exception):
            config = configparser.ConfigParser()
            config.read(filename)
            token = config.get("server", "token")
            url = config.get("server", "url")

    # Try environment variables
    token = os.getenv("SIMVUE_TOKEN", token)
    url = os.getenv("SIMVUE_URL", url)

    if not token:
        raise ValueError("No Simvue server token was specified")
    if not url:
        raise ValueError("No Simvue server URL was specified")

    return url, token


def get_offline_directory():
    """
    Get directory for offline cache
    """
    directory = None

    for filename in (
        os.path.join(os.path.expanduser("~"), ".simvue.ini"),
        "simvue.ini",
    ):
        with contextlib.suppress(Exception):
            config = configparser.ConfigParser()
            config.read(filename)
            directory = config.get("offline", "cache")

    if not directory:
        directory = os.path.join(os.path.expanduser("~"), ".simvue")

    return directory


def create_file(filename):
    """
    Create an empty file
    """
    try:
        with open(filename, "w") as fh:
            fh.write("")
    except Exception as err:
        logger.error("Unable to write file %s due to: %s", filename, str(err))


def remove_file(filename):
    """
    Remove file
    """
    if os.path.isfile(filename):
        try:
            os.remove(filename)
        except Exception as err:
            logger.error("Unable to remove file %s due to: %s", filename, str(err))


def get_expiry(token) -> typing.Optional[int]:
    """
    Get expiry date from a JWT token
    """
    expiry: typing.Optional[int] = None
    with contextlib.suppress(jwt.DecodeError):
        expiry = jwt.decode(token, options={"verify_signature": False})["exp"]

    return expiry


def prepare_for_api(data_in, all=True):
    """
    Remove references to pickling
    """
    data = data_in.copy()
    if "pickled" in data:
        del data["pickled"]
    if "pickledFile" in data and all:
        del data["pickledFile"]
    return data


def calculate_sha256(filename: str, is_file: bool) -> typing.Optional[str]:
    """
    Calculate sha256 checksum of the specified file
    """
    sha256_hash = hashlib.sha256()
    if is_file:
        try:
            with open(filename, "rb") as fd:
                for byte_block in iter(lambda: fd.read(CHECKSUM_BLOCK_SIZE), b""):
                    sha256_hash.update(byte_block)
                return sha256_hash.hexdigest()
        except Exception:
            return None

    if isinstance(filename, str):
        sha256_hash.update(bytes(filename, "utf-8"))
    else:
        sha256_hash.update(bytes(filename))
    return sha256_hash.hexdigest()


def validate_timestamp(timestamp):
    """
    Validate a user-provided timestamp
    """
    try:
        datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return False

    return True


def compare_alerts(first, second):
    """ """
    for key in ("name", "description", "source", "frequency", "notification"):
        if key in first and key in second:
            if not first[key]:
                continue

            if first[key] != second[key]:
                return False

    if "alerts" in first and "alerts" in second:
        for key in ("rule", "window", "metric", "threshold", "range_low", "range_high"):
            if key in first["alerts"] and key in second["alerts"]:
                if not first[key]:
                    continue

                if first["alerts"][key] != second["alerts"]["key"]:
                    return False

    return True


def request_headers(message_pack: bool=False) -> dict[str, str]:
    _, _token = get_auth()

    _headers: dict[str, str] = {
        "Authorization": f"Bearer {_token}",
        "User-Agent": f"Simvue Python client {__version__}",
    }
    _headers_mp: dict[str, str] = _headers | {
        "Content-Type": "application/msgpack"
    }

    return _headers_mp if message_pack else _headers

