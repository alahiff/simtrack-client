"""
Simvue API Connection
=====================

Provides methods for interacting with a Simvue server which include retry
policies. In cases where JSON is the expected form the data is firstly converted
to a JSON string
"""

import copy
import json
import typing

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from .utilities import parse_validation_response

DEFAULT_API_TIMEOUT = 10
RETRY_MULTIPLIER = 1
RETRY_MIN = 4
RETRY_MAX = 10
RETRY_STOP = 5


def set_json_header(headers: dict[str, str]) -> dict[str, str]:
    """
    Return a copy of the headers with Content-Type set to
    application/json
    """
    headers = copy.deepcopy(headers)
    headers["Content-Type"] = "application/json"
    return headers


@retry(
    wait=wait_exponential(multiplier=RETRY_MULTIPLIER, min=RETRY_MIN, max=RETRY_MAX),
    stop=stop_after_attempt(RETRY_STOP),
    retry=retry_if_exception_type(RuntimeError),
    reraise=True,
)
def post(
    url: str, headers: dict[str, str], data: typing.Any, is_json: bool = True
) -> requests.Response:
    """HTTP POST with retries

    Parameters
    ----------
    url : str
        URL to post to
    headers : dict[str, str]
        headers for the post request
    data : dict[str, typing.Any]
        data to post
    is_json : bool, optional
        send as JSON string, by default True

    Returns
    -------
    requests.Response
        response from post to server

    """
    if is_json:
        data_sent: typing.Union[str, dict[str, typing.Any]] = json.dumps(data)
        headers = set_json_header(headers)
    else:
        data_sent = data

    response = requests.post(
        url, headers=headers, data=data_sent, timeout=DEFAULT_API_TIMEOUT
    )

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Authorization error [{response.status_code}]: {response.text}"
        )

    if response.status_code == 422:
        _parsed_response = parse_validation_response(response.json())
        raise ValueError(
            f"Validation error for '{url}' [{response.status_code}]:\n{_parsed_response}"
        )

    if response.status_code not in (200, 201, 409):
        raise RuntimeError(
            f"HTTP error for '{url}' [{response.status_code}]: {response.text}"
        )

    return response


@retry(
    wait=wait_exponential(multiplier=RETRY_MULTIPLIER, min=RETRY_MIN, max=RETRY_MAX),
    retry=retry_if_exception_type(RuntimeError),
    stop=stop_after_attempt(RETRY_STOP),
    reraise=True,
)
def put(
    url: str,
    headers: dict[str, str],
    data: dict[str, typing.Any],
    is_json: bool = True,
    timeout: int = DEFAULT_API_TIMEOUT,
) -> requests.Response:
    """HTTP PUT with retries

    Parameters
    ----------
    url : str
        URL to put to
    headers : dict[str, str]
        headers for the post request
    data : dict[str, typing.Any]
        data to put
    is_json : bool, optional
        send as JSON string, by default True
    timeout : _type_, optional
        timeout of request, by default DEFAULT_API_TIMEOUT

    Returns
    -------
    requests.Response
        response from executing PUT
    """
    if is_json:
        data_sent: typing.Union[str, dict[str, typing.Any]] = json.dumps(data)
        headers = set_json_header(headers)
    else:
        data_sent = data

    response = requests.put(url, headers=headers, data=data_sent, timeout=timeout)

    response.raise_for_status()

    return response


@retry(
    wait=wait_exponential(multiplier=RETRY_MULTIPLIER, min=RETRY_MIN, max=RETRY_MAX),
    stop=stop_after_attempt(RETRY_STOP),
    retry=retry_if_exception_type(RuntimeError),
    reraise=True,
)
def get(
    url: str, headers: dict[str, str], timeout: int = DEFAULT_API_TIMEOUT, **kwargs
) -> requests.Response:
    """HTTP GET

    Parameters
    ----------
    url : str
        URL to put to
    headers : dict[str, str]
        headers for the post request
    timeout : _type_, optional
        timeout of request, by default DEFAULT_API_TIMEOUT

    Returns
    -------
    requests.Response
        response from executing GET
    """
    response = requests.get(url, headers=headers, timeout=timeout, **kwargs)
    if response.status_code == 422:
        print(response.json())
        _parsed_response = parse_validation_response(response.json())
        raise ValueError(
            f"Validation error for '{url}' [{response.status_code}]:\n{_parsed_response}"
        )
    response.raise_for_status()

    return response
