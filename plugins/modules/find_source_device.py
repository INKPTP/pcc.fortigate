from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress


def _parse_source(source_ip: str):
    """Return (source_network, source_ip_obj) where one of them is set."""

    try:
        if "/" in source_ip:
            try:
                return ipaddress.ip_network(source_ip, strict=False), None
            except ValueError:
                iface = ipaddress.ip_interface(source_ip)
                return iface.network, iface
        iface = ipaddress.ip_interface(f"{source_ip}/32")
        return iface.network, iface
    except ValueError as exc:
        raise ValueError(f"Invalid source_ip '{source_ip}': {exc}") from exc


def _interface_networks(interface):
    """Yield ip_network objects from interface definitions.

    Supports:
    - interface['ip'] as a string with or without prefix; optional interface['subnet'].
    - interface['ip'] as a list of strings with prefixes.
    Silently skips invalid or missing data instead of failing the module.
    """

    ip_field = interface.get("ip")
    subnet = interface.get("subnet")

    def _to_network(ip_value):
        if ip_value is None:
            return None
        try:
            if "/" in ip_value:
                return ipaddress.ip_network(ip_value, strict=False)
            if subnet is not None:
                return ipaddress.ip_network(f"{ip_value}/{subnet}", strict=False)
        except ValueError:
            return None
        return None

    if isinstance(ip_field, list):
        for ip_value in ip_field:
            net = _to_network(ip_value)
            if net:
                yield net
    else:
        net = _to_network(ip_field)
        if net:
            yield net


def main():
    module_args = dict(
        source_ip=dict(type="str", required=True),
        device_list=dict(type="list", required=True),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    source_ip_raw = module.params["source_ip"]
    device_list = module.params["device_list"]

    try:
        source_network, source_iface = _parse_source(source_ip_raw)
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    matched_device = None

    for device in device_list:
        device_name = device.get("device_name") or device.get("name")
        for interface in device.get("interfaces", []):
            for interface_network in _interface_networks(interface):
                in_same_network = (
                    (source_iface and source_iface.ip in interface_network)
                    or (not source_iface and source_network.overlaps(interface_network))
                )

                if in_same_network:
                    matched_device = {
                        "source": str(source_network),
                        "device_name": device_name,
                        "source_device": True,
                        "interface": interface,
                        "device_info": device,
                    }
                    break
            if matched_device:
                break
        if matched_device:
            break

    if not matched_device:
        matched_device = {
            "source": str(source_network),
            "device_name": None,
            "source_device": False,
            "interface": None,
            "device_info": None,
        }

    module.exit_json(changed=False, result=matched_device)


if __name__ == "__main__":
    main()
