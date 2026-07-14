# Error Code Reference: CONFIG_CORRUPT

**Document ID:** ERR-0031
**Category:** Error Code
**Applicable Device:** Cable Modem
**Applicable Technology:** Fixed Wireless
**Severity:** Major

## Summary
Reference entry describing the CONFIG_CORRUPT error, its trigger conditions, and the standard resolution workflow for Cable Modem units operating on Fixed Wireless.

## Error Description
Persistent configuration store corrupted. This error typically surfaces in the device's system log and, on managed devices, is also reported to the ISP's TR-069/ACS management platform or SNMP trap receiver.

## When This Occurs
- During initial WAN connection setup after a factory reset or configuration change.
- Following a firmware update that alters WAN authentication or line negotiation parameters.
- After prolonged network congestion or a backend provisioning system outage.
- When physical layer conditions (attenuation, optical power, RF levels) fall outside acceptable thresholds.

## Symptoms
- Cable Modem status LED reflects an error/alarm state (commonly solid red or fast-blinking amber).
- Customer reports total loss of service or intermittent connectivity correlating with repeated occurrences of this error in the log.
- Remote diagnostics from the ISP portal show the error code logged with an associated timestamp.

## Root Cause
This error is most commonly caused by a physical layer fault such as line degradation, damaged cabling, or hardware failure requiring on-site inspection.

## Resolution Steps
1. Capture the full error log entry, including timestamp and any accompanying error codes, from the device or ACS platform.
2. Cross-reference the account provisioning status in the OSS/CRM to rule out a backend service configuration issue.
3. Power cycle the Cable Modem and monitor for the error recurring within the first 10 minutes after reboot.
4. If the error persists, check physical layer statistics relevant to Fixed Wireless (sync rate, optical power, RF levels) against normal operating thresholds.
5. If physical layer statistics are out of spec, dispatch a field technician; otherwise escalate to Tier 2 Network Operations for backend session/provisioning reset.
6. Document resolution in the case notes and confirm with the customer that service has been restored before closing.

## Escalation Rules
If CONFIG_CORRUPT recurs 3 or more times within a 24-hour period for the same device, escalate to Tier 2 Network Operations immediately, referencing this document ID. If the device is under warranty and hardware fault is confirmed, initiate an RMA per the Equipment Replacement SOP.

## Related Documents
- Troubleshooting Guide: TS-0032
- Product Manual: MAN-0032
- Firmware Notes: FW-0032

## Tags
error-code, config_corrupt, fixed wireless, cable modem, diagnostics
