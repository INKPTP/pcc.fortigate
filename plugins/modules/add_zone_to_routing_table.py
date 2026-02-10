from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress

def main():
    module_args = dict(
        zone=dict(type="list", required=True),
        routing_table=dict(type="list", required=True),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    zone_data = module.params["zone"]
    routing_table_data = module.params["routing_table"]
    
    # Build interface to zone mapping with vdom support
    routing_to_zone = {}
    for result in zone_data:
        vdom = result.get('item', 'unknown')
        zones = result.get('json', {}).get('results', [])
        
        for zone in zones:
            zone_name = zone.get('name')
            if not zone_name:
                continue
                
            for interface in zone.get('interface', []):
                interface_name = interface.get('interface-name')
                if interface_name:
                    # Store both zone name and vdom
                    key = f"{vdom}:{interface_name}"
                    routing_to_zone[key] = zone_name
                    # Also store without vdom for backward compatibility
                    if interface_name not in routing_to_zone:
                        routing_to_zone[interface_name] = zone_name

    # Add zone name to each interface
    matched = 0
    for interface in routing_table_data["routing_table"]:
        interface_name = interface.get('name') or interface.get('interface')
        vdom = interface.get('vdom', 'unknown')
        
        # Try with vdom first, then without
        key_with_vdom = f"{vdom}:{interface_name}"
        zone = routing_to_zone.get(key_with_vdom) or routing_to_zone.get(interface_name, 'No-Zone')
        
        interface['zone'] = zone
        if zone != 'No-Zone':
            matched += 1

    module.exit_json(changed=False, result=routing_table_data)


if __name__ == "__main__":

    main()
