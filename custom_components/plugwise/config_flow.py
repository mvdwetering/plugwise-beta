"""Config flow for Plugwise integration."""
from __future__ import annotations

import datetime as dt  # pw-beta options
from typing import Any

import serial.tools.list_ports  # pw-beta usb
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import usb  # pw-beta usb
from homeassistant.components.zeroconf import ZeroconfServiceInfo
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import (
    CONF_BASE,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from plugwise.exceptions import (
    ConnectionFailedError,
    InvalidAuthentication,
    InvalidSetupError,
    InvalidXMLError,
    ResponseError,
    UnsupportedDeviceError,
)

# pw-beta Note; the below are explicit through isort
from plugwise.exceptions import NetworkDown  # pw-beta usb
from plugwise.exceptions import PortError  # pw-beta usb
from plugwise.exceptions import StickInitError  # pw-beta usb
from plugwise.exceptions import TimeoutException  # pw-beta usb
from plugwise.smile import Smile
from plugwise.stick import Stick  # pw-beta usb

from .const import (
    API,
    COORDINATOR,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
    FLOW_SMILE,
    FLOW_STRETCH,
    PW_TYPE,
    SMILE,
    STRETCH,
    STRETCH_USERNAME,
    ZEROCONF_MAP,
)

# pw-beta Note; the below are explicit through isort
from .const import CONF_HOMEKIT_EMULATION  # pw-beta option
from .const import CONF_MANUAL_PATH  # pw-beta usb
from .const import CONF_REFRESH_INTERVAL  # pw-beta option
from .const import CONF_USB_PATH  # pw-beta usb
from .const import DEFAULT_SCAN_INTERVAL  # pw-beta option
from .const import FLOW_NET  # pw-beta usb
from .const import FLOW_TYPE  # pw-beta usb
from .const import FLOW_USB  # pw-beta usb
from .const import STICK  # pw-beta usb

CONNECTION_SCHEMA = vol.Schema(
    {vol.Required(FLOW_TYPE, default=FLOW_NET): vol.In([FLOW_NET, FLOW_USB])}
)  # pw-beta usb


@callback
def plugwise_stick_entries(hass):  # pw-beta usb
    """Return existing connections for Plugwise USB-stick domain."""
    sticks = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(PW_TYPE) == STICK:
            sticks.append(entry.data.get(CONF_USB_PATH))
    return sticks


# Github issue: #265
# Might be a `tuple[dict[str, str], Stick | None]` for typing, but that throws
# Item None of Optional[Any] not having attribute mac [union-attr]
async def validate_usb_connection(
    self, device_path=None
) -> tuple[dict[str, str], Any]:  # pw-beta usb
    """Test if device_path is a real Plugwise USB-Stick."""
    errors = {}

    # Avoid creating a 2nd connection to an already configured stick
    if device_path in plugwise_stick_entries(self):
        errors[CONF_BASE] = "already_configured"
        return errors, None

    api_stick = await self.async_add_executor_job(Stick, device_path)
    try:
        await self.async_add_executor_job(api_stick.connect)
        await self.async_add_executor_job(api_stick.initialize_stick)
        await self.async_add_executor_job(api_stick.disconnect)
    except PortError:
        errors[CONF_BASE] = "cannot_connect"
    except StickInitError:
        errors[CONF_BASE] = "stick_init"
    except NetworkDown:
        errors[CONF_BASE] = "network_down"
    except TimeoutException:
        errors[CONF_BASE] = "network_timeout"
    return errors, api_stick


def _base_gw_schema(
    discovery_info: ZeroconfServiceInfo | None,
    user_input: dict[str, Any] | None,
) -> vol.Schema:
    """Generate base schema for gateways."""
    if not discovery_info:
        if not user_input:
            return vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Required(CONF_USERNAME, default=SMILE): vol.In(
                        {SMILE: FLOW_SMILE, STRETCH: FLOW_STRETCH}
                    ),
                }
            )
        return vol.Schema(
            {
                vol.Required(CONF_PASSWORD, default=user_input[CONF_PASSWORD]): str,
                vol.Required(CONF_HOST, default=user_input[CONF_HOST]): str,
                vol.Optional(CONF_PORT, default=user_input[CONF_PORT]): int,
                vol.Required(CONF_USERNAME, default=user_input[CONF_USERNAME]): vol.In(
                    {SMILE: FLOW_SMILE, STRETCH: FLOW_STRETCH}
                ),
            }
        )

    return vol.Schema({vol.Required(CONF_PASSWORD): str})


async def validate_gw_input(hass: HomeAssistant, data: dict[str, Any]) -> Smile:
    """Validate whether the user input allows us to connect to the gateway.

    Data has the keys from _base_gw_schema() with values provided by the user.
    """
    websession = async_get_clientsession(hass, verify_ssl=False)
    api = Smile(
        host=data[CONF_HOST],
        password=data[CONF_PASSWORD],
        port=data[CONF_PORT],
        username=data[CONF_USERNAME],
        timeout=30,
        websession=websession,
    )
    await api.connect()
    return api


class PlugwiseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Plugwise Smile."""

    VERSION = 1

    discovery_info: ZeroconfServiceInfo | None = None
    _username: str = DEFAULT_USERNAME

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Prepare configuration for a discovered Plugwise Smile."""
        self.discovery_info = discovery_info
        _properties = discovery_info.properties

        unique_id = discovery_info.hostname.split(".")[0].split("-")[0]
        if config_entry := await self.async_set_unique_id(unique_id):
            try:
                await validate_gw_input(
                    self.hass,
                    {
                        CONF_HOST: discovery_info.host,
                        CONF_PORT: discovery_info.port,
                        CONF_USERNAME: config_entry.data[CONF_USERNAME],
                        CONF_PASSWORD: config_entry.data[CONF_PASSWORD],
                    },
                )
            except Exception:  # pylint: disable=broad-except
                self._abort_if_unique_id_configured()
            else:
                self._abort_if_unique_id_configured(
                    {
                        CONF_HOST: discovery_info.host,
                        CONF_PORT: discovery_info.port,
                    }
                )

        if DEFAULT_USERNAME not in unique_id:
            self._username = STRETCH_USERNAME
        _product = _properties.get("product", None)
        _version = _properties.get("version", "n/a")
        _name = f"{ZEROCONF_MAP.get(_product, _product)} v{_version}"

        # This is an Anna, but we already have config entries.
        # Assuming that the user has already configured Adam, aborting discovery.
        if self._async_current_entries() and _product == "smile_thermo":
            return self.async_abort(reason="anna_with_adam")

        # If we have discovered an Adam or Anna, both might be on the network.
        # In that case, we need to cancel the Anna flow, as the Adam should
        # be added.
        for flow in self._async_in_progress():
            # This is an Anna, and there is already an Adam flow in progress
            if (
                _product == "smile_thermo"
                and "context" in flow
                and flow["context"].get("product") == "smile_open_therm"
            ):
                return self.async_abort(reason="anna_with_adam")

            # This is an Adam, and there is already an Anna flow in progress
            if (
                _product == "smile_open_therm"
                and "context" in flow
                and flow["context"].get("product") == "smile_thermo"
                and "flow_id" in flow
            ):
                self.hass.config_entries.flow.async_abort(flow["flow_id"])

        self.context.update(
            {
                "title_placeholders": {
                    CONF_HOST: discovery_info.host,
                    CONF_NAME: _name,
                    CONF_PORT: discovery_info.port,
                    CONF_USERNAME: self._username,
                },
                "configuration_url": f"http://{discovery_info.host}:{discovery_info.port}",
                "product": _product,
            }
        )
        return await self.async_step_user_gateway()

    async def async_step_user_usb(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:  # pw-beta usb
        """Step when user initializes a integration."""
        errors: dict[str, str] = {}
        ports = await self.hass.async_add_executor_job(serial.tools.list_ports.comports)
        list_of_ports = [
            f"{p}, s/n: {p.serial_number or 'n/a'}"
            + (f" - {p.manufacturer}" if p.manufacturer else "")
            for p in ports
        ]
        list_of_ports.append(CONF_MANUAL_PATH)

        if user_input is not None:
            user_input.pop(FLOW_TYPE, None)
            user_selection = user_input[CONF_USB_PATH]

            if user_selection == CONF_MANUAL_PATH:
                return await self.async_step_manual_path()

            port = ports[list_of_ports.index(user_selection)]
            device_path = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, port.device
            )
            errors, api_stick = await validate_usb_connection(self.hass, device_path)
            if not errors:
                await self.async_set_unique_id(api_stick.mac)
                return self.async_create_entry(
                    title="Stick", data={CONF_USB_PATH: device_path, PW_TYPE: STICK}
                )
        return self.async_show_form(
            step_id="user_usb",
            data_schema=vol.Schema(
                {vol.Required(CONF_USB_PATH): vol.In(list_of_ports)}
            ),
            errors=errors,
        )

    async def async_step_manual_path(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:  # pw-beta usb
        """Step when manual path to device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input.pop(FLOW_TYPE, None)
            device_path = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, user_input.get(CONF_USB_PATH)
            )
            errors, api_stick = await validate_usb_connection(self.hass, device_path)
            if not errors:
                await self.async_set_unique_id(api_stick.mac)
                return self.async_create_entry(
                    title="Stick", data={CONF_USB_PATH: device_path}
                )
        return self.async_show_form(
            step_id="manual_path",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USB_PATH, default="/dev/ttyUSB0" or vol.UNDEFINED
                    ): str
                }
            ),
            errors=errors,
        )

    async def async_step_user_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step when using network/gateway setups."""
        errors: dict[str, str] = {}

        if not user_input:
            return self.async_show_form(
                step_id="user_gateway",
                data_schema=_base_gw_schema(self.discovery_info, None),
                errors=errors,
            )

        if self.discovery_info:
            user_input[CONF_HOST] = self.discovery_info.host
            user_input[CONF_PORT] = self.discovery_info.port
            user_input[CONF_USERNAME] = self._username
        try:
            api = await validate_gw_input(self.hass, user_input)
        except ConnectionFailedError:
            errors[CONF_BASE] = "cannot_connect"
        except InvalidAuthentication:
            errors[CONF_BASE] = "invalid_auth"
        except InvalidSetupError:
            errors[CONF_BASE] = "invalid_setup"
        except (InvalidXMLError, ResponseError):
            errors[CONF_BASE] = "response_error"
        except UnsupportedDeviceError:
            errors[CONF_BASE] = "unsupported"
        except Exception:  # pylint: disable=broad-except
            errors[CONF_BASE] = "unknown"

        if errors:
            return self.async_show_form(
                step_id="user_gateway",
                data_schema=_base_gw_schema(None, user_input),
                errors=errors,
            )

        await self.async_set_unique_id(
            api.smile_hostname or api.gateway_id, raise_on_progress=False
        )
        self._abort_if_unique_id_configured()

        user_input[PW_TYPE] = API
        return self.async_create_entry(title=api.smile_name, data=user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step when using network/gateway setups."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[FLOW_TYPE] == FLOW_NET:
                return await self.async_step_user_gateway()

            if user_input[FLOW_TYPE] == FLOW_USB:
                return await self.async_step_user_usb()

        return self.async_show_form(
            step_id="user",
            data_schema=CONNECTION_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> config_entries.OptionsFlow:  # pw-beta options
        """Get the options flow for this handler."""
        return PlugwiseOptionsFlowHandler(config_entry)


# pw-beta - change the scan-interval via CONFIGURE
# pw-beta - add homekit emulation via CONFIGURE
# pw-beta - change the frontend refresh interval via CONFIGURE
class PlugwiseOptionsFlowHandler(config_entries.OptionsFlow):  # pw-beta options
    """Plugwise option flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:  # pragma: no cover
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_none(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:  # pragma: no cover
        """No options available."""
        if user_input is not None:
            # Apparently not possible to abort an options flow at the moment
            return self.async_create_entry(title="", data=self.config_entry.options)

        return self.async_show_form(step_id="none")

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:  # pragma: no cover
        """Manage the Plugwise options."""
        if not self.config_entry.data.get(CONF_HOST):
            return await self.async_step_none(user_input)

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id][COORDINATOR]
        interval: dt.timedelta = DEFAULT_SCAN_INTERVAL[
            coordinator.api.smile_type
        ]  # pw-beta options

        data = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_SCAN_INTERVAL, interval.seconds
                ),
            ): vol.All(cv.positive_int, vol.Clamp(min=10)),
        }  # pw-beta

        if coordinator.api.smile_type != "thermostat":
            return self.async_show_form(step_id="init", data_schema=vol.Schema(data))

        data.update(
            {
                vol.Optional(
                    CONF_HOMEKIT_EMULATION,
                    default=self.config_entry.options.get(
                        CONF_HOMEKIT_EMULATION, False
                    ),
                ): cv.boolean,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=self.config_entry.options.get(CONF_REFRESH_INTERVAL, 1.5),
                ): vol.All(vol.Coerce(float), vol.Range(min=1.5, max=10.0)),
            }
        )  # pw-beta

        return self.async_show_form(step_id="init", data_schema=vol.Schema(data))
