# Firmware Release Notes: Ubiquiti Dream Machine - v3.2.10

**Document ID:** FW-0010
**Category:** Firmware
**Device:** Ubiquiti Dream Machine
**Applicable Technology:** DSL
**Release Version:** v3.2.10
**Release Date:** 2025-02-11
**Distributed By:** T-Mobile Network Engineering

## Summary
Release notes and deployment guidance for firmware version v3.2.10 on the Ubiquiti Dream Machine, distributed to T-Mobile DSL subscribers via automated TR-069 (CWMP) provisioning.

## What's New
- Improved stability for IPv6 prefix delegation handling during DHCPv6 lease renewal
- Security patch addressing a remote authentication bypass vulnerability in the admin web UI (CVE-class issue)
- Performance improvement to NAT session table handling under high concurrent connection load
- Bug fix for a rare condition causing the device to intermittently drop 5GHz WiFi clients

## Known Issues
- Devices with heavily customized port forwarding rules may need to re-save those rules after the update.

## Deployment Plan
This firmware is being deployed via staged rollout:
1. **Canary Phase:** 1% of eligible Ubiquiti Dream Machine devices in the T-Mobile fleet, monitored for 48 hours for anomaly rates in reboot frequency and support ticket volume.
2. **Regional Phase:** 25% rollout by region, monitored for 72 hours.
3. **Full Fleet Rollout:** Remaining devices updated over a 2-week window during low-traffic maintenance windows (typically 2 AM - 5 AM local time).

## Agent Guidance for Support Calls
- If a customer reports issues shortly after a firmware update, check the device's current firmware version against this release and compare against the Known Issues section above.
- Do not manually force a firmware rollback without Tier 2 approval, as this can create version mismatch issues with backend provisioning profiles.
- If a customer's device failed to update and remains on a version 2+ releases behind current, refer to the Firmware Rollback/Update SOP for manual push procedures.

## Rollback Procedure
In the event of a critical issue, Tier 2/NOC can trigger a rollback to the previous stable version via the TR-069 ACS console by selecting the device's serial number and issuing a Download RPC pointing to the previous firmware image URL.

## Related Documents
- Product Manual: MAN-0011
- SOP: SOP-0011
- Error Code Reference: ERR-0011

## Tags
firmware, ubiquiti dream machine, dsl, t-mobile, release-notes
