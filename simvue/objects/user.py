import http
import typing
import requests

import simvue.api as sv_api

from simvue.objects.base import ServerObject

class User(ServerObject):
    _tenant: str
    _username: str
    def __init__(self) -> None:
        super().__init__()

    def _creation_packet(self) -> dict[str, typing.Any]:
        return {}

    def _get_identifier(self) -> typing.Optional[str]:
        response = sv_api.get(f"{self._server_url}/whoami", headers=self._headers)

        if response.status_code != http.HTTPStatus.OK:
            raise RuntimeError(
                "Failed to retrieve current user information"
            )
        _data = response.json()
        _user_name = _data.get("username")

        response = sv_api.get(f"{self._url}/{_user_name}", headers=self._headers)

        if response.status_code != http.HTTPStatus.OK:
            raise RuntimeError(
                "Failed to retrieve current tenant users"
            )

        if response.json() and (identifier := response.json().get("id")):
            return identifier

        return None

    @property
    def tenant(self) -> str:
        return self._retrieve_attribute("tenant").get("name")

    @property
    def email(self) -> str:
        return self._retrieve_attribute("email")

    @property
    def full_name(self) -> str:
        return self._retrieve_attribute("fullname")

    def __repr__(self) -> str:
        return f"simvue.User({self._identifier}, fullname={self.full_name}, email={self.email}, tenant={self.tenant})"

    def __str__(self) -> str:
        return self.__repr__()

