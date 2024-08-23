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
        self._tenant = _data["tenant"]
        self._username = _data["username"]

        response = sv_api.get(self._url, headers=self._headers, params={"tenant": self._tenant, "search": self._username})

        if response.status_code != http.HTTPStatus.OK:
            raise RuntimeError(
                "Failed to retrieve current tenant users"
            )

        if response and (identifier := response[0].get("id")):
            return identifier

        return None


if __name__ in "__main__":
    print(User().id)
