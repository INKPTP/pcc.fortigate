from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress


def _parse_source(source_ip: str):
    """Return (source_network, source_ip_obj) where one of them is set.

    - If `source_ip` includes a prefix, treat it as a network first; if that fails,
      treat it as an interface.
    - If no prefix is provided, treat it as a host /32.
    """

    try:
        if "/" in source_ip:
            # Prefer interpreting as network; fallback to interface
            try:
                return ipaddress.ip_network(source_ip, strict=False), None
            except ValueError:
                iface = ipaddress.ip_interface(source_ip)
                return iface.network, iface
        iface = ipaddress.ip_interface(f"{source_ip}/32")
        return iface.network, iface
    except ValueError as exc:  # surface clean error to Ansible
        raise ValueError(f"Invalid source_ip '{source_ip}': {exc}") from exc


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
            try:
                interface_ip = interface["ip"]
                interface_subnet = interface["subnet"]
                interface_network = ipaddress.ip_network(
                    f"{interface_ip}/{interface_subnet}", strict=False
                )
            except KeyError as exc:
                module.fail_json(
                    msg=f"Missing interface field {exc} for device {device_name}"
                )
            except ValueError as exc:
                module.fail_json(
                    msg=f"Invalid interface definition for device {device_name}: {exc}"
                )

            # Match if host IP is inside network, or if provided source is a network that overlaps
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
