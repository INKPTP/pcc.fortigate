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
    dest_objs = _parse_destination(destination[0])
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

def find_source_interface(source_ip_list, interfaces):
    for source in source_ip_list:
        try:
            source_network, source_iface = _parse_source(source)
        except ValueError as exc:
            module.fail_json(msg=str(exc))
        
    for interface in interfaces:
        for interface_network in _interface_networks(interface):
            in_same_network = (
                (source_iface and source_iface.ip in interface_network)
                or (not source_iface and source_network.overlaps(interface_network))
            )

            if in_same_network:
                # Create unique key for device+interface
                interface_name = interface.get("name") or interface.get("interface")
                return interface
    return None
                


def find_device_path(source_device, destination_list, all_devices, connections, max_hops=20):
    """Find firewall path from source device to destination IP."""
    current_device = source_device
    current_name = current_device.get("device_name") or current_device.get("name")
    device_path = [current_name]
    device_path_detail = []
    visited = {current_name}
    hops = 0
    debug_info = []
    

    while current_device is not None and hops < max_hops:
        hops += 1
        routing_table = current_device.get("routing_table", [])
        current_next_hop = find_next_hop(destination_list, routing_table)
        
        current_source_interface = find_source_interface(source_device["source"], current_device.get("interfaces", []))
        
        if current_next_hop is None:
            debug_info.append(f"Hop {hops}: No route found for {destination_list} on {current_name}")
            break
        
        # Check if destination is directly connected (route type is "connect")
        route_type = current_next_hop.get("type", "").lower()
        gateway = current_next_hop.get("next_hop") or current_next_hop.get("gateway")
        ip_mask = current_next_hop.get("ip_mask") or current_next_hop.get("destination")
        debug_info.append(f"Hop {hops}: {current_name} -> route {ip_mask} type={route_type} gateway={gateway}")
        device_path_detail.append({
            "device": current_name,
            "firewall_rule": {
                "incoming_interface": current_source_interface["zone"] if current_source_interface else None,
                "outgoing_interface": current_next_hop.get("interface")["zone"] if current_source_interface else None,
                "source": source_device["source"],
                "destination": destination_list,
                "service": service_list,
            },
            "incoming_interface": current_source_interface,
            "outgoing_interface": current_next_hop.get("interface"),
            "route_info":{
                "route": ip_mask,
                "type": route_type,
                "gateway": gateway,
                }
        })
            
        
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
        destination_list=dict(type="list", required=True), 
        service_list=dict(type="list", required=True), 
        rama6_ftg=dict(type="list", required=True),
        rama6_core_switch=dict(type="dict", required=True),
        pttn_ftg=dict(type="list", required=True),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    
    network_topology = module.params["network_topology"]
    source_device = module.params["source_device"]
    destination_list = module.params["destination_ip"]
    service_list = module.params["service_list"]
    rama6_ftg = module.params["rama6_ftg"]
    rama6_core_switch = module.params["rama6_core_switch"]
    pttn_ftg = module.params["pttn_ftg"]
    source_ip_list = source_device["source"]

    all_devices = rama6_ftg + [rama6_core_switch] + pttn_ftg
    device_path = find_device_path(source_device, destination_list, all_devices, network_topology)

    module.exit_json(changed=False, result=device_path)
