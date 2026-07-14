# Firmware Release Notes: ASUS RT-AX88U Router - v7.6.8

**Document ID:** FW-0044
**Category:** Firmware
**Device:** ASUS RT-AX88U Router
**Applicable Technology:** 5G Home Internet
**Release Version:** v7.6.8
**Release Date:** 2025-09-18
**Distributed By:** Comcast Network Engineering

## Summary
Release notes and deployment guidance for firmware version v7.6.8 on the ASUS RT-AX88U Router, distributed to Comcast 5G Home Internet subscribers via automated TR-069 (CWMP) provisioning.

## What's New
- Improved stability for WiFi 6 (802.11ax) channel selection under high-interference conditions
- Security patch addressing a remote authentication bypass vulnerability in the admin web UI (CVE-class issue)
- Performance improvement to NAT session table handling under high concurrent connection load
- Bug fix for a rare condition causing the device to intermittently drop 5GHz WiFi clients

## Known Issues
- No known issues reported at time of release.

## Deployment Plan
This firmware is being deployed via staged rollout:
1. **Canary Phase:** 1% of eligible ASUS RT-AX88U Router devices in the Comcast fleet, monitored for 48 hours for anomaly rates in reboot frequency and support ticket volume.
2. **Regional Phase:** 25% rollout by region, monitored for 72 hours.
3. **Full Fleet Rollout:** Remaining devices updated over a 2-week window during low-traffic maintenance windows (typically 2 AM - 5 AM local time).

## Agent Guidance for Support Calls
- If a customer reports issues shortly after a firmware update, check the device's current firmware version against this release and compare against the Known Issues section above.
- Do not manually force a firmware rollback without Tier 2 approval, as this can create version mismatch issues with backend provisioning profiles.
- If a customer's device failed to update and remains on a version 2+ releases behind current, refer to the Firmware Rollback/Update SOP for manual push procedures.

## Rollback Procedure
In the event of a critical issue, Tier 2/NOC can trigger a rollback to the previous stable version via the TR-069 ACS console by selecting the device's serial number and issuing a Download RPC pointing to the previous firmware image URL.

## Related Documents
- Product Manual: MAN-0045
- SOP: SOP-0045
- Error Code Reference: ERR-0045

## Tags
firmware, asus rt-ax88u router, 5g home internet, comcast, release-notes
