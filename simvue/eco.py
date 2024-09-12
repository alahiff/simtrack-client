import typing
import datetime
from codecarbon import EmissionsTracker
from codecarbon.external.logger import logging
from codecarbon.output_methods.base_output import BaseOutput as cc_BaseOutput
from simvue.utilities import simvue_timestamp

if typing.TYPE_CHECKING:
    from simvue import Run
    from codecarbon.output_methods.emissions_data import EmissionsData


class CodeCarbonOutput(cc_BaseOutput):
    def __init__(self, run: "Run") -> None:
        self._meta_update: bool = True
        self._simvue_run = run
        self._metrics_step: int = 0

    def out(self, total: "EmissionsData", delta: "EmissionsData") -> None:
        # Check if the run has been shutdown, if so do nothing
        if (
            self._simvue_run._shutdown_event
            and self._simvue_run._shutdown_event.is_set()
        ):
            return

        if self._meta_update:
            self._simvue_run.update_metadata(
                {
                    "codecarbon.country": total.country_name,
                    "codecarbon.country_iso_code": total.country_iso_code,
                    "codecarbon.region": total.region,
                    "codecarbon.version": total.codecarbon_version,
                }
            )
            self._meta_update = False

        _cc_timestamp: datetime.datetime = datetime.datetime.strptime(
            total.timestamp, "%Y-%m-%dT%H:%M:%S"
        )

        self._simvue_run.log_metrics(
            metrics={
                "codecarbon.emissions.total": total.emissions,
                "codecarbon.energy_consumed.total": total.energy_consumed,
                "codecarbon.emissions.delta": delta.emissions,
                "codecarbon.energy_consumed.delta": delta.energy_consumed,
            },
            step=self._metrics_step,
            timestamp=simvue_timestamp(_cc_timestamp),
        )
        self._metrics_step += 1

    def live_out(self, total: "EmissionsData", delta: "EmissionsData") -> None:
        self.out(total, delta)


class SimvueEmissionsTracker(EmissionsTracker):
    def __init__(
        self, project_name: str, simvue_run: "Run", metrics_interval: int
    ) -> None:
        self._simvue_run = simvue_run
        super().__init__(
            project_name=project_name,
            measure_power_secs=metrics_interval,
            experiment_id=None,
            experiment_name=None,
            logging_logger=CodeCarbonOutput(simvue_run),
            save_to_logger=True,
            allow_multiple_runs=True,
            log_level=logging.ERROR,
        )

    def set_measure_interval(self, interval: int) -> None:
        """Set the measure interval"""
        self._set_from_conf(interval, "measure_power_secs")

    def post_init(self) -> None:
        self._set_from_conf(self._simvue_run._id, "experiment_id")
        self._set_from_conf(self._simvue_run._name, "experiment_name")
