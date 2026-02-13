from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress
import json
from typing import List, Iterable, Optional, Dict, Any
# from openpyxl import Workbook
# from openpyxl.styles import Font, Alignment, PatternFill
# from datetime import datetime

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

def find_source_interface(source_ip_list, interfaces):
    for source in source_ip_list:
        try:
            source_network, source_iface = _parse_source(source)
        except ValueError as exc:
            module.fail_json(msg=str(exc))
            # pass
        
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

def save_firewall_rules_to_excel(path_detail, output_file="firewall_rules.xlsx"):
    """Save firewall rules to Excel file with formatting."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Firewall Rules"
    
    # Define headers
    headers = ["Path #", "Path Destinations", "Device", "Incoming Interface", "Outgoing Interface", "Source", "Destination", "Service"]
    ws.append(headers)
    
    # Format header row
    header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    
    # Add data rows
    for entry in path_detail:
        path_num = entry.get("path_number", 1)
        path_dests = entry.get("path_destinations", "")
        device = entry.get("device", "")
        rule = entry.get("firewall_rule", {})
        
        incoming_iface = rule.get("incoming_interface", "")
        outgoing_iface = rule.get("outgoing_interface", "")
        sources = "\n".join(rule.get("source", []))
        destinations = "\n".join(rule.get("destination", []))
        services = "\n".join(rule.get("service", []))
        
        ws.append([path_num, path_dests, device, incoming_iface, outgoing_iface, sources, destinations, services])
    
    # Adjust column widths and apply text wrapping
    column_widths = {"A": 10, "B": 25, "C": 20, "D": 20, "E": 20, "F": 25, "G": 25, "H": 20}
    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width
    
    # Apply text wrapping and alignment to data cells
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    
    # Save the workbook
    wb.save(output_file)
    print(f"Firewall rules saved to: {output_file}")
    return output_file
                
def find_device_path(source_device, destination_list, all_devices, connections, max_hops=20, max_paths=10, service_list=None):
    """Find multiple firewall paths from source device to destination IPs.
    
    Args:
        source_device: Source device information with source IPs
        destination_list: List of destination IPs
        all_devices: List of all network devices
        connections: Network topology connections
        max_hops: Maximum hops per path (default: 20)
        max_paths: Maximum number of paths to find (default: 10)
        service_list: List of services for firewall rules
    
    Returns:
        Dictionary containing list of paths found
    """
    if service_list is None:
        service_list = []
    
    source = source_device["source"]
    start_device = source_device["device_info"]
    start_name = start_device.get("device_name") or start_device.get("name")
    all_paths = []
    
    def explore_path(current_device, current_name, remaining_dests, current_path, 
                     current_path_detail, visited, depth):
        """Recursively explore paths, branching when destinations diverge."""
        if depth >= max_hops or len(all_paths) >= max_paths or not remaining_dests:
            return
        
        routing_table = current_device.get("routing_table", [])
        
        # Group destinations by their next hop
        dest_by_next_hop = {}
        for dest in remaining_dests:
            next_hop_route = find_next_hop(dest, routing_table)
            if next_hop_route is None:
                continue
            
            gateway = next_hop_route.get("next_hop") or next_hop_route.get("gateway")
            route_type = next_hop_route.get("type", "").lower()
            
            # Use gateway as key, or "connect" for directly connected
            key = "connect" if route_type == "connect" else gateway
            
            if key not in dest_by_next_hop:
                dest_by_next_hop[key] = {
                    "destinations": [],
                    "route": next_hop_route,
                    "gateway": gateway,
                    "route_type": route_type
                }
            dest_by_next_hop[key]["destinations"].append(dest)
        
        if not dest_by_next_hop:
            return
        
        # Build firewall rule details for current device
        current_incoming_interface_key = []
        current_incoming_interface_list = []
        
        for src in source:
            src_route = find_next_hop(src, routing_table)
            if src_route:
                zone = src_route.get("zone", "N/A")
                current_incoming_interface_key.append(zone)
                current_incoming_interface_list.append({
                    "incoming_interface": zone,
                    "source": src
                })
        current_incoming_interface_key = list(set(current_incoming_interface_key))
        
        # Process each next hop group (creates branches if multiple next hops)
        for hop_key, hop_info in dest_by_next_hop.items():
            dests_in_group = hop_info["destinations"]
            route_type = hop_info["route_type"]
            gateway = hop_info["gateway"]
            
            # Build outgoing interface info for this group
            current_outgoing_interface_key = []
            current_outgoing_interface_list = []
            
            for dst in dests_in_group:
                dst_route = find_next_hop(dst, routing_table)
                if dst_route:
                    zone = dst_route.get("zone", "N/A")
                    current_outgoing_interface_key.append(zone)
                    current_outgoing_interface_list.append({
                        "outgoing_interface": zone,
                        "destination": dst
                    })
            current_outgoing_interface_key = list(set(current_outgoing_interface_key))
            
            # Add firewall rule if this is a firewall device
            new_path_detail = current_path_detail.copy()
            if current_device.get("device_type") == 'firewall':
                for iface_in in current_incoming_interface_key:
                    for iface_out in current_outgoing_interface_key:
                        if iface_in != iface_out:
                            new_path_detail.append({
                                "device": current_name,
                                "firewall_rule": {
                                    "incoming_interface": iface_in,
                                    "outgoing_interface": iface_out,
                                    "source": [entry["source"] for entry in current_incoming_interface_list 
                                             if entry["incoming_interface"] == iface_in],
                                    "destination": [entry["destination"] for entry in current_outgoing_interface_list 
                                                  if entry["outgoing_interface"] == iface_out],
                                    "service": service_list,
                                }
                            })
            
            # Check if destinations are directly connected
            if route_type == "connect":
                # Path completed for these destinations
                all_paths.append({
                    "path": current_path.copy(),
                    "path_detail": new_path_detail,
                    "destinations": dests_in_group,
                    "hops": depth,
                    "status": "completed"
                })
                continue
            
            # Find next device via gateway
            if not gateway or gateway == "0.0.0.0":
                continue
            
            connected_device_names = get_device_connections(connections, current_name)
            if not connected_device_names:
                continue
            
            # Find next device with matching gateway IP
            for device_name in connected_device_names:
                if device_name in visited or len(all_paths) >= max_paths:
                    continue
                
                # Find the actual device object
                next_device = None
                for dev in all_devices:
                    if dev.get("device_name") == device_name:
                        next_device = dev
                        break
                
                if next_device is None:
                    continue
                
                # Check if this device has an interface with the gateway IP
                has_gateway = False
                for interface in next_device.get("interfaces", []):
                    interface_ip = interface.get("ip")
                    if interface_ip:
                        ip_list = [interface_ip] if isinstance(interface_ip, str) else interface_ip
                        for ip_val in ip_list:
                            if ip_val and ip_val.split("/")[0] == gateway:
                                has_gateway = True
                                break
                    if has_gateway:
                        break
                
                if not has_gateway:
                    continue
                
                # Recursively explore this branch
                new_visited = visited.copy()
                new_visited.add(device_name)
                new_path = current_path.copy()
                new_path.append(device_name)
                
                explore_path(next_device, device_name, dests_in_group, new_path, 
                           new_path_detail, new_visited, depth + 1)
                break  # Found the next device for this gateway
    
    # Start exploration from source device
    initial_visited = {start_name}
    initial_path = [start_name]
    explore_path(start_device, start_name, destination_list, initial_path, [], initial_visited, 0)
    
    return {
        "paths": all_paths,
        "path_count": len(all_paths),
        "status": "success" if all_paths else "no_path_found"
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
    destination_list = module.params["destination_list"]
    service_list = module.params["service_list"]
    rama6_ftg = module.params["rama6_ftg"]
    rama6_core_switch = module.params["rama6_core_switch"]
    pttn_ftg = module.params["pttn_ftg"]
    
    # json_file_path = r"D:\##--Work--##\code\ansible_collection\pcc.fortigate\vars\input2.json"
    
    # with open(json_file_path, 'r', encoding='utf-8') as f:
    #     data = json.load(f)
        
    # network_topology = data["network_topology"]
    # source_device = data["source_device"]
    # destination_list = data["destination_list"]
    # service_list = data["service_list"]
    # rama6_ftg = data["rama6_ftg"]
    # rama6_core_switch = data["rama6_core_switch"]
    # pttn_ftg = data["pttn_ftg"]

    all_devices = rama6_ftg + [rama6_core_switch] + pttn_ftg
    result = find_device_path(source_device, destination_list, all_devices, network_topology, service_list=service_list)
    
    for idx, path_info in enumerate(result.get("paths", []), 1):
        if path_info["path_detail"]:
            for rule in path_info["path_detail"]:
                fw_rule = rule['firewall_rule']
    
    # Save all paths to Excel
    # timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_path_details = []
    for idx, path_info in enumerate(result.get("paths", []), 1):
        for detail in path_info["path_detail"]:
            detail_copy = detail.copy()
            detail_copy["path_number"] = idx
            detail_copy["path_destinations"] = ", ".join(path_info.get("destinations", []))
            all_path_details.append(detail_copy)
    
    # if all_path_details:
    #     output_file = rf"D:\##--Work--##\code\ansible_collection\pcc.fortigate\plugins\modules\firewall_rules_{timestamp}.xlsx"
    #     save_firewall_rules_to_excel(all_path_details, output_file)
    # else:
    #     print("\nNo firewall rules to save.")
    
    module.exit_json(changed=False, result=all_path_details)

