import pytest
import pydantic
from simvue.models import AlertValidator
invalid_alerts = [
    # Invalid 'name' kwarg
    {
        "name": "test&failed",
        "source": "metrics",
        "rule": "is below",
        "frequency": 1,
        "window": 1,
        "threshold": 0.8,
    },
    # Missing required 'metric' kwarg
    {
        "name": "test_alert",
        "source": "metrics",
        "rule": "is below",
        "frequency": 1,
        "window": 1,
        "threshold": 0.8,
    },
    # Cannot have 'pattern' kwarg when source is metrics
    {
        "name": "test_alert",
        "source": "metrics",
        "pattern": "wrong",
        "rule": "is below",
        "metric": "accuracy",
        "frequency": 1,
        "window": 1,
        "threshold": 0.8,
    },
    # Invalid 'source' input
    {
        "name": "test_alert",
        "source": "alerts",
        "rule": "is below",
        "metric": "accuracy",
        "frequency": 1,
        "window": 1,
        "threshold": 0.8,
    },
    # Invalid type for 'frequency'
    {
        "name": "test_alert",
        "source": "metrics",
        "rule": "is below",
        "metric": "accuracy",
        "frequency": "one",
        "window": 1,
        "threshold": 0.8,
    },
    # Unexpected kwarg 'new'
    {
        "name": "test_alert",
        "source": "metrics",
        "rule": "is below",
        "metric": "accuracy",
        "frequency": "one",
        "window": 1,
        "threshold": 0.8,
        "new": "hi"
    },
    # Shouldn't specify aggregation when source is events
    {
        "name": "test_alert",
        "source": "events",
        "frequency": 1,
        "pattern": "test pattern",
        "aggregation": "sum",
    },
    # Shouldn't specify frequency (or any other things) when source is user
    {
        "name": "test_alert",
        "source": "user",
        "frequency": 1,
    },
]

valid_alerts = [
    # Valid metric based alert, no optional inputs specified
    {
        "name": "test_alert",
        "source": "metrics",
        "metric": "my_fraction",
        "rule": "is below",
        "frequency": 1,
        "window": 1,
        "threshold": 0.8,
    },
    # Valid metric based range alert, no optional inputs specified
    {
        "name": "test_alert",
        "source": "metrics",
        "metric": "my_fraction",
        "rule": "is outside range",
        "frequency": 1,
        "window": 1,
        "range_low": 0.8,
        "range_high": 0.9
    },
    # Valid metric based alert, all optional inputs specified
    {
        "name": "test_alert",
        "source": "metrics",
        "metric": "my_fraction",
        "rule": "is below",
        "frequency": 1,
        "window": 1,
        "threshold": 0.8,
        "description": "My test alert",
        "aggregation": "all",
        "notification": "email",
        "trigger_abort": True
    },
    # Valid events based alert, no optional inputs specified
    {
        "name": "test_alert",
        "source": "events",
        "frequency": 1,
        "pattern": "Look for this pattern!",
    },
    # Valid events based alert, all optional inputs specified
    {
        "name": "test_alert",
        "source": "events",
        "frequency": 1,
        "pattern": "Look for this pattern!",
        "description": "My test alert",
        "notification": "email",
        "trigger_abort": True
    },
    # Valid user alert, no optional inputs specified
    {
        "name": "test_alert",
        "source": "user",
    },
    # Valid user alert, all optional inputs specified
    {
        "name": "test_alert",
        "source": "user",
        "description": "My test alert",
        "notification": "email",
        "trigger_abort": True
    },
]

@pytest.mark.parametrize("alert_definitions", invalid_alerts)
def test_invalid_alert_configs(alert_definitions):
    with pytest.raises(pydantic.ValidationError):
        AlertValidator(**alert_definitions)
        
@pytest.mark.parametrize("alert_definitions", valid_alerts)
def test_valid_alert_configs(alert_definitions):
    AlertValidator(**alert_definitions)