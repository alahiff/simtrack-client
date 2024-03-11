import codecs
import json
import logging
import os
import time
import uuid

from .utilities import (
    create_file,
    get_offline_directory,
    prepare_for_api,
    skip_if_failed,
)

logger = logging.getLogger(__name__)


class Offline(object):
    """
    Class for offline runs
    """

    def __init__(self, name, uuid, id, suppress_errors=False):
        self._id = id
        self._name = name
        self._uuid = uuid
        self._aborted: bool = False
        self._directory = os.path.join(get_offline_directory(), self._uuid)
        self._suppress_errors = suppress_errors

    def _error(self, message):
        """
        Raise an exception if necessary and log error
        """
        if not self._suppress_errors:
            raise RuntimeError(message)
        else:
            logger.error(message)

        self._aborted = True

    def _write_json(self, filename, data):
        """
        Write JSON to file
        """
        try:
            with open(filename, "w") as fh:
                json.dump(data, fh)
        except Exception as err:
            self._error(f"Unable to write file {filename} due to {str(err)}")

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def create_run(self, data):
        """
        Create a run
        """
        try:
            os.makedirs(self._directory, exist_ok=True)
        except Exception as err:
            logger.error(
                "Unable to create directory %s due to: %s", self._directory, str(err)
            )

        filename = f"{self._directory}/run.json"
        if "name" not in data:
            data["name"] = None

        self._write_json(filename, data)

        status = data["status"]
        filename = f"{self._directory}/{status}"
        create_file(filename)

        return True

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def update(self, data):
        """
        Update metadata, tags or status
        """
        unique_id = time.time()
        filename = f"{self._directory}/update-{unique_id}.json"
        self._write_json(filename, data)

        if "status" in data:
            status = data["status"]
            filename = f"{self._directory}/{status}"
            create_file(filename)

            if status == "completed":
                status_running = f"{self._directory}/running"
                if os.path.isfile(status_running):
                    os.remove(status_running)

        return True

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def set_folder_details(self, data):
        """
        Set folder details
        """
        unique_id = time.time()
        filename = f"{self._directory}/folder-{unique_id}.json"
        self._write_json(filename, data)
        return True

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def save_file(self, data):
        """
        Save file
        """
        if "pickled" in data:
            temp_file = f"{self._directory}/temp-{str(uuid.uuid4())}.pickle"
            with open(temp_file, "wb") as fh:
                fh.write(data["pickled"])
            data["pickledFile"] = temp_file
        unique_id = time.time()
        filename = f"{self._directory}/file-{unique_id}.json"
        self._write_json(filename, prepare_for_api(data, False))
        return True

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def add_alert(self, data):
        """
        Add an alert
        """
        unique_id = time.time()
        filename = f"{self._directory}/alert-{unique_id}.json"
        self._write_json(filename, data)
        return True
