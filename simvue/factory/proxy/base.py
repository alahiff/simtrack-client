import abc
import logging
import typing


class SimvueBaseClass(abc.ABC):
    @abc.abstractmethod
    def __init__(
        self,
        name: typing.Optional[str],
        uniq_id: str,
        suppress_errors: bool,
    ) -> None:
        self._logger = logging.getLogger(f"simvue.{self.__class__.__name__}")
        self._suppress_errors: bool = suppress_errors
        self._uuid: str = uniq_id
        self._name: typing.Optional[str] = name
        self._id: typing.Optional[int] = None
        self._aborted: bool = False

    def _error(self, message: str) -> None:
        """
        Raise an exception if necessary and log error
        """
        if not self._suppress_errors:
            raise RuntimeError(message)
        else:
            self._logger.error(message)
            self._aborted = True

    @abc.abstractmethod
    def list_tags(self) -> typing.Optional[list[str]]:
        pass

    @abc.abstractmethod
    def create_run(
        self, data: dict[str, typing.Any]
    ) -> tuple[typing.Optional[str], typing.Optional[str]]:
        pass

    @abc.abstractmethod
    def update(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def set_folder_details(self, path, data) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def save_file(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def add_alert(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def set_alert_state(
        self, alert_id: str, status: str
    ) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def list_alerts(self) -> list[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def send_metrics(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def send_event(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def send_heartbeat(self) -> typing.Optional[dict[str, typing.Any]]:
        pass

    @abc.abstractmethod
    def get_abort_status(self) -> bool:
        pass
