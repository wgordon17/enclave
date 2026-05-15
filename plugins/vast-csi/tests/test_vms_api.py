#!/usr/bin/env python3
"""
Validates the VMS REST API flow from create_tenant_manager.yaml against a live
VMS instance. Tests the exact endpoints, payloads, and idempotency logic.

Usage:
    uv run --with requests plugins/vast-csi/tests/test_vms_api.py \
        --host 10.46.83.15 \
        --username admin \
        --password 123456 \
        [--tenant infra] \
        [--no-verify-ssl] \
        [--no-cleanup]
"""

import argparse
import secrets
import string
import urllib3

import requests

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"


def log(msg):
    print(f"  {msg}")


def section(title):
    print(f"\n── {title} {'─' * (60 - len(title))}")


def api(method, host, path, verify, timeout=30, **kwargs):
    url = f"https://{host}{path}"
    return requests.request(method, url, verify=verify, timeout=timeout, **kwargs)


def main():
    parser = argparse.ArgumentParser(description="Test VMS API flow for create_tenant_manager.yaml")
    parser.add_argument("--host", required=True, help="VMS hostname or IP (no protocol)")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--tenant", default="infra", help="Tenant name to test against (must exist)")
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    verify = not args.no_verify_ssl
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    tenant = args.tenant
    role_name = f"osac-csi-{tenant}"
    manager_name = f"osac-{tenant}"
    created = {"manager": None, "role": None}
    errors = []

    print(f"━━━ VMS API Test: create_tenant_manager.yaml ━━━")
    print(f"VMS: {args.host} | Tenant: {tenant} | SSL verify: {verify}")

    # ── Step 1: Admin auth ──────────────────────────────────────────────────

    section("Admin authentication")
    resp = api("POST", args.host, "/api/token/", verify, json={
        "username": args.username,
        "password": args.password,
    })
    if resp.status_code not in (200, 201):
        print(f"  {FAIL} Auth failed: {resp.status_code}")
        return 1
    access_token = resp.json()["access"]
    headers = {"Authorization": f"Bearer {access_token}"}
    log(f"{PASS} Authenticated (token: {access_token[:12]}...)")

    # ── Step 2: Look up tenant ──────────────────────────────────────────────

    section("Tenant lookup")
    resp = api("GET", args.host, f"/api/tenants/?name={tenant}", verify, headers=headers)
    if resp.status_code != 200 or len(resp.json()) == 0:
        print(f"  {FAIL} Tenant '{tenant}' not found (status={resp.status_code})")
        return 1
    tenant_id = resp.json()[0]["id"]
    log(f"{PASS} Tenant '{tenant}' found (id: {tenant_id})")

    # ── Step 3: Role (check + create) ───────────────────────────────────────

    section("Role: check + create")
    resp = api("GET", args.host, f"/api/roles/?name={role_name}", verify, headers=headers)
    if resp.status_code != 200:
        log(f"{FAIL} Role check failed: {resp.status_code}")
        errors.append("role check")
    elif len(resp.json()) > 0:
        role_id = resp.json()[0]["id"]
        log(f"{SKIP} Role '{role_name}' already exists (id: {role_id})")
    else:
        resp = api("POST", args.host, "/api/roles/", verify, headers=headers, json={
            "name": role_name,
            "tenant_id": int(tenant_id),
            "permissions_list": [
                "create_logical", "view_logical", "edit_logical", "delete_logical",
            ],
        })
        if resp.status_code not in (200, 201):
            log(f"{FAIL} Role creation failed: {resp.status_code} — {resp.text[:200]}")
            errors.append("role create")
        else:
            role_id = resp.json()["id"]
            created["role"] = role_id
            log(f"{PASS} Role '{role_name}' created (id: {role_id})")

    if "role_id" not in dir():
        log(f"{FAIL} Cannot continue without role ID")
        return 1

    # ── Step 4: Manager (check + create) ────────────────────────────────────

    section("Manager: check + create")
    rand_pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(30)) + "!@"

    resp = api("GET", args.host, f"/api/managers/?username={manager_name}", verify, headers=headers)
    if resp.status_code != 200:
        log(f"{FAIL} Manager check failed: {resp.status_code}")
        errors.append("manager check")
    elif len(resp.json()) > 0:
        manager_id = resp.json()[0]["id"]
        log(f"{SKIP} Manager '{manager_name}' already exists (id: {manager_id})")
        rand_pw = None
    else:
        resp = api("POST", args.host, "/api/managers/", verify, headers=headers, json={
            "username": manager_name,
            "password": rand_pw,
            "user_type": "TENANT_ADMIN",
            "tenant_id": int(tenant_id),
            "roles": [int(role_id)],
            "password_expiration_disabled": True,
            "is_temporary_password": False,
        })
        if resp.status_code not in (200, 201):
            log(f"{FAIL} Manager creation failed: {resp.status_code} — {resp.text[:200]}")
            errors.append("manager create")
        else:
            manager_id = resp.json()["id"]
            created["manager"] = manager_id
            log(f"{PASS} Manager '{manager_name}' created (id: {manager_id})")

    # ── Step 5: Verify manager can authenticate via tenant-scoped JWT ───────

    section("Manager authentication (tenant-scoped JWT)")
    if rand_pw:
        resp = api("POST", args.host, f"/api/token/{tenant}", verify, json={
            "username": manager_name,
            "password": rand_pw,
        })
        if resp.status_code in (200, 201):
            mgr_jwt = resp.json()["access"]
            mgr_headers = {"Authorization": f"Bearer {mgr_jwt}"}
            log(f"{PASS} Authenticated as '{manager_name}' via /api/token/{tenant}")

            for endpoint, expect_ok in [
                ("/api/views/", True),
                ("/api/vippools/", True),
                ("/api/clusters/", False),
            ]:
                resp = api("GET", args.host, endpoint, verify, headers=mgr_headers)
                if expect_ok and resp.status_code == 200:
                    log(f"{PASS} JWT -> {endpoint}: {resp.status_code}")
                elif not expect_ok and resp.status_code == 403:
                    log(f"{PASS} JWT -> {endpoint}: {resp.status_code} (denied as expected)")
                else:
                    log(f"{FAIL} JWT -> {endpoint}: {resp.status_code}")
                    errors.append(f"jwt access {endpoint}")
        else:
            log(f"{FAIL} Manager JWT auth failed: {resp.status_code} — {resp.text[:200]}")
            errors.append("manager auth")
    else:
        log(f"{SKIP} No password available (manager pre-existed)")

    # ── Cleanup ─────────────────────────────────────────────────────────────

    if not args.no_cleanup:
        section("Cleanup")
        if created["manager"]:
            resp = api("DELETE", args.host, f"/api/managers/{created['manager']}/", verify, headers=headers)
            log(f"Manager: {'deleted' if resp.status_code in (200, 204) else f'failed ({resp.status_code})'}")
        if created["role"]:
            resp = api("DELETE", args.host, f"/api/roles/{created['role']}/", verify, headers=headers)
            log(f"Role:    {'deleted' if resp.status_code in (200, 204) else f'failed ({resp.status_code})'}")
        if not any(created.values()):
            log("Nothing to clean up (all resources pre-existed)")
    else:
        section("Cleanup skipped (--no-cleanup)")
        for kind, rid in created.items():
            if rid:
                log(f"  Left in place: {kind} (id: {rid})")

    # ── Summary ─────────────────────────────────────────────────────────────

    print(f"\n━━━ {'ALL TESTS PASSED' if not errors else f'{len(errors)} FAILURE(S)'} ━━━")
    if errors:
        for e in errors:
            print(f"  FAILED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
