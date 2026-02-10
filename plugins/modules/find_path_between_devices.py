from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress
import json
from typing import List, Iterable, Optional, Dict, Any

def get_device_connections(connections, device_name):
    """Get list of connected device names for a specific device"""
    if device_name not in connections:
        return []
    
    device_connections = connections[device_name]
    
    if not device_connections:
        return []
    
    remote_device_list = []
    for conn in device_connections:
        if conn['remote_device'] not in remote_device_list:
            remote_device_list.append(conn['remote_device'])
                
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
    """Find best matching route using real router rules:
    1. Longest Prefix Match (LPM) wins
    2. If prefix length ties → prefer lower metric/distance
    3. Default route (0.0.0.0/0) is last resort
    """
    dest_objs = _parse_destination(destination)
    matching_routes = []

    # Find all matching routes (exclude default route in first pass)
    for route in routing_table:
        cidr = route.get("ip_mask")
        if not cidr:
            cidr = route.get("destination")
        if not cidr:
            continue
            
        try:
            route_net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
            
        if cidr == "0.0.0.0/0":
            continue  # skip default route in first pass
            
        if _dest_overlaps_route(dest_objs, route_net):
            # Extract metric/distance for tie-breaking
            metric = route.get("metric", 0)
            distance = route.get("distance", 0)
            matching_routes.append({
                "route": route,
                "prefix_len": route_net.prefixlen,
                "metric": int(metric) if metric else 0,
                "distance": int(distance) if distance else 0
            })

    # Sort by: 1) Longest prefix (descending), 2) Lowest metric, 3) Lowest distance
    if matching_routes:
        matching_routes.sort(key=lambda x: (-x["prefix_len"], x["metric"], x["distance"]))
        return matching_routes[0]["route"]

    # No match found, try default route (last resort)
    for route in routing_table:
        cidr = route.get("ip_mask") or route.get("destination")
        if cidr == "0.0.0.0/0":
            try:
                ipaddress.ip_network(cidr, strict=False)
                return route
            except ValueError:
                continue
    
    return None

def find_device_path(source_device, destination_ip, all_devices, connections, max_hops=20):
    """Find firewall path from source device to destination IP."""
    current_device = source_device
    current_name = current_device.get("device_name") or current_device.get("name")
    device_path = [current_name]
    visited = {current_name}
    hops = 0
    debug_info = []

    while current_device is not None and hops < max_hops:
        hops += 1
        routing_table = current_device.get("routing_table", [])
        current_next_hop = find_next_hop(destination_ip, routing_table)
        
        if current_next_hop is None:
            debug_info.append(f"Hop {hops}: No route found for {destination_ip} on {current_name}")
            break
        
        # Check if destination is directly connected (route type is "connect")
        route_type = current_next_hop.get("type", "").lower()
        gateway = current_next_hop.get("next_hop") or current_next_hop.get("gateway")
        ip_mask = current_next_hop.get("ip_mask") or current_next_hop.get("destination")
        debug_info.append(f"Hop {hops}: {current_name} -> route {ip_mask} type={route_type} gateway={gateway}")
        
        if route_type == "connect":
            # Destination is on this device, we're done
            debug_info.append(f"Hop {hops}: Destination directly connected on {current_name}")
            break
        
        # Find next device via gateway
        if not gateway or gateway == "0.0.0.0":
            debug_info.append(f"Hop {hops}: Invalid gateway {gateway} on {current_name}")
            break
        
        connected_device_names = get_device_connections(connections, current_name)
        
        debug_info.append(f"Hop {hops}: Connected devices: {connected_device_names}")
        if not connected_device_names:
            debug_info.append(f"Hop {hops}: No connected devices found for {current_name}")
            break
        
        next_device = None
        for device_name in connected_device_names:
            if device_name in visited:
                continue
            # Find the actual device object from all_devices
            candidate = None
            for dev in all_devices:
                if dev.get("device_name") == device_name:
                    candidate = dev
                    break
            
            if candidate is None:
                continue
            
            # Check if this device has an interface with the gateway IP
            for interface in candidate.get("interfaces", []):
                interface_ip = interface.get("ip")
                if interface_ip:
                    # Handle both string and list formats
                    ip_list = [interface_ip] if isinstance(interface_ip, str) else interface_ip
                    for ip_val in ip_list:
                        if ip_val and ip_val.split("/")[0] == gateway:
                            next_device = candidate
                            break
                if next_device:
                    break
            if next_device:
                break
        
        if next_device is None:
            debug_info.append(f"Hop {hops}: No device found with gateway IP {gateway}")
            break
        
        next_name = next_device.get("device_name")
        if next_name in visited:
            debug_info.append(f"Hop {hops}: Loop detected - {next_name} already visited")
            break
        
        debug_info.append(f"Hop {hops}: Moving to {next_name}")
        device_path.append(next_name)
        visited.add(next_name)
        current_device = next_device
        current_name = next_name
                            
    return {
        "path": device_path,
        "hops": hops,
        "status": "completed" if hops < max_hops else "max_hops_reached",
        "debug": debug_info
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




