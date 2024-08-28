import simvue.api as sv_api
import json
import requests
import humanfriendly
import typing
import functools

from simvue.utilities import get_auth
from simvue.objects.base import ServerObject

class Folder(ServerObject):
    def __init__(self, path: str, suppress_errors: bool=False) -> None:
        self._path = path
        super().__init__(suppress_errors=suppress_errors)

    def _creation_packet(self) -> dict[str, typing.Any]:
        return {"path": self._path}

    def _get_identifier(self) -> typing.Optional[str]:
        """Retrieve folder identifier for the specified path if found

        Parameters
        ----------
        path : str
            the path to search for

        Returns
        -------
        str | None
            if a match is found, return the identifier of the folder
        """
        # Only need to retrieve the identifier once
        if self._identifier:
            return self._identifier


        params: dict[str, str] = {"filters": json.dumps([f"path == {self._path}"])}

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
    def tags(self) -> list[str]:
        return self._retrieve_attribute("tags")

    @tags.setter
    def tags(self, tags: list[str]) -> None:
        self._set_attribute("tags", tags)

    @property
    def name(self) -> str:
        return self._retrieve_attribute("name")

    @property
    def path(self) -> str:
        return self._retrieve_attribute("path")

    @property
    def description(self) -> str:
        return self._retrieve_attribute("description")

    @description.setter
    def description(self, description: str) -> None:
        self._set_attribute("description", description)

    @property
    def retention_period(self) -> typing.Optional[str]:
        _retention = self._retrieve_attribute("ttl")
        if _retention is None:
            return _retention
        return humanfriendly.format_timespan(self._retrieve_attribute("ttl"))

    @retention_period.setter
    def retention_period(self, retention_period: str) -> None:
        # Parse the time to live/retention time if specified
        try:
            retention_secs: typing.Optional[int] = int(
                humanfriendly.parse_timespan(retention_period)
            )
        except humanfriendly.InvalidTimespan:
            raise TypeError(
                f"Invalid argument '{retention_period}' for retention period"
            )
        self._set_attribute("ttl", retention_secs)

    def __repr__(self) -> str:
        return f"simvue.Folder({self._identifier}, path={self.path}, tags={self.tags}, retention_period={self.retention_period})"

