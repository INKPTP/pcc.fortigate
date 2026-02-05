from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress

def main():
    module_args = dict(
        source_ip=dict(type="str", required=True),
        device_list=dict(type="list", required=True)
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    source = module.params["source_ip"]
    device_list = module.params["device_list"]

    try:
        source_interface = ipaddress.ip_interface(source if "/" in source else f"{source}/32")
    except ValueError as exc:
        module.fail_json(msg=f"Invalid source_ip: {exc}")

    matched_device = None

    for device in device_list:
        for interface in device.get("interfaces", []):
            try:
                interface_ip = interface["ip"]
                interface_subnet = interface["subnet"]
                network = ipaddress.ip_network(f"{interface_ip}/{interface_subnet}", strict=False)
            except KeyError as exc:
                module.fail_json(msg=f"Missing interface field {exc} for device {device.get('name')}")
            except ValueError as exc:
                module.fail_json(msg=f"Invalid interface definition for device {device.get('name')}: {exc}")

            if source_interface.ip in network:
                matched_device = {
                    "source_ip": str(source_interface.ip),
                    "device_name": device.get("name"),
                    "source_device": True,
                    "device_info": device,
                }
                break
        if matched_device:
            break

    if not matched_device:
        matched_device = {
            "source_ip": str(source_interface.ip),
            "device": None,
            "source_device": False,
            "device_info": device,
        }

    module.exit_json(changed=False, result=matched_device)

if __name__ == "__main__":
    main()
