import logging
import typing
import http

if typing.TYPE_CHECKING:
    from simvue.config.user import SimvueConfiguration

from simvue.api import get, post, put
from simvue.factory.proxy.base import SimvueBaseClass
from simvue.utilities import prepare_for_api, skip_if_failed
from simvue.version import __version__

logger = logging.getLogger(__name__)

UPLOAD_TIMEOUT: int = 30
DEFAULT_API_TIMEOUT: int = 10


class Remote(SimvueBaseClass):
    """
    Class which interacts with Simvue REST API
    """

    def __init__(
        self,
        name: typing.Optional[str],
        uniq_id: str,
        config: "SimvueConfiguration",
        suppress_errors: bool = True,
    ) -> None:
        self._config = config

        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {self._config.server.token}",
            "User-Agent": f"Simvue Python client {__version__}",
        }
        self._headers_mp: dict[str, str] = self._headers | {
            "Content-Type": "application/msgpack"
        }
        super().__init__(name, uniq_id, suppress_errors)

        self._id = uniq_id

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def list_tags(self) -> list[str]:
        logger.debug("Retrieving existing tags")
        try:
            response = get(
                f"{self._config.server.url}/api/runs/{self._id}", self._headers
            )
        except Exception as err:
            self._error(f"Exception retrieving tags: {str(err)}")
            return []

        logger.debug(
            'Got status code %d when retrieving tags: "%s"',
            response.status_code,
            response.text,
        )

        if not (response_data := response.json()) or (
            (data := response_data.get("tags")) is None
        ):
            self._error(
                "Expected key 'tags' in response from server during alert retrieval"
            )
            return []

        if response.status_code == http.HTTPStatus.OK:
            return data

        return []

    @skip_if_failed("_aborted", "_suppress_errors", (None, None))
    def create_run(self, data) -> tuple[typing.Optional[str], typing.Optional[int]]:
        """
        Create a run
        """
        if data.get("folder") != "/":
            logger.debug("Creating folder %s if necessary", data.get("folder"))
            try:
                response = post(
                    f"{self._config.server.url}/api/folders",
                    self._headers,
                    {"path": data.get("folder")},
                )
            except Exception as err:
                self._error(f"Exception creating folder: {str(err)}")
                return (None, None)

            logger.debug(
                'Got status code %d when creating folder, with response: "%s"',
                response.status_code,
                response.text,
            )

            if response.status_code not in (
                http.HTTPStatus.OK,
                http.HTTPStatus.CONFLICT,
            ):
                self._error(f"Unable to create folder {data.get('folder')}")
                return (None, None)

        logger.debug('Creating run with data: "%s"', data)

        try:
            response = post(f"{self._config.server.url}/api/runs", self._headers, data)
        except Exception as err:
            self._error(f"Exception creating run: {str(err)}")
            return (None, None)

        logger.debug(
            'Got status code %d when creating run, with response: "%s"',
            response.status_code,
            response.text,
        )

        if response.status_code == http.HTTPStatus.CONFLICT:
            self._error(f"Duplicate run, name {data['name']} already exists")
            return (None, None)
        elif response.status_code != http.HTTPStatus.OK:
            self._error(f"Got status code {response.status_code} when creating run")
            return (None, None)

        if "name" in response.json():
            self._name = response.json()["name"]

        if "id" in response.json():
            self._id = response.json()["id"]

        return self._name, self._id

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def update(
        self, data: dict[str, typing.Any], _=None
    ) -> typing.Optional[dict[str, typing.Any]]:
        """
        Update metadata, tags or status
        """
        if self._id:
            data["id"] = self._id

        logger.debug('Updating run with data: "%s"', data)

        retry = True
        while retry:
            retry = False
            try:
                response = put(f"{self._config.server.url}/api/runs", self._headers, data)
            except Exception as err:
                if "409" in f"{err}":
                    print(f"Ignoring exception updating run as 409: {err}")
                    retry = True
                else:
                    self._error(f"Exception updating run: {err}")
                    return None

        logger.debug(
            'Got status code %d when updating run, with response: "%s"',
            response.status_code,
            response.text,
        )

        if response.status_code == http.HTTPStatus.OK:
            return data

        self._error(f"Got status code {response.status_code} when updating run")
        return None

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def set_folder_details(
        self, data, run=None
    ) -> typing.Optional[dict[str, typing.Any]]:
        """
        Set folder details
        """
        if run is not None and not __version__:
            data["name"] = run

        try:
            response = post(
                f"{self._config.server.url}/api/folders", self._headers, data
            )
        except Exception as err:
            self._error(f"Exception creating folder: {err}")
            return None

        if response.status_code in (http.HTTPStatus.OK, http.HTTPStatus.CONFLICT):
            folder_id = response.json()["id"]
            data["id"] = folder_id

            if response.status_code == http.HTTPStatus.OK:
                logger.debug('Got id of new folder: "%s"', folder_id)
            else:
                logger.debug('Got id of existing folder: "%s"', folder_id)

        logger.debug('Setting folder details with data: "%s"', data)

        try:
            response = put(
                f"{self._config.server.url}/api/folders", self._headers, data
            )
        except Exception as err:
            self._error(f"Exception setting folder details: {err}")
            return None

        logger.debug(
            'Got status code %d when setting folder details, with response: "%s"',
            response.status_code,
            response.text,
        )

        if response.status_code == http.HTTPStatus.OK:
            return response.json()

        self._error(
            f"Got status code {response.status_code} when updating folder details"
        )
        return None

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def save_file(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        """
        Save file
        """
        logger.debug('Getting presigned URL for saving artifact, with data: "%s"', data)

        # Get presigned URL
        try:
            response = post(
                f"{self._config.server.url}/api/artifacts",
                self._headers,
                prepare_for_api(data),
            )
        except Exception as err:
            self._error(
                f"Got exception when preparing to upload file {data['name']} to object storage: {str(err)}"
            )
            return None

        logger.debug(
            'Got status code %d when getting presigned URL, with response: "%s"',
            response.status_code,
            response.text,
        )

        if response.status_code == http.HTTPStatus.CONFLICT:
            return data

        if response.status_code != http.HTTPStatus.OK:
            self._error(
                f"Got status code {response.status_code} when registering file {data['name']}"
            )
            return None

        storage_id = None
        if "storage_id" in response.json():
            storage_id = response.json()["storage_id"]

        if not storage_id:
            return None

        if "url" in response.json():
            url = response.json()["url"]
            if "pickled" in data and "pickledFile" not in data:
                try:
                    response = put(
                        url, {}, data["pickled"], is_json=False, timeout=UPLOAD_TIMEOUT
                    )

                    logger.debug(
                        "Got status code %d when uploading artifact",
                        response.status_code,
                    )

                    if response.status_code != http.HTTPStatus.OK:
                        self._error(
                            f"Got status code {response.status_code} when uploading object {data['name']} to object storage"
                        )
                        return None
                except Exception as err:
                    self._error(
                        f"Got exception when uploading object {data['name']} to object storage: {str(err)}"
                    )
                    return None
            else:
                if "pickledFile" in data:
                    use_filename = data["pickledFile"]
                else:
                    use_filename = data["originalPath"]

                try:
                    with open(use_filename, "rb") as fh:
                        response = put(
                            url, {}, fh, is_json=False, timeout=UPLOAD_TIMEOUT
                        )

                        logger.debug(
                            "Got status code %d when uploading artifact",
                            response.status_code,
                        )

                        if response.status_code != http.HTTPStatus.OK:
                            self._error(
                                f"Got status code {response.status_code} when uploading file {data['name']} to object storage"
                            )
                            return None
                except Exception as err:
                    self._error(
                        f"Got exception when uploading file {data['name']} to object storage: {str(err)}"
                    )
                    return None

        if storage_id:
            path = f"{self._config.server.url}/api/runs/{self._id}/artifacts"
            data["storage"] = storage_id

            try:
                response = put(path, self._headers, prepare_for_api(data))
            except Exception as err:
                self._error(
                    f"Got exception when confirming upload of file {data['name']}: {str(err)}"
                )
                return None

            if response.status_code != http.HTTPStatus.OK:
                self._error(
                    f"Got status code {response.status_code} when confirming upload of file {data['name']}: {response.text}"
                )
                return None

        return data

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def add_alert(self, data, run=None):
        """
        Add an alert
        """
        if run is not None:
            data["run"] = run

        logger.debug('Adding alert with data: "%s"', data)

        try:
            response = post(
                f"{self._config.server.url}/api/alerts", self._headers, data
            )
        except Exception as err:
            self._error(f"Got exception when creating an alert: {str(err)}")
            return False

        logger.debug(
            'Got response %d when adding alert, with response: "%s"',
            response.status_code,
            response.text,
        )

        if response.status_code in (http.HTTPStatus.OK, http.HTTPStatus.CONFLICT):
            return response.json()

        self._error(f"Got status code {response.status_code} when creating alert")
        return False

    @skip_if_failed("_aborted", "_suppress_errors", {})
    def set_alert_state(
        self, alert_id, status
    ) -> typing.Optional[dict[str, typing.Any]]:
        """
        Set alert state
        """
        data = {"run": self._id, "alert": alert_id, "status": status}
        try:
            response = put(
                f"{self._config.server.url}/api/alerts/status", self._headers, data
            )
        except Exception as err:
            self._error(f"Got exception when setting alert state: {err}")
            return {}

        if response.status_code == http.HTTPStatus.OK:
            return response.json()

        return {}

    @skip_if_failed("_aborted", "_suppress_errors", [])
    def list_alerts(self) -> list[dict[str, typing.Any]]:
        """
        List alerts
        """
        try:
            response = get(f"{self._config.server.url}/api/alerts", self._headers)
        except Exception as err:
            self._error(f"Got exception when listing alerts: {str(err)}")
            return []

        if not (response_data := response.json()) or (
            (data := response_data.get("data")) is None
        ):
            self._error(
                "Expected key 'alerts' in response from server during alert retrieval"
            )
            return []

        if response.status_code == http.HTTPStatus.OK:
            return data

        return []

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def send_metrics(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        """
        Send metrics
        """
        logger.debug("Sending metrics")

        try:
            response = post(
                f"{self._config.server.url}/api/metrics",
                self._headers_mp,
                data,
                is_json=False,
            )
        except Exception as err:
            self._error(f"Exception sending metrics: {str(err)}")
            return None

        logger.debug("Got status code %d when sending metrics", response.status_code)

        if response.status_code == http.HTTPStatus.OK:
            return response.json()

        self._error(f"Got status code {response.status_code} when sending metrics")
        return None

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def send_event(
        self, data: dict[str, typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        """
        Send events
        """
        logger.debug("Sending events")

        try:
            response = post(
                f"{self._config.server.url}/api/events",
                self._headers_mp,
                data,
                is_json=False,
            )
        except Exception as err:
            self._error(f"Exception sending event: {str(err)}")
            return None

        logger.debug("Got status code %d when sending events", response.status_code)

        if response.status_code == http.HTTPStatus.OK:
            return response.json()

        self._error(f"Got status code {response.status_code} when sending events")
        return None

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def send_heartbeat(self) -> typing.Optional[dict[str, typing.Any]]:
        """
        Send heartbeat
        """
        logger.debug("Sending heartbeat")

        try:
            response = put(
                f"{self._config.server.url}/api/runs/heartbeat",
                self._headers,
                {"id": self._id},
            )
        except Exception as err:
            self._error(f"Exception creating run: {str(err)}")
            return None

        logger.debug("Got status code %d when sending heartbeat", response.status_code)

        if response.status_code == http.HTTPStatus.OK:
            return response.json()

        self._error(f"Got status code {response.status_code} when sending heartbeat")
        return None

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def get_abort_status(self) -> bool:
        logger.debug("Retrieving alert status")

        try:
            response = get(
                f"{self._config.server.url}/api/runs/{self._id}/abort", self._headers_mp
            )
        except Exception as err:
            self._error(f"Exception retrieving abort status: {str(err)}")
            return False

        logger.debug(
            "Got status code %d when checking abort status", response.status_code
        )

        if response.status_code == http.HTTPStatus.OK:
            if (status := response.json().get("status")) is None:
                self._error(
                    f"Expected key 'status' when retrieving abort status {response.json()}"
                )
                return False
            return status

        self._error(
            f"Got status code {response.status_code} when checking abort status"
        )
        return False
