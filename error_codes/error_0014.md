# Error Code Reference: QOS_POLICY_ERR

**Document ID:** ERR-0014
**Category:** Error Code
**Applicable Device:** DSL Modem
**Applicable Technology:** 5G Home Internet
**Severity:** Minor

## Summary
Reference entry describing the QOS_POLICY_ERR error, its trigger conditions, and the standard resolution workflow for DSL Modem units operating on 5G Home Internet.

## Error Description
QoS policy push from ACS rejected by CPE. This error typically surfaces in the device's system log and, on managed devices, is also reported to the ISP's TR-069/ACS management platform or SNMP trap receiver.

## When This Occurs
- During initial WAN connection setup after a factory reset or configuration change.
- Following a firmware update that alters WAN authentication or line negotiation parameters.
- After prolonged network congestion or a backend provisioning system outage.
- When physical layer conditions (attenuation, optical power, RF levels) fall outside acceptable thresholds.

## Symptoms
- DSL Modem status LED reflects an error/alarm state (commonly solid red or fast-blinking amber).
- Customer reports total loss of service or intermittent connectivity correlating with repeated occurrences of this error in the log.
- Remote diagnostics from the ISP portal show the error code logged with an associated timestamp.

## Root Cause
This error is most commonly caused by a mismatch or failure at the network authentication/provisioning layer, requiring backend session reset.

## Resolution Steps
1. Capture the full error log entry, including timestamp and any accompanying error codes, from the device or ACS platform.
2. Cross-reference the account provisioning status in the OSS/CRM to rule out a backend service configuration issue.
3. Power cycle the DSL Modem and monitor for the error recurring within the first 10 minutes after reboot.
4. If the error persists, check physical layer statistics relevant to 5G Home Internet (sync rate, optical power, RF levels) against normal operating thresholds.
5. If physical layer statistics are out of spec, dispatch a field technician; otherwise escalate to Tier 2 Network Operations for backend session/provisioning reset.
6. Document resolution in the case notes and confirm with the customer that service has been restored before closing.

## Escalation Rules
If QOS_POLICY_ERR recurs 3 or more times within a 24-hour period for the same device, escalate to Tier 2 Network Operations immediately, referencing this document ID. If the device is under warranty and hardware fault is confirmed, initiate an RMA per the Equipment Replacement SOP.

## Related Documents
- Troubleshooting Guide: TS-0015
- Product Manual: MAN-0015
- Firmware Notes: FW-0015

## Tags
error-code, qos_policy_err, 5g home internet, dsl modem, diagnostics
