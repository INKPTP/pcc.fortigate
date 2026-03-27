from __future__ import annotations
from ansible.module_utils.basic import AnsibleModule

def main():
    module_args = dict(
        policies=dict(type="list", required=True),
        active_schedule=dict(type="list", required=True),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
        
    policies = module.params["policies"]
    active_schedule = module.params["active_schedule"]
    
    expire_policy = []
    
    for item in policies:
        if item.get("schedule") not in active_schedule:
            expire_policy.append(item)
    
    module.exit_json(changed=False, result=expire_policy)

if __name__ == "__main__":
    main()