from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule

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

if __name__ == "__main__":
    module_args = dict(
        network_topology=dict(type="dict", required=True),
        source_device_name=dict(type="str", required=True),
    )

    connected_device = get_device_connections(network_topology, source_device_name)

    module.exit_json(changed=False, result=connected_device)