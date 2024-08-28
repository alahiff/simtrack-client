import typing
import requests

import simvue.api as sv_api

from simvue.objects.base import ServerObject

class Tag(ServerObject):
    def __init__(self, name: str) -> None:
        self._name: str= name
        super().__init__()

    def _creation_packet(self) -> dict[str, typing.Any]:
        return {
            "name": self._name,
            "colour": "grey",
            "description": ""
        }

    def _get_identifier(self) -> typing.Optional[str]:
        # Only need to retrieve the identifier once
        if self._identifier:
            return self._identifier


        params: dict[str, str] = {"search": self._name}

        response: requests.Response = sv_api.get(
            self._url, headers=self._headers, params=params
        )

        if (
            response.status_code == 200
            and (response_data := response.json().get("data"))
            and (identifier := response_data[0].get("id"))
        ):
            return identifier

        return None

    @property
    def name(self) -> str:
        return self._name

    @property
    def color(self) -> str:
        return self._retrieve_attribute("colour")

    @color.setter
    def color(self, color: str) -> None:
        self._set_attribute("colour", color)

    @property
    def description(self) -> str:
        return self._retrieve_attribute("description")

    @description.setter
    def description(self, description: str) -> None:
        self._set_attribute("description", description)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"simvue.Tag({self._identifier}, name={self._name}, color={self.color}, description={self.description})"

