from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress
from typing import List, Iterable, Optional, Dict, Any

def get_device_connections(connections, device_name):
    """Print connections for a specific device"""
    if device_name not in connections:
        print(f"Device '{device_name}' not found.")
        return
    
    device_connections = connections[device_name]
    
    print("\n" + "="*80)
    print(f"DEVICE: {device_name}")
    print("="*80)
    
    if not device_connections:
        print("No connections found.")
    else:
        remote_device_list = []
        for i, conn in enumerate(device_connections, 1):
            if conn['remote_device'] not in remote_device_list:
                remote_device_list.append(conn['remote_device'])
                print(f"   Local Device:    {conn['local_device']}")
                print(f"   Local Interface:  {conn['local_interface']} ({conn['local_ip']})")
                print(f"   Remote Interface: {conn['remote_interface']} ({conn['remote_ip']})")
                print(f"   Subnet: {conn['subnet']}")
                print()
                
        print(f"Connected to {len(remote_device_list)} device(s):\n")
        for conn in remote_device_list:
            print(conn)
    print("="*80)
    return remote_device_list

def _parse_destination(dest: str) -> List[ipaddress._BaseAddress | ipaddress._BaseNetwork]:
    """Parse destination string supporting:
    - single IP (e.g., 10.10.3.155)
    - network with prefix (e.g., 10.10.3.0/24)
    - IP range with dash (e.g., 10.10.3.10-10.10.3.20)
    Returns list of address or network objects to test.
    """

    dest = dest.strip()

    # Range: A-B
    if "-" in dest:
        start_str, end_str = [p.strip() for p in dest.split("-", 1)]
        start_ip = ipaddress.ip_address(start_str)
        end_ip = ipaddress.ip_address(end_str)
        # Summarize into minimal set of networks
        return list(ipaddress.summarize_address_range(start_ip, end_ip))

    # Network
    if "/" in dest:
        return [ipaddress.ip_network(dest, strict=False)]

    # Single IP
    return [ipaddress.ip_address(dest)]

def _dest_overlaps_route(dest_objects: Iterable[ipaddress._BaseAddress | ipaddress._BaseNetwork], route_network: ipaddress._BaseNetwork) -> bool:
    """Return True if any destination object is contained in or overlaps the route network."""

    for dest in dest_objects:
        if isinstance(dest, ipaddress._BaseAddress):
            if dest in route_network:
                return True
        else:  # network
            if dest.subnet_of(route_network) or dest.overlaps(route_network):
                return True
    return False

def _iter_interface_ips(interface: Dict[str, Any]):
    ip_field = interface.get("ip")
    if isinstance(ip_field, list):
        for ip_val in ip_field:
            if ip_val:
                yield ip_val
    elif isinstance(ip_field, str) and ip_field:
        yield ip_field

def _find_device_by_gateway(gateway: str, all_devices: List[Dict[str, Any]]):
    for device in all_devices:
        for interface in device.get("interfaces", []):
            for ip_val in _iter_interface_ips(interface):
                # strip possible prefix
                ip_only = ip_val.split("/")[0]
                if ip_only == gateway:
                    return device
    return None

def _any_dest_in_network(dest_str: str, interface_ip: str, subnet_mask: str) -> bool:
    """Check if parsed destination overlaps/fits in the interface network."""
    if not interface_ip or not subnet_mask:
        return False
    try:
        cidr = ipaddress.IPv4Network(f"0.0.0.0/{subnet_mask}").prefixlen
        interface_net = ipaddress.ip_network(f"{interface_ip}/{cidr}", strict=False)
        for obj in _parse_destination(dest_str):
            if isinstance(obj, ipaddress._BaseAddress) and obj in interface_net:
                return True
            if isinstance(obj, ipaddress._BaseNetwork) and obj.subnet_of(interface_net):
                return True
    except (ValueError, AttributeError):
        pass
    return False


def find_next_hop(destination, routing_table):
    dest_objs = _parse_destination(destination)
    next_hop = None

    for route in routing_table:
        cidr = route.get("ip_mask")
        if not cidr:
            continue
        try:
            route_net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if cidr == "0.0.0.0/0":
            continue  # skip default route in first pass
        if _dest_overlaps_route(dest_objs, route_net):
            next_hop = route
            break

    if next_hop is None:
        for route in routing_table:
            cidr = route.get("ip_mask")
            if cidr != "0.0.0.0/0":
                continue
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            next_hop = route
            break

    return next_hop

def find_device_path(source_device, destination_ip, all_devices, connections, max_hops=20):
    """Find firewall path from source device to destination IP."""
    current_device = source_device
    current_name = current_device.get("device_name") or current_device.get("name")
    device_path = [current_name]
    visited = {current_name}
    hops = 0

    while current_device is not None and hops < max_hops:
        hops += 1
        routing_table = current_device.get("routing_table", [])
        if not routing_table:
            break
        
        current_next_hop = find_next_hop(destination_ip, routing_table)
        if current_next_hop is None:
            break
        
        # Check if destination is directly connected (route type is "connect")
        route_type = current_next_hop.get("type", "").lower()
        if route_type == "connect":
            # Destination is on this device, we're done
            break
        
        # Find next device via gateway
        gateway = current_next_hop.get("gateway")
        if not gateway or gateway == "0.0.0.0":
            break
        
        connected_device_list = get_device_connections(connections, current_name)
        if not connected_device_list:
            break
        
        next_device = None
        for con_device in connected_device_list:
            con_device_name = con_device.get("device_name")
            if con_device_name in visited:
                continue
            for interface in con_device.get("interfaces", []):
                interface_ip = interface.get("ip")
                if interface_ip and interface_ip.split("/")[0] == gateway:
                    next_device = con_device
                    break
            if next_device:
                break
        
        if next_device is None:
            break
        
        next_name = next_device.get("device_name")
        if next_name in visited:
            break
        
        device_path.append(next_name)
        visited.add(next_name)
        current_device = next_device
        current_name = next_name
                            
    return {
        "path": device_path,
        "hops": hops,
        "status": "completed" if hops < max_hops else "max_hops_reached"
    }
                

if __name__ == "__main__":
    module_args = dict(
        network_topology=dict(type="dict", required=True),
        source_device=dict(type="dict", required=True),
        destination_ip=dict(type="str", required=True), 
        rama6_ftg=dict(type="list", required=True),
        rama6_core_switch=dict(type="dict", required=True),
        pttn_ftg=dict(type="list", required=True),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    
    network_topology = module.params["network_topology"]
    source_device = module.params["source_device"]
    destination_ip = module.params["destination_ip"]
    rama6_ftg = module.params["rama6_ftg"]
    rama6_core_switch = module.params["rama6_core_switch"]
    pttn_ftg = module.params["pttn_ftg"]

    all_devices = rama6_ftg + [rama6_core_switch] + pttn_ftg
    device_path = find_device_path(source_device, destination_ip, all_devices, network_topology)

    module.exit_json(changed=False, result=device_path)
