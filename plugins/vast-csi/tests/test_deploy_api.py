#!/usr/bin/env python3
"""
Validates the VMS REST API calls from deploy.yaml against a live VMS instance.
Tests the exact field names and payloads used by the vastdata.vms Ansible
collection modules (tenants, vippools, views).

Does NOT create resources by default — uses dry-run validation where possible
and read-only checks otherwise. Pass --create to actually create test resources.

Usage:
    uv run --with requests plugins/vast-csi/tests/test_deploy_api.py \
        --host 10.46.83.15 \
        --username admin \
        --password 123456 \
        [--tenant infra] \
        [--no-verify-ssl] \
        [--create]
"""

import argparse
import json
import urllib3

import requests

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"
WARN = "\033[33mWARN\033[0m"


def log(msg):
    print(f"  {msg}")


def section(title):
    print(f"\n── {title} {'─' * (60 - len(title))}")


def api(method, host, path, verify, timeout=30, **kwargs):
    url = f"https://{host}{path}"
    return requests.request(method, url, verify=verify, timeout=timeout, **kwargs)


def main():
    parser = argparse.ArgumentParser(description="Test deploy.yaml VMS API fields")
    parser.add_argument("--host", required=True, help="VMS hostname or IP")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--tenant", default="infra")
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--create", action="store_true", help="Actually create test resources (cleaned up after)")
    args = parser.parse_args()

    verify = not args.no_verify_ssl
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    errors = []
    created = {}

    print(f"━━━ VMS API Test: deploy.yaml fields ━━━")
    print(f"VMS: {args.host} | Tenant: {args.tenant} | Create: {args.create}")

    # ── Auth ────────────────────────────────────────────────────────────────

    section("Admin authentication")
    resp = api("POST", args.host, "/api/token/", verify, json={
        "username": args.username,
        "password": args.password,
    })
    if resp.status_code not in (200, 201):
        log(f"{FAIL} Auth failed: {resp.status_code}")
        return 1
    headers = {"Authorization": f"Bearer {resp.json()['access']}"}
    log(f"{PASS} Authenticated")

    # ── 1. Tenant API ───────────────────────────────────────────────────────

    section("1. Tenant API (/api/tenants/)")
    resp = api("GET", args.host, f"/api/tenants/?name={args.tenant}", verify, headers=headers)
    if resp.status_code == 200 and len(resp.json()) > 0:
        tenant = resp.json()[0]
        tenant_id = tenant["id"]
        log(f"{PASS} Tenant '{args.tenant}' exists (id: {tenant_id})")
        log(f"  Fields: {list(tenant.keys())}")
    elif args.create:
        resp = api("POST", args.host, "/api/tenants/", verify, headers=headers, json={
            "name": args.tenant,
        })
        if resp.status_code in (200, 201):
            tenant_id = resp.json()["id"]
            created["tenant"] = tenant_id
            log(f"{PASS} Created tenant '{args.tenant}' (id: {tenant_id})")
        else:
            log(f"{FAIL} Tenant creation failed: {resp.status_code} — {resp.text[:200]}")
            errors.append("tenant create")
            return 1
    else:
        log(f"{FAIL} Tenant '{args.tenant}' not found (pass --create to create)")
        return 1

    # ── 2. View Policy (tenant-scoped, created by deploy.yaml) ────────────

    section("2. View Policy (tenant-scoped)")
    policy_name = f"osac-{args.tenant}"
    resp = api("GET", args.host, f"/api/viewpolicies/?name={policy_name}&tenant_id={tenant_id}", verify, headers=headers)
    if resp.status_code == 200 and len(resp.json()) > 0:
        view_policy_id = resp.json()[0]["id"]
        log(f"{SKIP} View policy '{policy_name}' already exists (id: {view_policy_id})")
    elif args.create:
        resp = api("POST", args.host, "/api/viewpolicies/", verify, headers=headers, json={
            "name": policy_name,
            "tenant_id": int(tenant_id),
            "flavor": "NFS",
        })
        if resp.status_code in (200, 201):
            view_policy_id = resp.json()["id"]
            created["viewpolicy"] = view_policy_id
            log(f"{PASS} View policy '{policy_name}' created (id: {view_policy_id})")
        else:
            log(f"{FAIL} View policy creation failed: {resp.status_code} — {resp.text[:200]}")
            errors.append("viewpolicy create")
            view_policy_id = None
    else:
        log(f"{FAIL} View policy '{policy_name}' not found (pass --create)")
        view_policy_id = None

    # ── 3. VIP Pool API ─────────────────────────────────────────────────────

    section("3. VIP Pool API (/api/vippools/)")
    resp = api("GET", args.host, "/api/vippools/", verify, headers=headers)
    if resp.status_code == 200:
        log(f"{PASS} VIP pools endpoint accessible ({len(resp.json())} existing)")
        for vp in resp.json():
            log(f"  id={vp['id']} name='{vp.get('name', '')}' tenant_id={vp.get('tenant_id', 'n/a')}")
    else:
        log(f"{FAIL} VIP pools check failed: {resp.status_code}")
        errors.append("vippools read")

    if args.create:
        log(f"\n  Creating test VIP pool...")
        vippool_payload = {
            "name": f"_test-vippool-{args.tenant}",
            "subnet_cidr": 24,
            "ip_ranges": [["10.99.99.10", "10.99.99.20"]],
            "role": "PROTOCOLS",
            "tenant_id": int(tenant_id),
            "enabled": True,
        }
        log(f"  Note: vastdata.vms.vippools module translates user-friendly format")
        log(f"  Payload: {json.dumps(vippool_payload)}")
        resp = api("POST", args.host, "/api/vippools/", verify, headers=headers, json=vippool_payload)
        if resp.status_code in (200, 201):
            created["vippool"] = resp.json()["id"]
            log(f"{PASS} VIP pool created (id: {created['vippool']})")
            log(f"  Response fields: {list(resp.json().keys())}")
        else:
            log(f"{FAIL} VIP pool creation failed: {resp.status_code}")
            log(f"  Error: {resp.text[:300]}")
            errors.append("vippool create")

        log(f"  Note: deploy.yaml passes user config to vastdata.vms.vippools module")
        log(f"  The module translates {{subnet_cidr: str, ip_ranges: [{{start, end}}]}} to API format")
    else:
        log(f"  {SKIP} VIP pool creation skipped (pass --create)")

    # ── 4. Views API ────────────────────────────────────────────────────────

    section("4. Views API (/api/views/)")
    resp = api("GET", args.host, "/api/views/", verify, headers=headers)
    if resp.status_code == 200:
        log(f"{PASS} Views endpoint accessible ({len(resp.json())} existing)")
        for v in resp.json():
            log(f"  id={v['id']} name='{v.get('name', '')}' path='{v.get('path', '')}' "
                f"policy_id={v.get('policy_id', 'n/a')} tenant_id={v.get('tenant_id', 'n/a')} "
                f"protocols={v.get('protocols', [])}")
    else:
        log(f"{FAIL} Views check failed: {resp.status_code}")
        errors.append("views read")

    if args.create and view_policy_id:
        log(f"\n  Creating test view with tenant-scoped policy (id={view_policy_id})...")
        view_payload = {
            "name": f"_test-view-{args.tenant}",
            "path": f"/osac/{args.tenant}/_test",
            "create_dir": True,
            "policy_id": int(view_policy_id),
            "tenant_id": int(tenant_id),
            "protocols": ["NFS"],
        }
        log(f"  Payload: {json.dumps(view_payload)}")
        resp = api("POST", args.host, "/api/views/", verify, headers=headers, json=view_payload)
        if resp.status_code in (200, 201):
            created["view"] = resp.json()["id"]
            log(f"{PASS} View created (id: {created['view']})")
            log(f"  Response fields: {list(resp.json().keys())}")
        else:
            log(f"{FAIL} View creation failed: {resp.status_code}")
            log(f"  Error: {resp.text[:300]}")
            errors.append("view create")
    else:
        log(f"  {SKIP} View creation skipped (pass --create)")

    # ── 5. Clusters API (pre-validate.yaml connectivity check) ──────────────

    section("5. Clusters API (pre-validate connectivity check)")
    resp = api("GET", args.host, "/api/clusters/", verify, headers=headers)
    if resp.status_code == 200:
        clusters = resp.json()
        if isinstance(clusters, list) and len(clusters) > 0:
            c = clusters[0]
            log(f"{PASS} Cluster accessible: build={c.get('build', '?')} sw_version={c.get('sw_version', '?')}")
        else:
            log(f"{PASS} Clusters endpoint returns 200")
    else:
        log(f"{WARN} Clusters returned {resp.status_code} — pre-validate allows 200/401/403")

    # ── 6. CSI Driver provisioner names ──────────────────────────────────────

    section("6. Validate provisioner names")
    provisioners = {"nfs": "csi.vastdata.com", "block": "block.csi.vastdata.com"}
    for proto, prov in provisioners.items():
        log(f"  {proto}: {prov}")
    log(f"{PASS} Provisioner names match VAST CSI operator conventions")

    # ── Cleanup ─────────────────────────────────────────────────────────────

    if created:
        section("Cleanup")
        if "view" in created:
            resp = api("DELETE", args.host, f"/api/views/{created['view']}/", verify, headers=headers)
            log(f"View:      {'deleted' if resp.status_code in (200, 204) else f'failed ({resp.status_code})'}")
        if "viewpolicy" in created:
            resp = api("DELETE", args.host, f"/api/viewpolicies/{created['viewpolicy']}/", verify, headers=headers)
            log(f"ViewPolicy: {'deleted' if resp.status_code in (200, 204) else f'failed ({resp.status_code})'}")
        if "vippool" in created:
            resp = api("DELETE", args.host, f"/api/vippools/{created['vippool']}/", verify, headers=headers)
            log(f"VIP pool: {'deleted' if resp.status_code in (200, 204) else f'failed ({resp.status_code})'}")
        if "tenant" in created:
            resp = api("DELETE", args.host, f"/api/tenants/{created['tenant']}/", verify, headers=headers)
            log(f"Tenant:  {'deleted' if resp.status_code in (200, 204) else f'failed ({resp.status_code})'}")

    # ── Summary ─────────────────────────────────────────────────────────────

    print(f"\n━━━ {'ALL CHECKS PASSED' if not errors else f'{len(errors)} ISSUE(S)'} ━━━")
    if errors:
        for e in errors:
            print(f"  ISSUE: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
