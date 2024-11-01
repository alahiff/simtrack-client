import abc
import enum
import sys
import typing
import pydantic

if sys.version_info < (3, 11):
    from typing_extensions import Self
else:
    from typing import Self


class Status(str, enum.Enum):
    Created = "created"
    Running = "running"
    Completed = "completed"
    Lost = "lost"
    Terminated = "terminated"
    Failed = "failed"


class Time(str, enum.Enum):
    Created = "created"
    Started = "started"
    Modified = "modified"
    Ended = "ended"


class System(str, enum.Enum):
    Working_Directory = "cwd"
    Hostname = "hostname"
    Python_Version = "pythonversion"
    Platform_System = "platform.system"
    Platform_Release = "platform.release"
    Platform_Version = "platform.version"
    CPU_Architecture = "cpu.arch"
    CPU_Processor = "cpu.processor"
    GPU_Name = "gpu.name"
    GPU_Driver = "gpu.driver"


class RestAPIFilter(abc.ABC):
    def __init__(self) -> None:
        self._filters: list[str] = []
        self._generate_members()

    def _time_within(
        self, time_type: Time, *, hours: int = 0, days: int = 0, years: int = 0
    ) -> Self:
        if len(_non_zero := list(i for i in (hours, days, years) if i != 0)) > 1:
            raise AssertionError(
                "Only one duration type may be provided: hours, days or years"
            )
        if len(_non_zero) < 1:
            raise AssertionError(
                f"No duration provided for filter '{time_type.value}_within'"
            )

        if hours:
            self._filters.append(f"{time_type.value} < {hours}h")
        elif days:
            self._filters.append(f"{time_type.value} < {days}d")
        else:
            self._filters.append(f"{time_type.value} < {years}y")
        return self

    @abc.abstractmethod
    def _generate_members(self) -> None:
        pass

    @pydantic.validate_call
    def has_name(self, name: str) -> Self:
        self._filters.append(f"name == {name}")
        return self

    @pydantic.validate_call
    def has_name_containing(self, name: str) -> Self:
        self._filters.append(f"name contains {name}")
        return self

    @pydantic.validate_call
    def created_within(
        self,
        *,
        hours: pydantic.NonNegativeInt = 0,
        days: pydantic.NonNegativeInt = 0,
        years: pydantic.NonNegativeInt = 0,
    ) -> Self:
        return self._time_within(Time.Created, hours=hours, days=days, years=years)

    @pydantic.validate_call
    def has_description_containing(self, search_str: str) -> Self:
        self._filters.append(f"description contains {search_str}")
        return self

    @pydantic.validate_call
    def exclude_description_containing(self, search_str: str) -> Self:
        self._filters.append(f"description not contains {search_str}")
        return self

    @pydantic.validate_call
    def has_tag(self, tag: str) -> Self:
        self._filters.append(f"has tag.{tag}")
        return self

    def as_list(self) -> list[str]:
        return self._filters

    def clear(self) -> None:
        self._filters = []


class FoldersFilter(RestAPIFilter):
    def has_path(self, name: str) -> "FoldersFilter":
        self._filters.append(f"path == {name}")
        return self

    def has_path_containing(self, name: str) -> "FoldersFilter":
        self._filters.append(f"path contains {name}")
        return self

    def _generate_members(self) -> None:
        return super()._generate_members()


class RunsFilter(RestAPIFilter):
    def _generate_members(self) -> None:
        _global_comparators: list[typing.Callable] = [
            self._value_contains,
            self._value_eq,
            self._value_neq,
        ]

        _numeric_comparators: list[typing.Callable] = [
            self._value_geq,
            self._value_leq,
            self._value_lt,
            self._value_gt,
        ]

        for label, system_spec in System.__members__.items():
            for function in _global_comparators:
                _label: str = label.lower()
                _func_name: str = function.__name__.replace("_value", _label)

                def _out_func(value, func=function):
                    return func("system", system_spec.value, value)

                _out_func.__name__ = _func_name
                setattr(self, _func_name, _out_func)

        for function in _global_comparators + _numeric_comparators:
            _func_name = function.__name__.replace("_value", "metadata")

            def _out_func(attribute, value, func=function):
                return func("metadata", attribute, value)

            _out_func.__name__ = _func_name
            setattr(self, _func_name, _out_func)

    @pydantic.validate_call
    def author(self, username: str = "self") -> Self:
        self._filters.append(f"user == {username}")
        return self

    @pydantic.validate_call
    def exclude_author(self, username: str = "self") -> Self:
        self._filters.append(f"user != {username}")
        return self

    def starred(self) -> Self:
        self._filters.append("starred")
        return self

    @pydantic.validate_call
    def has_name(self, name: str) -> Self:
        self._filters.append(f"name == {name}")
        return self

    @pydantic.validate_call
    def has_name_containing(self, name: str) -> Self:
        self._filters.append(f"name contains {name}")
        return self

    @pydantic.validate_call
    def has_status(self, status: Status) -> Self:
        self._filters.append(f"status == {status.value}")
        return self

    def is_running(self) -> Self:
        return self.has_status(Status.Running)

    def is_lost(self) -> Self:
        return self.has_status(Status.Lost)

    def has_completed(self) -> Self:
        return self.has_status(Status.Completed)

    def has_failed(self) -> Self:
        return self.has_status(Status.Failed)

    @pydantic.validate_call
    def has_alert(
        self, alert_name: str, is_critical: typing.Optional[bool] = None
    ) -> Self:
        self._filters.append(f"alert.name == {alert_name}")
        if is_critical is True:
            self._filters.append("alert.status == critical")
        elif is_critical is False:
            self._filters.append("alert.status == ok")
        return self

    @pydantic.validate_call
    def started_within(
        self,
        *,
        hours: pydantic.PositiveInt = 0,
        days: pydantic.PositiveInt = 0,
        years: pydantic.PositiveInt = 0,
    ) -> Self:
        return self._time_within(Time.Started, hours=hours, days=days, years=years)

    @pydantic.validate_call
    def modified_within(
        self,
        *,
        hours: pydantic.PositiveInt = 0,
        days: pydantic.PositiveInt = 0,
        years: pydantic.PositiveInt = 0,
    ) -> Self:
        return self._time_within(Time.Modified, hours=hours, days=days, years=years)

    @pydantic.validate_call
    def ended_within(
        self,
        *,
        hours: pydantic.PositiveInt = 0,
        days: pydantic.PositiveInt = 0,
        years: pydantic.PositiveInt = 0,
    ) -> Self:
        return self._time_within(Time.Ended, hours=hours, days=days, years=years)

    @pydantic.validate_call
    def in_folder(self, folder_name: str) -> Self:
        self._filters.append(f"folder.path == {folder_name}")
        return self

    @pydantic.validate_call
    def has_metadata_attribute(self, attribute: str) -> Self:
        self._filters.append(f"metadata.{attribute} exists")
        return self

    @pydantic.validate_call
    def exclude_metadata_attribute(self, attribute: str) -> Self:
        self._filters.append(f"metadata.{attribute} not exists")
        return self

    def _value_eq(
        self, category: str, attribute: str, value: typing.Union[str, int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} == {value}")
        return self

    def _value_neq(
        self, category: str, attribute: str, value: typing.Union[str, int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} != {value}")
        return self

    def _value_contains(
        self, category: str, attribute: str, value: typing.Union[str, int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} contains {value}")
        return self

    def _value_leq(
        self, category: str, attribute: str, value: typing.Union[int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} <= {value}")
        return self

    def _value_geq(
        self, category: str, attribute: str, value: typing.Union[int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} >= {value}")
        return self

    def _value_lt(
        self, category: str, attribute: str, value: typing.Union[int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} < {value}")
        return self

    def _value_gt(
        self, category: str, attribute: str, value: typing.Union[int, float]
    ) -> Self:
        self._filters.append(f"{category}.{attribute} > {value}")
        return self

    def __str__(self) -> str:
        return " && ".join(self._filters) if self._filters else "None"

    def __repr__(self) -> str:
        return f"{super().__repr__()[:-1]}, filters={self._filters}>"
