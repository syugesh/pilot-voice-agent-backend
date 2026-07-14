# Error Code Reference: LAN_LOOP_DETECT

**Document ID:** ERR-0016
**Category:** Error Code
**Applicable Device:** WiFi 6 Router
**Applicable Technology:** DSL
**Severity:** Major

## Summary
Reference entry describing the LAN_LOOP_DETECT error, its trigger conditions, and the standard resolution workflow for WiFi 6 Router units operating on DSL.

## Error Description
Ethernet loop detected on LAN switch ports. This error typically surfaces in the device's system log and, on managed devices, is also reported to the ISP's TR-069/ACS management platform or SNMP trap receiver.

## When This Occurs
- During initial WAN connection setup after a factory reset or configuration change.
- Following a firmware update that alters WAN authentication or line negotiation parameters.
- After prolonged network congestion or a backend provisioning system outage.
- When physical layer conditions (attenuation, optical power, RF levels) fall outside acceptable thresholds.

## Symptoms
- WiFi 6 Router status LED reflects an error/alarm state (commonly solid red or fast-blinking amber).
- Customer reports total loss of service or intermittent connectivity correlating with repeated occurrences of this error in the log.
- Remote diagnostics from the ISP portal show the error code logged with an associated timestamp.

## Root Cause
This error is most commonly caused by a mismatch or failure at the network authentication/provisioning layer, requiring backend session reset.

## Resolution Steps
1. Capture the full error log entry, including timestamp and any accompanying error codes, from the device or ACS platform.
2. Cross-reference the account provisioning status in the OSS/CRM to rule out a backend service configuration issue.
3. Power cycle the WiFi 6 Router and monitor for the error recurring within the first 10 minutes after reboot.
4. If the error persists, check physical layer statistics relevant to DSL (sync rate, optical power, RF levels) against normal operating thresholds.
5. If physical layer statistics are out of spec, dispatch a field technician; otherwise escalate to Tier 2 Network Operations for backend session/provisioning reset.
6. Document resolution in the case notes and confirm with the customer that service has been restored before closing.

## Escalation Rules
If LAN_LOOP_DETECT recurs 3 or more times within a 24-hour period for the same device, escalate to Tier 2 Network Operations immediately, referencing this document ID. If the device is under warranty and hardware fault is confirmed, initiate an RMA per the Equipment Replacement SOP.

## Related Documents
- Troubleshooting Guide: TS-0017
- Product Manual: MAN-0017
- Firmware Notes: FW-0017

## Tags
error-code, lan_loop_detect, dsl, wifi 6 router, diagnostics
