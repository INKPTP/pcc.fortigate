from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
from ipaddress import IPv4Network, IPv4Address, IPv4Interface

def ip_in_same_subnet(ip1, subnet1, ip2, subnet2):
    """Check if two IPs are in the same subnet"""
    try:
        net1 = IPv4Network(f"{ip1}/{subnet1}", strict=False)
        net2 = IPv4Network(f"{ip2}/{subnet2}", strict=False)
        return net1.network_address == net2.network_address and net1.netmask == net2.netmask
    except:
        return False

def normalize_interface(interface):
    """Normalize interface data to handle multiple formats"""
    normalized_list = []
    
    # Get interface name (support both 'name' and 'interface' keys)
    interface_name = interface.get('name') or interface.get('interface', 'unknown')
    
    # Handle different IP formats
    ip_data = interface.get('ip')
    
    if isinstance(ip_data, list):
        # New format: list of CIDR strings
        for cidr in ip_data:
            try:
                cidr = cidr.strip().rstrip('.')
                iface = IPv4Interface(cidr)
                normalized_list.append({
                    'name': interface_name,
                    'ip': str(iface.ip),
                    'subnet': str(iface.network.netmask),
                    'cidr': str(iface.network)
                })
            except Exception as e:
                print(f"Warning: Could not parse IP '{cidr}': {e}")
                continue
    elif isinstance(ip_data, str):
        # IP as string (might be CIDR or plain IP)
        if '/' in ip_data:
            try:
                iface = IPv4Interface(ip_data)
                normalized_list.append({
                    'name': interface_name,
                    'ip': str(iface.ip),
                    'subnet': str(iface.network.netmask),
                    'cidr': str(iface.network)
                })
            except Exception as e:
                print(f"Warning: Could not parse IP '{ip_data}': {e}")
        else:
            # Plain IP with subnet in separate field
            subnet = interface.get('subnet', '255.255.255.0')
            normalized_list.append({
                'name': interface_name,
                'ip': ip_data,
                'subnet': subnet,
                'cidr': str(IPv4Network(f"{ip_data}/{subnet}", strict=False))
            })
    else:
        # Old format: separate ip and subnet fields
        ip_addr = interface.get('ip')
        subnet = interface.get('subnet', '255.255.255.0')
        if ip_addr:
            normalized_list.append({
                'name': interface_name,
                'ip': ip_addr,
                'subnet': subnet,
                'cidr': str(IPv4Network(f"{ip_addr}/{subnet}", strict=False))
            })
    
    return normalized_list

def find_device_connections(devices):
    
    """Find all connections between devices based on subnet matching"""
    connections = {}
    
    # Initialize connections dictionary for each device
    for device in devices:
        connections[device['name']] = []
    
    # Normalize all interfaces first
    normalized_devices = []
    for device in devices:
        normalized_interfaces = []
        for interface in device['interfaces']:
            normalized_interfaces.extend(normalize_interface(interface))
        normalized_devices.append({
            'name': device['name'],
            'interfaces': normalized_interfaces
        })
        
    for i, device1 in enumerate(normalized_devices):
        for interface1 in device1['interfaces']:
            for device2 in normalized_devices[i+1:]:
                for interface2 in device2['interfaces']:
                    if interface1['ip'] == interface2['ip']:
                        continue  # Skip if same IP 
                    if ip_in_same_subnet(
                        interface1['ip'], interface1['subnet'],
                        interface2['ip'], interface2['subnet']
                    ):
                        # Add edge with subnet label
                        subnet = IPv4Network(f"{interface1['ip']}/{interface1['subnet']}", strict=False)
                        label = f"{interface1['name']}\n{str(subnet)}\n{interface2['name']}"
                        connections[device1['name']].append({
                            'local_device': device1['name'],
                            'local_interface': interface1['name'],
                            'local_ip': interface1['ip'],
                            'remote_device': device2['name'],
                            'remote_interface': interface2['name'],
                            'remote_ip': interface2['ip'],
                            'subnet': str(subnet)
                        })
                        connections[device2['name']].append({
                            'local_device': device2['name'],
                            'local_interface': interface2['name'],
                            'local_ip': interface2['ip'],
                            'remote_device': device1['name'],
                            'remote_interface': interface1['name'],
                            'remote_ip': interface1['ip'],
                            'subnet': str(subnet)
                        })
                        
    return connections

if __name__ == "__main__":
    module_args = dict(
        rama6_ftg=dict(type="dict", required=True),
        rama6_core_switch=dict(type="dict", required=True),
        pttn_ftg=dict(type="dict", required=True),
        
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    rama6_ftg = module.params["rama6_ftg"]
    rama6_core_switch = module.params["rama6_core_switch"]
    pttn_ftg = module.params["pttn_ftg"]

    all_devices = rama6_ftg + rama6_core_switch + pttn_ftg or []
    network_topology = find_device_connections(all_devices)

    module.exit_json(changed=False, result=network_topology)