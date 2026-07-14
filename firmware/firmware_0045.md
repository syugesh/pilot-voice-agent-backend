# Firmware Release Notes: Sagemcom F@st 5566 Gateway - v3.7.15

**Document ID:** FW-0045
**Category:** Firmware
**Device:** Sagemcom F@st 5566 Gateway
**Applicable Technology:** GPON
**Release Version:** v3.7.15
**Release Date:** 2025-01-10
**Distributed By:** Verizon Network Engineering

## Summary
Release notes and deployment guidance for firmware version v3.7.15 on the Sagemcom F@st 5566 Gateway, distributed to Verizon GPON subscribers via automated TR-069 (CWMP) provisioning.

## What's New
- Improved stability for PPPoE session re-authentication after brief WAN outages
- Security patch addressing an outdated TLS library used for the TR-069 management channel
- Performance improvement to VoIP jitter buffer management for improved call quality
- Bug fix for a rare condition causing the device to fail to renew its DHCP lease after a 72+ hour uptime period

## Known Issues
- A small number of devices may experience a single reboot approximately 24 hours after update as background optimization completes.

## Deployment Plan
This firmware is being deployed via staged rollout:
1. **Canary Phase:** 1% of eligible Sagemcom F@st 5566 Gateway devices in the Verizon fleet, monitored for 48 hours for anomaly rates in reboot frequency and support ticket volume.
2. **Regional Phase:** 25% rollout by region, monitored for 72 hours.
3. **Full Fleet Rollout:** Remaining devices updated over a 2-week window during low-traffic maintenance windows (typically 2 AM - 5 AM local time).

## Agent Guidance for Support Calls
- If a customer reports issues shortly after a firmware update, check the device's current firmware version against this release and compare against the Known Issues section above.
- Do not manually force a firmware rollback without Tier 2 approval, as this can create version mismatch issues with backend provisioning profiles.
- If a customer's device failed to update and remains on a version 2+ releases behind current, refer to the Firmware Rollback/Update SOP for manual push procedures.

## Rollback Procedure
In the event of a critical issue, Tier 2/NOC can trigger a rollback to the previous stable version via the TR-069 ACS console by selecting the device's serial number and issuing a Download RPC pointing to the previous firmware image URL.

## Related Documents
- Product Manual: MAN-0046
- SOP: SOP-0046
- Error Code Reference: ERR-0046

## Tags
firmware, sagemcom f@st 5566 gateway, gpon, verizon, release-notes
