# Firmware Release Notes: Motorola MB8611 Cable Modem - v5.9.29

**Document ID:** FW-0047
**Category:** Firmware
**Device:** Motorola MB8611 Cable Modem
**Applicable Technology:** Fiber
**Release Version:** v5.9.29
**Release Date:** 2025-03-12
**Distributed By:** Airtel Network Engineering

## Summary
Release notes and deployment guidance for firmware version v5.9.29 on the Motorola MB8611 Cable Modem, distributed to Airtel Fiber subscribers via automated TR-069 (CWMP) provisioning.

## What's New
- Improved stability for WiFi 6 (802.11ax) channel selection under high-interference conditions
- Security patch addressing an outdated TLS library used for the TR-069 management channel
- Performance improvement to VoIP jitter buffer management for improved call quality
- Bug fix for a rare condition causing the device to fail to renew its DHCP lease after a 72+ hour uptime period

## Known Issues
- No known issues reported at time of release.

## Deployment Plan
This firmware is being deployed via staged rollout:
1. **Canary Phase:** 1% of eligible Motorola MB8611 Cable Modem devices in the Airtel fleet, monitored for 48 hours for anomaly rates in reboot frequency and support ticket volume.
2. **Regional Phase:** 25% rollout by region, monitored for 72 hours.
3. **Full Fleet Rollout:** Remaining devices updated over a 2-week window during low-traffic maintenance windows (typically 2 AM - 5 AM local time).

## Agent Guidance for Support Calls
- If a customer reports issues shortly after a firmware update, check the device's current firmware version against this release and compare against the Known Issues section above.
- Do not manually force a firmware rollback without Tier 2 approval, as this can create version mismatch issues with backend provisioning profiles.
- If a customer's device failed to update and remains on a version 2+ releases behind current, refer to the Firmware Rollback/Update SOP for manual push procedures.

## Rollback Procedure
In the event of a critical issue, Tier 2/NOC can trigger a rollback to the previous stable version via the TR-069 ACS console by selecting the device's serial number and issuing a Download RPC pointing to the previous firmware image URL.

## Related Documents
- Product Manual: MAN-0048
- SOP: SOP-0048
- Error Code Reference: ERR-0048

## Tags
firmware, motorola mb8611 cable modem, fiber, airtel, release-notes
