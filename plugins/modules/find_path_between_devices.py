from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress
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

def find_device_path(source_device, destination_ip, all_devices, connections, service_list=None, max_hops=20):
    """Find firewall path from source device to destination IP."""
    current_device_detail = source_device["device_info"]
    current_device = source_device
    current_name = current_device_detail.get("device_name") or current_device_detail.get("name")
    device_path = [current_name]
    visited = {current_name}
    device_path_detail = []
    hops = 0
    debug_info = []
    previous_outgoing_interface = None

    while current_device_detail is not None and hops < max_hops:
        hops += 1
        routing_table = current_device_detail.get("routing_table", [])
        if not routing_table:
            debug_info.append(f"Hop {hops}: No routing table on {current_name}")
            break
        
        current_next_hop = find_next_hop(destination_ip, routing_table)
        if current_next_hop is None:
            debug_info.append(f"Hop {hops}: No route found for {destination_ip} on {current_name}")
            break
        
        # Check if destination is directly connected (route type is "connect")
        route_type = current_next_hop.get("type", "").lower()
        gateway = current_next_hop.get("gateway") or current_next_hop.get("next_hop")
        ip_mask = current_next_hop.get("ip_mask") or current_next_hop.get("destination")
        route_interface = current_next_hop.get("interface")
        debug_info.append(f"Hop {hops}: {current_name} -> route {ip_mask} type={route_type} gateway={gateway} interface={route_interface}")
        
        # Find outgoing interface based on route or gateway
        outgoing_interface = None
        if route_interface:
            # Find the interface details from the device
            for iface in current_device_detail.get("interfaces", []):
                iface_name = iface.get("name") or iface.get("interface")
                if iface_name == route_interface:
                    outgoing_interface = iface
                    break
        
        # Fallback: find outgoing interface based on gateway network
        if not outgoing_interface and gateway and gateway != "0.0.0.0":
            try:
                gateway_ip = ipaddress.ip_address(gateway)
                for iface in current_device_detail.get("interfaces", []):
                    iface_ip = iface.get("ip", "")
                    iface_subnet = iface.get("subnet")
                    
                    if isinstance(iface_ip, list) and iface_ip:
                        iface_ip = iface_ip[0]
                    
                    if "/" in str(iface_ip):
                        iface_ip = iface_ip.split("/")[0]
                    
                    if iface_ip and iface_subnet:
                        try:
                            cidr = ipaddress.IPv4Network(f"0.0.0.0/{iface_subnet}").prefixlen
                            iface_net = ipaddress.ip_network(f"{iface_ip}/{cidr}", strict=False)
                            if gateway_ip in iface_net:
                                outgoing_interface = iface
                                break
                        except (ValueError, AttributeError):
                            pass
            except ValueError:
                pass
        
        # Find incoming interface (where traffic arrives at this device)
        incoming_interface = None
        if hops == 1:
            # First hop - incoming is the source device interface
            incoming_interface = source_device.get("interface")
        elif previous_outgoing_interface:
            # Traffic comes from the previous device's outgoing interface
            # Find the local interface in the same network
            prev_ip = previous_outgoing_interface.get("ip", "")
            prev_subnet = previous_outgoing_interface.get("subnet")
            
            if isinstance(prev_ip, list) and prev_ip:
                prev_ip = prev_ip[0]
            
            if "/" in str(prev_ip):
                prev_ip = prev_ip.split("/")[0]
            
            for iface in current_device_detail.get("interfaces", []):
                curr_ip = iface.get("ip", "")
                curr_subnet = iface.get("subnet")
                
                if isinstance(curr_ip, list) and curr_ip:
                    curr_ip = curr_ip[0]
                
                if "/" in str(curr_ip):
                    curr_ip = curr_ip.split("/")[0]
                
                # Check if both IPs are in the same network
                if prev_ip and curr_ip and prev_subnet and curr_subnet:
                    try:
                        # Build network from prev interface
                        cidr_prev = ipaddress.IPv4Network(f"0.0.0.0/{prev_subnet}").prefixlen
                        prev_net = ipaddress.ip_network(f"{prev_ip}/{cidr_prev}", strict=False)
                        
                        # Check if current IP is in same network
                        curr_ip_obj = ipaddress.ip_address(curr_ip)
                        if curr_ip_obj in prev_net:
                            incoming_interface = iface
                            break
                    except (ValueError, AttributeError):
                        pass
        
        if route_type == "connect":
            # Destination is on this device, we're done
            debug_info.append(f"Hop {hops}: Destination directly connected on {current_name}")
            # Log this final hop
            device_log_detail = {
                "source": source_device.get("source"),
                "destination": destination_ip,
                "service": service_list or [],
                "device_name": current_name,
                "incoming_interface": incoming_interface.get("name") if incoming_interface else None,
                "outgoing_interface": outgoing_interface.get("name") if outgoing_interface else None,
                "incoming_zone": incoming_interface.get("zone") if incoming_interface else None,
                "outgoing_zone": outgoing_interface.get("zone") if outgoing_interface else None,
            }
            device_path_detail.append(device_log_detail)
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
        
        # Log current device details before moving to next
        device_log_detail = {
            "source": source_device.get("source"),
            "destination": destination_ip,
            "service": service_list or [],
            "device_name": current_name,
            "incoming_interface": incoming_interface.get("name") if incoming_interface else None,
            "outgoing_interface": outgoing_interface.get("name") if outgoing_interface else None,
            "incoming_zone": incoming_interface.get("zone") if incoming_interface else None,
            "outgoing_zone": outgoing_interface.get("zone") if outgoing_interface else None,
        }
        device_path_detail.append(device_log_detail)
        
        visited.add(next_name)
        current_device_detail = next_device
        current_name = next_name
        previous_outgoing_interface = outgoing_interface
                            
    return {
        "path": device_path,
        "path_detail": device_path_detail,
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
    service_list = ["TCP/80","TCP/443","UDP/53"]

    all_devices = rama6_ftg + [rama6_core_switch] + pttn_ftg
    device_path = find_device_path(source_device, destination_ip, all_devices, network_topology, service_list)

    module.exit_json(changed=False, result=device_path)
