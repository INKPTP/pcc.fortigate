# from ansible.module_utils.basic import AnsibleModule
import ipaddress
from collections import deque, defaultdict
from pathlib import Path
import yaml


def find_device_by_ip(ip_addr, device_list):
    ip_interface = ipaddress.ip_interface(ip_addr if "/" in ip_addr else f"{ip_addr}/32")
    for dev in device_list:
        for intf in dev["interfaces"]:
            intf_ip = intf["ip"]
            intf_subnet = intf["subnet"]
            network = ipaddress.ip_network(f"{intf_ip}/{intf_subnet}", strict=False)
            if ip_interface.ip in network:
                return dev["name"]
    return None

def find_device_by_subnet(subnet, device_list):
    net = ipaddress.ip_network(subnet)
    for dev in device_list:
        for intf in dev["interfaces"]:
            intf_net = ipaddress.ip_network(
                f'{intf["ip"]}/{intf["subnet"]}',
                strict=False
            )
            if intf_net == net:
                return dev["name"]
    return None

def find_path(graph, start, end):
    queue = deque([[start]])
    visited = set()

    while queue:
        path = queue.popleft()
        node = path[-1]

        if node == end:
            return path

        if node not in visited:
            visited.add(node)
            for neighbor in graph[node]:
                queue.append(path + [neighbor])

    return None

def build_topology(device_list):
    # Build IP → Device mapping
    ip_to_device = {}

    for dev in device_list:
        for intf in dev["interfaces"]:
            ip_to_device[intf["ip"]] = dev["name"]

    # Build topology graph
    graph = defaultdict(list)

    for dev in device_list:
        src = dev["name"]
        for route in dev.get("routing_table", []):
            gw = route["gateway"]
            if gw in ip_to_device:
                dst = ip_to_device[gw]
                graph[src].append(dst)
    return graph


if __name__ == "__main__":    
    
    # module_args = dict(
    #     source_ip=dict(type="str", required=True),
    #     device_list=dict(type="list", required=True)
    # )

    # module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    # source = module.params["source_ip"]
    # device_list = module.params["device_list"]
    
    with open('default.yml', 'r') as f:
        device_list = yaml.load(f, Loader=yaml.SafeLoader)['device_list']
        
    # source_subnet = "10.10.70.0/24"
    source_ip = "10.10.60.100"
    destination_subnet = "10.10.20.0/24"

    graph = build_topology(device_list)
    # src_device = find_device_by_subnet(source_subnet)
    src_device = find_device_by_ip(source_ip, device_list)
    dst_device = find_device_by_subnet(destination_subnet, device_list)

    path = find_path(graph, src_device, dst_device)

    print("Source device:", src_device)
    print("Destination device:", dst_device)
    print("Path:", " -> ".join(path) if path else "No path found")
