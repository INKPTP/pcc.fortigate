from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule
import ipaddress
import json
import re
import socket
from typing import List, Iterable, Optional, Dict, Any
# from openpyxl import Workbook
# from openpyxl.styles import Font, Alignment, PatternFill
# from datetime import datetime

def is_private_ip(ip_str: str) -> bool:
    """Check if IP address is private."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private
    except ValueError:
        return False

def resolve_fqdn(fqdn: str) -> List[str]:
    """Resolve FQDN to IP address(es). Returns list of IPs or empty list if resolution fails."""
    try:
        result = socket.getaddrinfo(fqdn, None)
        ips = list(set([addr[4][0] for addr in result]))
        return ips
    except (socket.gaierror, socket.error):
        return []

def convert_ip_to_object(ip_string: str) -> dict:
    """Convert IP string to detailed object format with type, ipaddress, subnet, cidr, start_ip, end_ip, fqdn."""
    ip_string = ip_string.strip()
    
    # Check if it's a FQDN (contains letters, wildcards, or domain-like patterns)
    # FQDN should have at least one alphabetic character or asterisk
    if re.search(r'[a-zA-Z*]', ip_string) and not re.match(r'^[\d\.\/\-\s]+$', ip_string):
        # Try to resolve the FQDN
        resolved_ips = resolve_fqdn(ip_string)
        
        if resolved_ips:
            # Check if all resolved IPs are private
            all_private = all(is_private_ip(ip) for ip in resolved_ips)
            
            if all_private:
                # Convert to ipmask or iprange based on number of IPs
                if len(resolved_ips) == 1:
                    # Single IP - return as ipmask
                    return {
                        "type": "ipmask",
                        "ipaddress": resolved_ips[0],
                        "subnet": "255.255.255.255",
                        "cidr": 32,
                        "start_ip": resolved_ips[0],
                        "end_ip": resolved_ips[0],
                        "fqdn": ""
                    }
                else:
                    # Multiple IPs - return as iprange (use first and last IP)
                    sorted_ips = sorted(resolved_ips, key=lambda ip: ipaddress.ip_address(ip))
                    start_ip = sorted_ips[0]
                    end_ip = sorted_ips[-1]
                    return {
                        "type": "iprange",
                        "ipaddress": f"{start_ip} {end_ip}",
                        "subnet": "",
                        "cidr": 0,
                        "start_ip": start_ip,
                        "end_ip": end_ip,
                        "fqdn": ""
                    }
            # else: at least one public IP, keep as FQDN
        # else: resolution failed, keep as FQDN
        
        # Keep as FQDN for public IPs or resolution failures
        return {
            "type": "fqdn",
            "ipaddress": "",
            "subnet": "0.0.0.0 0.0.0.0",
            "cidr": 0,
            "start_ip": "0.0.0.0",
            "end_ip": "0.0.0.0",
            "fqdn": ip_string
        }
    
    # Check if it's an IP range (contains -)
    if "-" in ip_string and not any(char.isalpha() for char in ip_string):
        parts = ip_string.split("-")
        start_ip = parts[0].strip()
        end_ip = parts[1].strip()
        return {
            "type": "iprange",
            "ipaddress": f"{start_ip} {end_ip}",
            "subnet": "",
            "cidr": 0,
            "start_ip": start_ip,
            "end_ip": end_ip,
            "fqdn": ""
        }
    
    # Check if it's a CIDR notation (contains /)
    if "/" in ip_string:
        try:
            network = ipaddress.ip_network(ip_string, strict=False)
            ip_addr = str(network.network_address)
            prefix_len = network.prefixlen
            netmask = str(network.netmask)
            
            # Calculate start and end IP
            start_ip = str(network.network_address)
            end_ip = str(network.broadcast_address)
            
            return {
                "type": "ipmask",
                "ipaddress": ip_addr,
                "subnet": netmask,
                "cidr": prefix_len,
                "start_ip": start_ip,
                "end_ip": end_ip,
                "fqdn": ""
            }
        except ValueError:
            # Fallback to single IP if parsing fails
            pass
    
    # Single IP address (default)
    return {
        "type": "ipmask",
        "ipaddress": ip_string,
        "subnet": "255.255.255.255",
        "cidr": 32,
        "start_ip": ip_string,
        "end_ip": ip_string,
        "fqdn": ""
    }

def ip_objects_equal(obj1: dict, obj2: dict) -> bool:
    """Check if two IP objects represent the same IP/range/FQDN."""
    if obj1.get("type") != obj2.get("type"):
        return False
    
    if obj1.get("type") == "fqdn":
        return obj1.get("fqdn") == obj2.get("fqdn")
    elif obj1.get("type") == "iprange":
        return (obj1.get("start_ip") == obj2.get("start_ip") and 
                obj1.get("end_ip") == obj2.get("end_ip"))
    else:  # ipmask
        return (obj1.get("ipaddress") == obj2.get("ipaddress") and 
                obj1.get("cidr") == obj2.get("cidr"))

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
    - FQDN (e.g., example.com) - will be resolved to IP addresses
    Returns list of address or network objects to test.
    """

    dest = dest.strip()
    
    # Check if it's an FQDN (contains letters)
    if re.search(r'[a-zA-Z]', dest) and not re.match(r'^[\d\.\/\-\s]+$', dest):
        # Try to resolve FQDN to IP addresses
        resolved_ips = resolve_fqdn(dest)
        if resolved_ips:
            # Return list of IP address objects
            return [ipaddress.ip_address(ip) for ip in resolved_ips]
        else:
            # Resolution failed, return empty list (route won't match)
            return []

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
            # module.fail_json(msg=str(exc))
            pass
        
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

def find_source_device(source_list, destination_list, service_list, device_list):
    matched_device_list = []
    matched_device_map = {} 
    temp_destination_ip_list = []
    for destination_ip in destination_list:
        try:
            dest_network, dest_iface = _parse_source(destination_ip)
            temp_destination_ip_list.append(str(dest_network))
        except ValueError as exc:
            pass
            # module.fail_json(msg=str(exc))
    destination_ip_list = temp_destination_ip_list
    
    for source_ip in source_list:
        try:
            source_network, source_iface = _parse_source(source_ip)
        except ValueError as exc:
            pass
            # module.fail_json(msg=str(exc))

        matched_device = None

        # First pass: check if source is directly on a device interface
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
                            # Preserve IP range format if input is a range, otherwise use CIDR
                            source_value = source_ip if "-" in source_ip else str(source_network)
                            existing_match = matched_device_map[match_key]
                            if source_value not in existing_match["source"]:
                                existing_match["source"].append(source_value)
                            matched_device = existing_match
                        else:
                            # Create new match
                            # Preserve IP range format if input is a range, otherwise use CIDR
                            source_value = source_ip if "-" in source_ip else str(source_network)
                            matched_device = {
                                "device_name": device_name,
                                "source_device": True,
                                "source_interface": interface,
                                "source": [source_value],
                                "destination": destination_ip_list,
                                "service": service_list,
                                "device_info": device,
                                "source_behind_device": False,
                            }
                            matched_device_map[match_key] = matched_device
                            matched_device_list.append(matched_device)
                        break
                if matched_device:
                    break
            if matched_device:
                break

        # Second pass: if source not directly connected, trace routing to find entry device
        if not matched_device:
            # Find all devices that have a route to the source network
            devices_with_route = []
            
            for device in device_list:
                device_name = device.get("device_name") or device.get("name")
                routing_table = device.get("routing_table", [])
                
                # Check if this device has a route to the source network
                source_route = find_next_hop(source_ip, routing_table)
                if source_route:
                    route_type = source_route.get("type", "").lower()
                    gateway = source_route.get("next_hop") or source_route.get("gateway")
                    zone = source_route.get("zone", "N/A")
                    
                    devices_with_route.append({
                        "device": device,
                        "device_name": device_name,
                        "route": source_route,
                        "gateway": gateway,
                        "zone": zone,
                        "route_type": route_type
                    })
            
            # Find the last device before traffic goes out of scope
            # This is the device whose next hop for the source is NOT in our device list
            entry_device = None
            
            for dev_info in devices_with_route:
                device_name = dev_info["device_name"]
                gateway = dev_info["gateway"]
                route_type = dev_info["route_type"]
                
                # If route type is connect, skip (source is directly connected, should have been caught in first pass)
                if route_type == "connect":
                    continue
                
                # Check if the gateway is on any other device in our list
                gateway_on_tracked_device = False
                
                if gateway and gateway != "0.0.0.0":
                    for other_device in device_list:
                        if other_device.get("device_name") == device_name:
                            continue  # Skip self
                        
                        # Check if gateway IP is on this other device's interfaces
                        for interface in other_device.get("interfaces", []):
                            interface_ip = interface.get("ip")
                            if interface_ip:
                                ip_list = [interface_ip] if isinstance(interface_ip, str) else interface_ip
                                for ip_val in ip_list:
                                    if ip_val and ip_val.split("/")[0] == gateway:
                                        gateway_on_tracked_device = True
                                        break
                            if gateway_on_tracked_device:
                                break
                        if gateway_on_tracked_device:
                            break
                
                # If gateway is NOT on any tracked device, this is our entry device
                if not gateway_on_tracked_device:
                    entry_device = dev_info
                    break
            
            # Use the entry device as source device
            if entry_device:
                device_name = entry_device["device_name"]
                zone = entry_device["zone"]
                match_key = f"{device_name}:routing:{zone}"
                
                if match_key in matched_device_map:
                    # Merge with existing match
                    # Preserve IP range format if input is a range, otherwise use CIDR
                    source_value = source_ip if "-" in source_ip else str(source_network)
                    existing_match = matched_device_map[match_key]
                    if source_value not in existing_match["source"]:
                        existing_match["source"].append(source_value)
                    matched_device = existing_match
                else:
                    # Create new match for source behind device
                    # Preserve IP range format if input is a range, otherwise use CIDR
                    source_value = source_ip if "-" in source_ip else str(source_network)
                    matched_device = {
                        "device_name": device_name,
                        "source_device": True,
                        "source_interface": None,  # Not directly connected
                        "source": [source_value],
                        "destination": destination_ip_list,
                        "service": service_list,
                        "device_info": entry_device["device"],
                        "source_behind_device": True,
                        "source_zone": zone,
                    }
                    matched_device_map[match_key] = matched_device
                    matched_device_list.append(matched_device)

        if not matched_device:
            # Preserve IP range format if input is a range, otherwise use CIDR
            source_value = source_ip if "-" in source_ip else str(source_network)
            matched_device = {
                "device_name": None,
                "source_device": False,
                "source_interface": None,
                "source": [source_value],
                "destination": destination_ip_list,
                "service": service_list,
                "device_info": None,
                "source_behind_device": False,
            }
            matched_device_list.append(matched_device)
        
    return matched_device_list

def summarize_firewall_rules(path_details):
    """Summarize firewall rules by grouping rules with same device, incoming interface, and outgoing interface.
    
    Args:
        path_details: List of path detail dictionaries
        
    Returns:
        List of summarized path detail dictionaries
    """
    summary_map = {}
    
    for entry in path_details:
        device = entry.get("device", "")
        site = entry.get("site", "")
        vdom = entry.get("vdom", "")
        device_ip_address = entry.get("device_ip_address", "")
        rule = entry.get("firewall_rule", {})
        incoming_iface = rule.get("incoming_interface", "")
        outgoing_iface = rule.get("outgoing_interface", "")
        
        # Create unique key for grouping
        key = f"{device}|{incoming_iface}|{outgoing_iface}"
        
        if key in summary_map:
            # Merge with existing entry
            existing = summary_map[key]
            existing_rule = existing["firewall_rule"]
            
            # Merge sources (avoid duplicates by comparing IP objects)
            for src in rule.get("source", []):
                if not any(ip_objects_equal(src, existing_src) for existing_src in existing_rule["source"]):
                    existing_rule["source"].append(src)
            
            # Merge destinations (avoid duplicates by comparing IP objects)
            for dst in rule.get("destination", []):
                if not any(ip_objects_equal(dst, existing_dst) for existing_dst in existing_rule["destination"]):
                    existing_rule["destination"].append(dst)
            
            # Merge services (avoid duplicates)
            for svc in rule.get("service", []):
                if svc not in existing_rule["service"]:
                    existing_rule["service"].append(svc)
            
            # Update path numbers and destinations
            path_num = entry.get("path_number", "")
            if path_num and str(path_num) not in str(existing.get("path_number", "")):
                existing["path_number"] = f"{existing['path_number']}, {path_num}"
            
            path_dests = entry.get("path_destinations", "")
            if path_dests and path_dests not in existing.get("path_destinations", ""):
                if existing.get("path_destinations"):
                    existing["path_destinations"] = f"{existing['path_destinations']}; {path_dests}"
                else:
                    existing["path_destinations"] = path_dests
                    
            source_device = entry.get("source_device", "")
            if source_device and source_device not in existing.get("source_device", ""):
                if existing.get("source_device"):
                    existing["source_device"] = f"{existing['source_device']}, {source_device}"
                else:
                    existing["source_device"] = source_device
        else:
            # Create new entry (deep copy to avoid modifying original)
            new_entry = {
                "device": device,
                "site": site,
                "vdom": vdom,
                "device_ip_address": device_ip_address,
                "firewall_rule": {
                    "name": rule.get("name", ""),
                    "incoming_interface": incoming_iface,
                    "outgoing_interface": outgoing_iface,
                    "source": rule.get("source", []).copy(),
                    "destination": rule.get("destination", []).copy(),
                    "service": rule.get("service", []).copy(),
                    "schedule": rule.get("schedule", {}),
                    "action": rule.get("action", ""),
                },
                "path_number": entry.get("path_number", ""),
                "path_destinations": entry.get("path_destinations", ""),
                "source_device": entry.get("source_device", ""),
            }
            summary_map[key] = new_entry
    
    return list(summary_map.values())

def retrive_vpn_user_ip_mapping(vpn_user_rules, source_list):
    """Map source usernames to their assigned VPN IP pools.

    Each entry in vpn_user_rules is expected to follow the vpn_user_rules.json
    schema, which combines role assignment and IP pool into a single record:
        {
            "usernames":     [...],   # list of usernames this rule applies to
            "assigned_role": "...",   # role name (informational)
            "ipv4_addresses": [...]   # IP ranges/addresses assigned by this rule
        }
    """
    unique_ip_pool_list = []

    for source in source_list:
        source_lower = source.lower()
        for rule in vpn_user_rules:
            if not rule.get('ipv4_addresses'):
                continue
            if source_lower in [u.lower() for u in rule.get('usernames', [])]:
                for ip in rule['ipv4_addresses']:
                    if ip not in unique_ip_pool_list:
                        unique_ip_pool_list.append(ip)
                break

    return unique_ip_pool_list
         
def find_device_path(rule_name, source_device, destination_list, all_devices, connections, max_hops=20, max_paths=10, service_list=None, schedule=None, action=None):
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
        
        # Check if source is behind the device (not directly connected)
        # This logic should only apply to the first device (start device)
        source_behind_device = source_device.get("source_behind_device", False)
        is_start_device = (current_name == start_name)
        
        if source_behind_device and is_start_device:
            # Use the zone from routing table for sources behind the device (first hop only)
            source_zone = source_device.get("source_zone", "N/A")
            for src in source:
                current_incoming_interface_key.append(source_zone)
                current_incoming_interface_list.append({
                    "incoming_interface": source_zone,
                    "source": src
                })
        else:
            # For all other cases: look up source route in current device's routing table
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
                            # Convert source IPs to detailed objects
                            source_objects = [
                                convert_ip_to_object(entry["source"]) 
                                for entry in current_incoming_interface_list 
                                if entry["incoming_interface"] == iface_in
                            ]
                            
                            # Convert destination IPs to detailed objects
                            destination_objects = [
                                convert_ip_to_object(entry["destination"]) 
                                for entry in current_outgoing_interface_list 
                                if entry["outgoing_interface"] == iface_out
                            ]
                            
                            new_path_detail.append({
                                "device": current_name,
                                "site": current_device.get("site"),
                                "vdom": current_device.get("vdom"),
                                "device_ip_address": current_device.get("device_ip_address"),
                                "firewall_rule": {
                                    "name": rule_name,
                                    "incoming_interface": iface_in,
                                    "outgoing_interface": iface_out,
                                    "source": source_objects,
                                    "destination": destination_objects,
                                    "service": service_list,
                                    "schedule": schedule if schedule else {},
                                    "action": action if action else "",
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
                # No connected devices found - destination is out of scope
                # Record firewall rule and mark path as completed
                all_paths.append({
                    "path": current_path.copy(),
                    "path_detail": new_path_detail,
                    "destinations": dests_in_group,
                    "hops": depth,
                    "status": "out_of_scope"
                })
                continue
            
            # Find next device with matching gateway IP
            next_device_found = False
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
                next_device_found = True
                break  # Found the next device for this gateway
            
            # If no next device found, destination is out of scope
            # Record firewall rule with outgoing interface and mark as completed
            if not next_device_found:
                all_paths.append({
                    "path": current_path.copy(),
                    "path_detail": new_path_detail,
                    "destinations": dests_in_group,
                    "hops": depth,
                    "status": "out_of_scope"
                })
    
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
        rule_cr_id=dict(type="str", required=True),
        rule_source_list=dict(type="list", required=True),
        rule_destination_list=dict(type="list", required=True), 
        rule_service_list=dict(type="list", required=True),
        rule_schedule=dict(type="dict", required=True),
        rule_action=dict(type="str", required=True),
        rama6_ftg=dict(type="list", required=True),
        rama6_core_switch=dict(type="dict", required=True),
        pttn_ftg=dict(type="list", required=True),
        vpn_user_rules=dict(type="list", required=True),
        # vpn_user_role=dict(type="list", required=True),
        # vpn_ip_pool=dict(type="list", required=True)
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    
    network_topology = module.params["network_topology"]
    rule_name = module.params["rule_cr_id"]
    source_list = module.params["rule_source_list"]
    destination_list = module.params["rule_destination_list"]
    service_list = module.params["rule_service_list"]
    schedule = module.params["rule_schedule"]
    action = module.params["rule_action"]
    rama6_ftg = module.params["rama6_ftg"]
    rama6_core_switch = module.params["rama6_core_switch"]
    pttn_ftg = module.params["pttn_ftg"]
    vpn_user_rules = module.params["vpn_user_rules"]
    # vpn_user_role = module.params["vpn_user_role"]
    # vpn_ip_pool = module.params["vpn_ip_pool"]

    all_devices = rama6_ftg + [rama6_core_switch] + pttn_ftg
    
    # check if source_list contains IP addresses or usernames, and retrieve VPN user IP mapping if needed
    has_non_ip = False
    for item in source_list:
        try:
            # Check if it's an IP range (contains -)
            if "-" in item:
                parts = item.split("-")
                if len(parts) == 2:
                    # Try to parse both parts as IP addresses
                    ipaddress.ip_address(parts[0].strip())
                    ipaddress.ip_address(parts[1].strip())
                    # Valid IP range, continue to next item
                    continue
            # Try to parse as regular IP/network
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            has_non_ip = True
            break
    
    if has_non_ip:
        vpn_ip_list = retrive_vpn_user_ip_mapping(vpn_user_rules, source_list)
        if not vpn_ip_list:
            module.fail_json(msg=f"No VPN IP mapping found for users: {source_list}. Please verify VPN user roles and IP pools.")
        source_list = vpn_ip_list
    
    # Find source device from source_list
    source_device_list = find_source_device(source_list, destination_list, service_list, all_devices)
    if not source_device_list:
        module.fail_json(msg=f"No source device found for source IPs: {source_list}")
    
    source_device_name_list = []
    for device in source_device_list:
        device_name = device.get("device_name")
        if device_name and device_name not in source_device_name_list:
            source_device_name_list.append(device_name)
    
    # Process each source device and collect all paths
    all_path_details = []
    path_counter = 1
    
    for source_device in source_device_list:
        if source_device.get("device_info") is None:
            # Skip entries without valid device info
            continue
            
        device_name = source_device.get("device_name")
        # print(f"Processing paths from source device: {device_name}")
        
        result = find_device_path(rule_name, source_device, destination_list, all_devices, network_topology, service_list=service_list, schedule=schedule, action=action)
        
        # Collect path details from this source device
        for path_info in result.get("paths", []):
            for detail in path_info["path_detail"]:
                detail_copy = detail.copy()
                detail_copy["path_number"] = path_counter
                detail_copy["path_destinations"] = ", ".join(path_info.get("destinations", []))
                detail_copy["source_device"] = device_name
                all_path_details.append(detail_copy)
            path_counter += 1
    
    if all_path_details:
        summarized_rules = summarize_firewall_rules(all_path_details)
        
        # Group rules by device and append sequential numbers to rule names
        device_rule_counts = {}
        for rule in summarized_rules:
            device_name = rule.get("device", "")
            if device_name not in device_rule_counts:
                device_rule_counts[device_name] = 0
            device_rule_counts[device_name] += 1
            
            # Append sequential number to rule name
            original_name = rule["firewall_rule"]["name"]
            rule["firewall_rule"]["name"] = f"{original_name}_{device_rule_counts[device_name]}"
        
        module.exit_json(changed=False, result=summarized_rules)
    else:
        module.fail_json(msg="No firewall paths found. Unable to route from source to destination.")
