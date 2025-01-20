import typing

import pydantic
import datetime

from simvue.api.objects.base import SimvueObject, staging_check, write_only
from simvue.models import NAME_REGEX, DATETIME_FORMAT


class StorageBase(SimvueObject):
    def __init__(
        self,
        identifier: typing.Optional[str] = None,
        _read_only: bool = False,
        **kwargs,
    ) -> None:
        """Retrieve an alert from the Simvue server by identifier"""
        self._label = "storage"
        self._endpoint = self._label
        super().__init__(identifier, _read_only=_read_only, **kwargs)

    @classmethod
    def new(cls, **kwargs):
        pass

    @property
    @staging_check
    def name(self) -> str:
        """Retrieve the name for this storage"""
        return self._get_attribute("name")

    @name.setter
    @write_only
    @pydantic.validate_call
    def name(
        self, name: typing.Annotated[str, pydantic.Field(pattern=NAME_REGEX)]
    ) -> None:
        """Set name assigned to this folder"""
        self._staging["name"] = name

    @property
    def type(self) -> str:
        """Retrieve the type of storage"""
        return self._get_attribute("type")

    @property
    @staging_check
    def default(self) -> bool:
        """Retrieve if this is the default storage for the user"""
        return self._get_attribute("is_default")

    @default.setter
    @write_only
    @pydantic.validate_call
    def default(self, is_default: bool) -> None:
        """Set this storage to be the default"""
        self._staging["is_default"] = is_default

    @property
    @staging_check
    def tenant_usable(self) -> bool:
        """Retrieve if this is usable by the current user tenant"""
        return self._get_attribute("is_tenant_usable")

    @tenant_usable.setter
    @write_only
    @pydantic.validate_call
    def tenant_usable(self, is_tenant_usable: bool) -> None:
        """Set this storage to be usable by the current user tenant"""
        self._staging["tenant_usable"] = is_tenant_usable

    @property
    @staging_check
    def enabled(self) -> bool:
        """Retrieve if this is enabled"""
        return self._get_attribute("is_enabled")

    @enabled.setter
    @write_only
    @pydantic.validate_call
    def enabled(self, is_enabled: bool) -> None:
        """Set this storage to be usable by the current user tenant"""
        self._staging["is_enabled"] = is_enabled

    @property
    def created(self) -> datetime.datetime | None:
        """Retrieve created datetime for the artifact"""
        _created: str | None = self._get_attribute("created")
        return (
            datetime.datetime.strptime(_created, DATETIME_FORMAT) if _created else None
        )