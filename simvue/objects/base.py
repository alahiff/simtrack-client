from functools import lru_cache
import typing
import http
import logging

import requests
import simvue.utilities as sv_util
import simvue.api as sv_api
import abc


class ServerObject(abc.ABC):
    _identifier: typing.Optional[str] = None
    _url: str
    _token: str
    def __init__(self, end_point: typing.Optional[str]=None, suppress_errors: bool=False) -> None:
        _url, _token = sv_util.get_auth()
        self._logger = logging.getLogger(f"simvue.{self.__class__.__name__}")
        self._suppress_errors = suppress_errors

        # The endpoint is either provided or taken to be the pluralised lower case
        # form of the subclass
        _endpoint: str = end_point or (self.__class__.__name__.lower() + "s")

        self._server_url = f"{_url}/api"
        self._url = f"{self._server_url}/{_endpoint}"
        self._headers = sv_util.request_headers()
        self._identifier = self._get_identifier()
        self._get_object()

    @abc.abstractmethod
    def _get_identifier(self) -> typing.Optional[str]:
        pass

    @property
    def id(self) -> typing.Optional[str]:
        return self._get_identifier()

    def star(self, mark_as_starred: bool=True) -> typing.Optional[bool]:
        # If the URL does not exist for this object return None
        try:
            sv_api.put(f"{self._url}/{self._identifier}/starred", headers=self._headers, data={"starred": mark_as_starred})
        except requests.exceptions.HTTPError:
            return None

    def _error(self, message: str) -> None:
        """
        Raise an exception if necessary and log error
        """
        if not self._suppress_errors:
            raise RuntimeError(message)
        else:
            self._logger.error(message)

    def check_token(self) -> bool:
        """
        Check token
        """
        if not (expiry := sv_util.get_expiry(self._token)):
            self._error("Failed to parse user token")
            return False

        if time.time() - expiry > 0:
            self._error("Token has expired")
            return False
        return True

    @abc.abstractmethod
    def _creation_packet(self) -> dict[str, typing.Any]:
        pass

    def _retrieve_attribute(self, attribute: str) -> typing.Any:
        try:
            return self._get_object()[attribute]
        except KeyError:
            raise RuntimeError(
                f"Expected key '{attribute}' in response for "
                f"object '{self._identifier}' of type '{self.__class__.__name__}'"
            )

    def _set_attribute(self, attribute: str, value: typing.Any) -> None:
        if not self._identifier:
            raise RuntimeError(
                f"Cannot set attributes for {self.__class__.__name__}, "
                "failed to retrieve identifier"
            )
        response = sv_api.put(
            f"{self._url}",
            headers=self._headers,
            data={attribute: value, "id": self._identifier}
        )

        if response.status_code != http.HTTPStatus.OK:
            raise RuntimeError(
                f"Failed to update '{self._identifier}' of type '{self.__class__.__name__}': {response.text}"
            )

    @lru_cache
    def _get_object(self) -> None:
        if self._identifier:
            response = sv_api.get(f"{self._url}/{self._identifier}", self._headers)

            if response.status_code not in (http.HTTPStatus.OK, http.HTTPStatus.NOT_FOUND):
                raise RuntimeError(f"Failed to retrieve or create {self.__class__.__name__}('{self._identifier}')")

            if response.status_code == http.HTTPStatus.OK:
                return response.json()

        response = sv_api.post(
            self._url,
            self._headers,
            self._creation_packet()
        )

        self._identifier = self._get_identifier()

        return response.json()

