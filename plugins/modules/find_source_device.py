from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress
import json

def _parse_source(source_ip: str):
    """Return (source_network, source_ip_obj) where one of them is set."""

    try:
        if "/" in source_ip:
            try:
                return ipaddress.ip_network(source_ip, strict=False), None
            except ValueError:
                iface = ipaddress.ip_interface(source_ip)
                return iface.network, iface
        if "-" in source_ip:
            start_ip, end_ip = source_ip.split("-", 1)
            start_ip_obj = ipaddress.ip_address(start_ip.strip())
            end_ip_obj = ipaddress.ip_address(end_ip.strip())
            if type(start_ip_obj) is not type(end_ip_obj):
                raise ValueError("Start and end IP addresses are of different types")
            # Create the smallest network that includes both IPs
            combined = ipaddress.summarize_address_range(start_ip_obj, end_ip_obj)
            # Return the first network in the summary (there should be only one)
            return next(combined), None
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
        source_ip_list=dict(type="list", required=True),
        destination_ip_list=dict(type="list", required=True),
        service_list=dict(type="list", required=True),
        device_list=dict(type="list", required=True),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
        
    source_ip_list = module.params["source_ip_list"]
    destination_ip_list = module.params["destination_ip_list"]
    service_list = module.params["service_list"]
    device_list = module.params["device_list"]
    matched_device_list = []
    matched_device_map = {}  # Track matches by device+interface
    
    temp_destination_ip_list = []
    for destination_ip in destination_ip_list:
        try:
            dest_network, dest_iface = _parse_source(destination_ip)
            temp_destination_ip_list.append(str(dest_network))
        except ValueError as exc:
            # pass
            module.fail_json(msg=str(exc))
    destination_ip_list = temp_destination_ip_list
    
    for source_ip in source_ip_list:
        try:
            source_network, source_iface = _parse_source(source_ip)
        except ValueError as exc:
            # pass
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
                        # Create unique key for device+interface
                        interface_name = interface.get("name") or interface.get("interface")
                        match_key = f"{device_name}:{interface_name}"
                        
                        if match_key in matched_device_map:
                            # Merge with existing match
                            existing_match = matched_device_map[match_key]
                            existing_match["source"].append(str(source_network))
                            if str(source_network) not in existing_match["source"]:
                                existing_match["source"] = f"{existing_match['source']}, {source_network}"
                            matched_device = existing_match
                        else:
                            # Create new match
                            matched_device = {
                                "source": [str(source_network)],
                                "destination": destination_ip_list,
                                "service": service_list,
                                "device_name": device_name,
                                "source_device": True,
                                "source_interface": interface,
                                "device_info": device,
                            }
                            matched_device_map[match_key] = matched_device
                            matched_device_list.append(matched_device)
                        break
                if matched_device:
                    break
            if matched_device:
                break

        if not matched_device:
            matched_device = {
                "source": [str(source_network)],
                "destination": destination_ip_list,
                "service": service_list,
                "device_name": None,
                "source_device": False,
                "source_interface": None,
                "device_info": None,
            }
            matched_device_list.append(matched_device)

        
    # print(json.dumps(matched_device_list, indent=2))

    module.exit_json(changed=False, result=matched_device_list)


if __name__ == "__main__":
    main()
