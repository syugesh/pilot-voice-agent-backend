# Firmware Release Notes: Huawei HG8245H ONT - v3.2.0

**Document ID:** FW-0030
**Category:** Firmware
**Device:** Huawei HG8245H ONT
**Applicable Technology:** Cable/DOCSIS
**Release Version:** v3.2.0
**Release Date:** 2025-04-13
**Distributed By:** BT Openreach Network Engineering

## Summary
Release notes and deployment guidance for firmware version v3.2.0 on the Huawei HG8245H ONT, distributed to BT Openreach Cable/DOCSIS subscribers via automated TR-069 (CWMP) provisioning.

## What's New
- Improved stability for PPPoE session re-authentication after brief WAN outages
- Security patch addressing a remote authentication bypass vulnerability in the admin web UI (CVE-class issue)
- Performance improvement to NAT session table handling under high concurrent connection load
- Bug fix for a rare condition causing the device to intermittently drop 5GHz WiFi clients

## Known Issues
- A small number of devices may experience a single reboot approximately 24 hours after update as background optimization completes.

## Deployment Plan
This firmware is being deployed via staged rollout:
1. **Canary Phase:** 1% of eligible Huawei HG8245H ONT devices in the BT Openreach fleet, monitored for 48 hours for anomaly rates in reboot frequency and support ticket volume.
2. **Regional Phase:** 25% rollout by region, monitored for 72 hours.
3. **Full Fleet Rollout:** Remaining devices updated over a 2-week window during low-traffic maintenance windows (typically 2 AM - 5 AM local time).

## Agent Guidance for Support Calls
- If a customer reports issues shortly after a firmware update, check the device's current firmware version against this release and compare against the Known Issues section above.
- Do not manually force a firmware rollback without Tier 2 approval, as this can create version mismatch issues with backend provisioning profiles.
- If a customer's device failed to update and remains on a version 2+ releases behind current, refer to the Firmware Rollback/Update SOP for manual push procedures.

## Rollback Procedure
In the event of a critical issue, Tier 2/NOC can trigger a rollback to the previous stable version via the TR-069 ACS console by selecting the device's serial number and issuing a Download RPC pointing to the previous firmware image URL.

## Related Documents
- Product Manual: MAN-0031
- SOP: SOP-0031
- Error Code Reference: ERR-0031

## Tags
firmware, huawei hg8245h ont, cable/docsis, bt openreach, release-notes
