"""Python client for the Lboro Sport / Legend facility-hire API."""

from .bootstrap import (
    BootstrapClient,
    BootstrapError,
    BootstrapHTTPError,
    LegendConfig,
    run_bootstrap,
    write_env,
)
from .client import LegendAPIError, LegendAuthError, LegendClient, LegendError
from .models import (
    Activity,
    ActivityConcept,
    AvailabilityItem,
    AvailabilityResponse,
    Facility,
    Location,
    Offer,
    Order,
    OrderItem,
    Reservation,
    Slot,
    SlotDetail,
)

__version__ = "0.1.0"

__all__ = [
    "Activity",
    "ActivityConcept",
    "AvailabilityItem",
    "AvailabilityResponse",
    "BootstrapClient",
    "BootstrapError",
    "BootstrapHTTPError",
    "Facility",
    "LegendAPIError",
    "LegendAuthError",
    "LegendClient",
    "LegendConfig",
    "LegendError",
    "Location",
    "Offer",
    "Order",
    "OrderItem",
    "Reservation",
    "Slot",
    "SlotDetail",
    "__version__",
    "run_bootstrap",
    "write_env",
]
